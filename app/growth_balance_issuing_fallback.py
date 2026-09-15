from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GrowthBalanceService,
    growth_balance_service,
)
from app.growth_balance_authorization_reservations import (
    GrowthBalanceAuthorizationReservationService,
    growth_balance_authorization_reservation_service,
)
from app.stripe_objects import stripe_field

_FALLBACK_REASONS = {"webhook_error", "webhook_timeout", "network_fallback"}
_FALLBACK_INCIDENT_PREFIX = "issuing_authorization_fallback:"


class GrowthBalanceIssuingFallbackService:
    """Fail-close the funded Issuing rail after Stripe makes a fallback decision.

    Stripe can make an Issuing decision without a usable response from Partizan. The
    authorization reservation layer remains responsible for the liability hold. This
    layer treats the fallback itself as an integration incident: first lock the local
    rail, then request a provider-side card pause, and keep a durable marker that no
    later healthy authorization event can clear automatically.
    """

    def __init__(
        self,
        *,
        reservation_service: GrowthBalanceAuthorizationReservationService = (
            growth_balance_authorization_reservation_service
        ),
        balance_service: GrowthBalanceService = growth_balance_service,
    ) -> None:
        self._reservations = reservation_service
        self._balance = balance_service
        self._store = balance_service._store

    def record_authorization(self, authorization: object) -> bool:
        # Reconcile the authorization/hold first. If the fallback was approved, its
        # pending liability is therefore durable before we perform a network pause.
        handled = self._reservations.record_authorization(authorization)
        fallback = self._fallback_detail(authorization)
        if fallback is None:
            return handled

        authorization_id = self._object_id(stripe_field(authorization, "id"))
        card_id = self._object_id(stripe_field(authorization, "card"))
        if not authorization_id or not card_id:
            return handled
        rail = self._rail_for_card(card_id)
        if rail is None:
            return handled
        project_id = UUID(str(rail["project_id"]))
        reason, reason_message = fallback
        incident_key = f"{_FALLBACK_INCIDENT_PREFIX}{authorization_id}"
        existing = self._store.get(GROWTH_BALANCE_LOCK_NAMESPACE, incident_key) or {}
        now = datetime.now(UTC).isoformat()
        incident = {
            "kind": "ISSUING_AUTHORIZATION_FALLBACK",
            "authorization_id": authorization_id,
            "project_id": str(project_id),
            "card_id": card_id,
            "approved": stripe_field(authorization, "approved") is True,
            "authorization_status": str(stripe_field(authorization, "status", "") or ""),
            "amount_cents": max(self._integer(stripe_field(authorization, "amount", 0)), 0),
            "currency": str(stripe_field(authorization, "currency", "") or "").lower(),
            "fallback_reason": reason,
            "reason_message": reason_message,
            "state": "RECONCILIATION_REQUIRED",
            "pause_confirmed_at": existing.get("pause_confirmed_at"),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        self._store.put(GROWTH_BALANCE_LOCK_NAMESPACE, incident_key, incident)

        # Close the local gate before the provider mutation. Even if Stripe's pause
        # call fails or times out, ordinary synchronous authorization requests now
        # see an inactive rail and are declined while the webhook retry reconciles.
        rail["card_status"] = "inactive"
        rail["issuing_fallback_reconciliation_required"] = True
        rail["issuing_fallback_authorization_id"] = authorization_id
        rail["issuing_fallback_reason"] = reason
        rail["issuing_fallback_detected_at"] = existing.get("created_at") or now
        rail["updated_at"] = now
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)

        if incident["pause_confirmed_at"]:
            return True

        pause_reason = f"ISSUING_FALLBACK_{authorization_id}_{reason}"
        self._balance.pause_rail(project_id, pause_reason)
        incident["pause_confirmed_at"] = datetime.now(UTC).isoformat()
        incident["updated_at"] = incident["pause_confirmed_at"]
        self._store.put(GROWTH_BALANCE_LOCK_NAMESPACE, incident_key, incident)
        return True

    def unresolved_for_project(self, project_id: UUID) -> bool:
        return any(
            item.get("kind") == "ISSUING_AUTHORIZATION_FALLBACK"
            and str(item.get("project_id") or "") == str(project_id)
            and item.get("state") == "RECONCILIATION_REQUIRED"
            for item in self._store.list_namespace(GROWTH_BALANCE_LOCK_NAMESPACE)
        )

    def _rail_for_card(self, card_id: str) -> dict | None:
        for rail in self._store.list_namespace(GROWTH_BALANCE_RAIL_NAMESPACE):
            if str(rail.get("card_id") or "") == card_id:
                return rail
        return None

    @classmethod
    def _fallback_detail(cls, authorization: object) -> tuple[str, str] | None:
        history = list(stripe_field(authorization, "request_history") or [])
        for request in reversed(history):
            reason = str(stripe_field(request, "reason", "") or "").lower()
            if reason in _FALLBACK_REASONS:
                return reason, str(stripe_field(request, "reason_message", "") or "")
        return None

    @staticmethod
    def _object_id(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        return str(stripe_field(value, "id", "") or "").strip()

    @staticmethod
    def _integer(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


growth_balance_issuing_fallback_service = GrowthBalanceIssuingFallbackService()
