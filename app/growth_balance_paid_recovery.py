from __future__ import annotations

from datetime import UTC, datetime
from types import MethodType
from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, CustomerProjectNotFoundError
from app.growth_balance import (
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
    growth_balance_service,
)


def install_paid_checkout_project_recovery(
    service: GrowthBalanceService = growth_balance_service,
) -> GrowthBalanceService:
    """Repair project state when the paid top-up ledger survived a process crash."""

    if getattr(service, "_paid_checkout_project_recovery_installed", False):
        return service

    original_credit = service.credit_paid_checkout

    def credit(
        instance: GrowthBalanceService,
        project_id: UUID,
        *,
        session_id: str,
        amount_cents: int,
        currency: str,
        stripe_customer_id: str | None = None,
    ) -> bool:
        credited = original_credit(
            project_id,
            session_id=session_id,
            amount_cents=amount_cents,
            currency=currency,
            stripe_customer_id=stripe_customer_id,
        )
        if not credited:
            return False

        record = instance._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id)
        if record is None or record.get("state") != "PAID":
            return True
        project = instance._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CustomerProjectNotFoundError(project_id)

        changed = False
        if stripe_customer_id and not project.get("stripe_customer_id"):
            project["stripe_customer_id"] = stripe_customer_id
            changed = True

        paid_at = str(record.get("paid_at") or "").strip()
        current_funded_at = str(project.get("growth_balance_last_funded_at") or "").strip()
        if paid_at and (
            not current_funded_at or _timestamp_is_newer(paid_at, current_funded_at)
        ):
            project["growth_balance_last_funded_at"] = paid_at
            changed = True

        if not project.get("launch_unlocked"):
            project["launch_unlocked"] = True
            project["launch_entitlement_source"] = "GROWTH_BALANCE"
            project["launch_unlocked_at"] = paid_at or datetime.now(UTC).isoformat()
            if project.get("status") in {"PREVIEW", "CHECKOUT_PENDING"}:
                project["status"] = "UNLOCKED"
            changed = True

        if changed:
            instance._persist_project(project)
        return True

    service.credit_paid_checkout = MethodType(credit, service)
    service._paid_checkout_project_recovery_installed = True
    return service


def _timestamp_is_newer(candidate: str, current: str) -> bool:
    try:
        candidate_dt = datetime.fromisoformat(candidate)
        current_dt = datetime.fromisoformat(current)
    except ValueError:
        return candidate > current
    if candidate_dt.tzinfo is None:
        candidate_dt = candidate_dt.replace(tzinfo=UTC)
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=UTC)
    return candidate_dt > current_dt
