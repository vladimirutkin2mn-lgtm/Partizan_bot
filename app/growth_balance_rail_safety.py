from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GrowthBalanceService,
    growth_balance_service,
)

_NORMAL_REACTIVATION_REASONS = frozenset(
    {"CUSTOMER", "CHANNELS", "SETUP", "FUNDING", "MANDATE_PAUSED"}
)
_operator_reactivation_project: ContextVar[str | None] = ContextVar(
    "growth_balance_operator_reactivation_project",
    default=None,
)


def is_growth_balance_safety_pause(reason: object) -> bool:
    normalized = str(reason or "").strip()
    return bool(normalized) and normalized not in _NORMAL_REACTIVATION_REASONS


def require_growth_balance_reactivation_allowed(project_id: UUID, paused_reason: object) -> None:
    if not is_growth_balance_safety_pause(paused_reason):
        return
    if _operator_reactivation_project.get() != str(project_id):
        raise ValueError(
            "Growth Balance rail safety pause requires explicit operator reconciliation "
            "before reactivation"
        )


@contextmanager
def growth_balance_operator_reactivation_scope(project_id: UUID):
    token = _operator_reactivation_project.set(str(project_id))
    try:
        yield
    finally:
        _operator_reactivation_project.reset(token)


class GrowthBalanceRailSafetyService:
    def __init__(self, balance_service: GrowthBalanceService = growth_balance_service) -> None:
        self._balance = balance_service
        self._store = balance_service._store

    def reactivate_after_reconciliation(
        self,
        project_id: UUID,
        *,
        expected_pause_reason: str,
        confirm_reconciled: bool,
        confirm_reactivation: bool,
        reconciliation_note: str,
    ) -> dict:
        if not confirm_reconciled:
            raise ValueError("confirm_reconciled=true is required")
        if not confirm_reactivation:
            raise ValueError("confirm_reactivation=true is required")
        note = reconciliation_note.strip()
        if len(note) < 3:
            raise ValueError("A reconciliation note is required")

        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None or not rail.get("card_id"):
            raise ValueError("Growth Balance rail is not provisioned")
        if rail.get("binding_status") != "BOUND":
            raise ValueError("Growth Balance rail is not bound to provider billing")

        actual_reason = str(rail.get("paused_reason") or "").strip()
        if not actual_reason:
            raise ValueError("Growth Balance rail is not paused")
        if actual_reason != expected_pause_reason.strip():
            raise ValueError("Growth Balance rail pause reason changed; reload before reconciling")
        if not is_growth_balance_safety_pause(actual_reason):
            raise ValueError("Growth Balance rail pause does not require operator reconciliation")

        with growth_balance_operator_reactivation_scope(project_id):
            self._balance.activate_rail(project_id)

        now = datetime.now(UTC).isoformat()
        for item in self._store.list_namespace(GROWTH_BALANCE_LOCK_NAMESPACE):
            if item.get("kind") != "ISSUING_AUTHORIZATION_FALLBACK":
                continue
            if str(item.get("project_id") or "") != str(project_id):
                continue
            if str(item.get("pause_reason") or "") != actual_reason:
                continue
            if item.get("state") != "RECONCILIATION_REQUIRED":
                continue
            authorization_id = str(item.get("authorization_id") or "").strip()
            if not authorization_id:
                continue
            incident_key = f"issuing_authorization_fallback:{authorization_id}"
            item["state"] = "RESOLVED_BY_OPERATOR"
            item["resolved_at"] = now
            item["resolution_note"] = note[:1000]
            item["updated_at"] = now
            self._store.put(GROWTH_BALANCE_LOCK_NAMESPACE, incident_key, item)

        self._store.put(
            GROWTH_BALANCE_LOCK_NAMESPACE,
            f"rail_reactivation:{project_id}:{uuid4()}",
            {
                "kind": "GROWTH_BALANCE_RAIL_REACTIVATION",
                "project_id": str(project_id),
                "previous_pause_reason": actual_reason,
                "actor": "OPERATOR",
                "reconciliation_note": note[:1000],
                "reactivated_at": now,
            },
        )
        return self._balance.rail_view(project_id)


growth_balance_rail_safety_service = GrowthBalanceRailSafetyService()
