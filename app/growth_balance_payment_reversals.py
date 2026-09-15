from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import stripe

from app.config import Settings
from app.growth_balance import (
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
    growth_balance_service,
)
from app.stripe_objects import stripe_field

GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE = "customer_growth_balance_payment_reversals"
_ADJUSTMENT_PREFIX = "payment_reversal_adjustment:"
_DISPUTE_EVENT_TYPES = {
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.closed",
    "charge.dispute.funds_withdrawn",
    "charge.dispute.funds_reinstated",
}
_DISPUTE_EVENT_RANK = {
    "charge.dispute.created": 1,
    "charge.dispute.updated": 2,
    "charge.dispute.funds_withdrawn": 3,
    "charge.dispute.closed": 4,
    "charge.dispute.funds_reinstated": 5,
}


@dataclass(frozen=True)
class _TopUpBinding:
    project_id: UUID
    checkout_generation: int
    amount_cents: int
    currency: str
    session_id: str


class GrowthBalancePaymentReversalService:
    """Apply Stripe refund/dispute liabilities to funded Growth Balance.

    The original paid Checkout record stays immutable so payment replay continues to
    validate against the original amount. A single negative PAID adjustment per exact
    Checkout generation makes the existing Growth Balance funded calculation operate
    on net funds without changing the core ledger implementation.
    """

    def __init__(self, balance_service: GrowthBalanceService = growth_balance_service) -> None:
        self._balance = balance_service
        self._store = balance_service._store

    def handle_event(self, event: object, *, settings: Settings) -> bool:
        event_type = str(stripe_field(event, "type", ""))
        data = stripe_field(event, "data")
        obj = stripe_field(data, "object")
        if obj is None:
            return False
        event_id = str(stripe_field(event, "id", "") or "")
        event_created = self._integer(stripe_field(event, "created", 0))

        if event_type == "charge.refunded":
            return self.record_charge_refund(
                obj,
                event_id=event_id,
                event_created=event_created,
                settings=settings,
            )
        if event_type not in _DISPUTE_EVENT_TYPES:
            return False

        charge = self._charge_for_dispute(obj, settings=settings)
        return self.record_dispute(
            obj,
            event_type=event_type,
            charge=charge,
            event_id=event_id,
            event_created=event_created,
            settings=settings,
        )

    def record_charge_refund(
        self,
        charge: object,
        *,
        event_id: str,
        event_created: int,
        settings: Settings,
    ) -> bool:
        binding = self._binding_for_charge(charge)
        if binding is None:
            return False
        charge_id = self._object_id(charge)
        if not charge_id:
            raise ValueError("Growth Balance refund Charge id is missing")
        refunded_cents = self._integer(stripe_field(charge, "amount_refunded", 0))
        if refunded_cents <= 0:
            return False
        if refunded_cents > binding.amount_cents:
            raise ValueError("Growth Balance refund exceeds the original paid top-up")

        key = f"refund:{charge_id}"
        existing = self._store.get(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE, key) or {}
        recorded_cents = max(self._integer(existing.get("amount_cents")), refunded_cents)
        confirmed_cents = min(
            self._integer(existing.get("pause_confirmed_amount_cents")),
            recorded_cents,
        )
        payload = {
            "kind": "REFUND",
            "project_id": str(binding.project_id),
            "checkout_generation": binding.checkout_generation,
            "topup_session_id": binding.session_id,
            "charge_id": charge_id,
            "amount_cents": recorded_cents,
            "currency": binding.currency,
            "active": True,
            "event_id": event_id,
            "event_created": max(self._integer(existing.get("event_created")), event_created),
            "pause_confirmed_amount_cents": confirmed_cents,
            "created_at": existing.get("created_at") or datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._store.put(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE, key, payload)
        self._recompute_adjustment(binding)

        if recorded_cents > confirmed_cents:
            reason = f"STRIPE_GROWTH_BALANCE_REFUND_{charge_id}_{recorded_cents}"
            self._pause_and_sync_capacity(binding.project_id, reason=reason, settings=settings)
            payload["pause_confirmed_amount_cents"] = recorded_cents
            payload["pause_confirmed_at"] = datetime.now(UTC).isoformat()
            payload["updated_at"] = datetime.now(UTC).isoformat()
            self._store.put(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE, key, payload)
        else:
            self._sync_local_rail_capacity(binding.project_id, settings=settings)
        return True

    def record_dispute(
        self,
        dispute: object,
        *,
        event_type: str,
        charge: object,
        event_id: str,
        event_created: int,
        settings: Settings,
    ) -> bool:
        if event_type not in _DISPUTE_EVENT_TYPES:
            return False
        binding = self._binding_for_charge(charge)
        if binding is None:
            return False
        dispute_id = self._object_id(dispute)
        if not dispute_id:
            raise ValueError("Growth Balance dispute id is missing")
        dispute_charge_id = self._object_id(stripe_field(dispute, "charge"))
        charge_id = self._object_id(charge)
        if dispute_charge_id and dispute_charge_id != charge_id:
            raise ValueError("Growth Balance dispute Charge does not match retrieved Charge")
        amount_cents = self._integer(stripe_field(dispute, "amount", 0))
        currency = str(stripe_field(dispute, "currency", "") or "").lower()
        if amount_cents <= 0 or amount_cents > binding.amount_cents:
            raise ValueError("Growth Balance dispute amount is invalid")
        if currency != binding.currency:
            raise ValueError("Growth Balance dispute currency does not match paid top-up")

        key = f"dispute:{dispute_id}"
        existing = self._store.get(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE, key) or {}
        incoming_order = (event_created, _DISPUTE_EVENT_RANK[event_type])
        existing_order = (
            self._integer(existing.get("event_created")),
            self._integer(existing.get("event_rank")),
        )
        apply_incoming = not existing or incoming_order >= existing_order
        if apply_incoming:
            active = event_type != "charge.dispute.funds_reinstated"
            confirmed_cents = self._integer(existing.get("pause_confirmed_amount_cents"))
            if not active:
                confirmed_cents = 0
            elif not bool(existing.get("active")) or amount_cents > self._integer(
                existing.get("amount_cents")
            ):
                confirmed_cents = 0
            payload = {
                "kind": "DISPUTE",
                "project_id": str(binding.project_id),
                "checkout_generation": binding.checkout_generation,
                "topup_session_id": binding.session_id,
                "charge_id": charge_id,
                "dispute_id": dispute_id,
                "amount_cents": amount_cents,
                "currency": binding.currency,
                "active": active,
                "dispute_status": str(stripe_field(dispute, "status", "") or ""),
                "event_type": event_type,
                "event_id": event_id,
                "event_created": event_created,
                "event_rank": _DISPUTE_EVENT_RANK[event_type],
                "pause_confirmed_amount_cents": min(confirmed_cents, amount_cents),
                "created_at": existing.get("created_at") or datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._store.put(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE, key, payload)
        else:
            payload = existing

        self._recompute_adjustment(binding)
        active = bool(payload.get("active"))
        confirmed_cents = self._integer(payload.get("pause_confirmed_amount_cents"))
        liability_cents = self._integer(payload.get("amount_cents"))
        if active and liability_cents > confirmed_cents:
            reason = f"STRIPE_GROWTH_BALANCE_DISPUTE_{dispute_id}_{liability_cents}"
            self._pause_and_sync_capacity(binding.project_id, reason=reason, settings=settings)
            payload["pause_confirmed_amount_cents"] = liability_cents
            payload["pause_confirmed_at"] = datetime.now(UTC).isoformat()
            payload["updated_at"] = datetime.now(UTC).isoformat()
            self._store.put(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE, key, payload)
        else:
            # Funds reinstatement restores net capacity only in local accounting. The
            # card remains paused until a separate operator/customer-approved action
            # deliberately activates it again.
            self._sync_local_rail_capacity(binding.project_id, settings=settings)
        return True

    def _binding_for_charge(self, charge: object) -> _TopUpBinding | None:
        metadata = stripe_field(charge, "metadata")
        if stripe_field(metadata, "partizan_entitlement") != "growth_balance_topup":
            return None
        project_raw = str(stripe_field(metadata, "partizan_project_id", "") or "")
        generation = self._integer(stripe_field(metadata, "partizan_checkout_generation", 0))
        amount_cents = self._integer(stripe_field(metadata, "partizan_amount_cents", 0))
        currency = str(stripe_field(charge, "currency", "") or "").lower()
        try:
            project_id = UUID(project_raw)
        except ValueError as exc:
            raise ValueError("Growth Balance Charge project metadata is invalid") from exc
        if generation <= 0 or amount_cents <= 0 or currency != "usd":
            raise ValueError("Growth Balance Charge metadata is incomplete")
        if self._integer(stripe_field(charge, "amount", 0)) != amount_cents:
            raise ValueError("Growth Balance Charge amount does not match signed top-up metadata")

        matches = [
            item
            for item in self._store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)
            if item.get("state") == "PAID"
            and str(item.get("project_id") or "") == str(project_id)
            and self._integer(item.get("checkout_generation")) == generation
            and self._integer(item.get("amount_cents")) == amount_cents
            and str(item.get("currency") or "").lower() == currency
            and str(item.get("session_id") or "")
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Growth Balance reversal cannot bind to exactly one paid Checkout generation"
            )
        return _TopUpBinding(
            project_id=project_id,
            checkout_generation=generation,
            amount_cents=amount_cents,
            currency=currency,
            session_id=str(matches[0]["session_id"]),
        )

    def _recompute_adjustment(self, binding: _TopUpBinding) -> int:
        liabilities = [
            item
            for item in self._store.list_namespace(GROWTH_BALANCE_PAYMENT_REVERSAL_NAMESPACE)
            if str(item.get("project_id") or "") == str(binding.project_id)
            and self._integer(item.get("checkout_generation")) == binding.checkout_generation
        ]
        refund_cents = sum(
            self._integer(item.get("amount_cents"))
            for item in liabilities
            if item.get("kind") == "REFUND"
        )
        dispute_cents = sum(
            self._integer(item.get("amount_cents"))
            for item in liabilities
            if item.get("kind") == "DISPUTE" and bool(item.get("active"))
        )
        reversal_cents = min(binding.amount_cents, max(refund_cents + dispute_cents, 0))
        adjustment_key = self._adjustment_key(binding)
        if reversal_cents <= 0:
            self._store.delete(GROWTH_BALANCE_TOPUP_NAMESPACE, adjustment_key)
            return 0
        existing = self._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, adjustment_key) or {}
        now = datetime.now(UTC).isoformat()
        self._store.put(
            GROWTH_BALANCE_TOPUP_NAMESPACE,
            adjustment_key,
            {
                "kind": "PAYMENT_REVERSAL_ADJUSTMENT",
                "project_id": str(binding.project_id),
                "source_checkout_generation": binding.checkout_generation,
                "source_topup_session_id": binding.session_id,
                "amount_cents": -reversal_cents,
                "currency": binding.currency,
                "state": "PAID",
                "created_at": existing.get("created_at") or now,
                "updated_at": now,
            },
        )
        return reversal_cents

    def _pause_and_sync_capacity(
        self,
        project_id: UUID,
        *,
        reason: str,
        settings: Settings,
    ) -> None:
        # Provider pause happens before we mark the event confirmed. If this process
        # crashes after Stripe accepted the pause, retrying uses the same stable reason
        # and therefore the same existing pause idempotency key.
        self._balance.pause_rail(project_id, reason)
        self._sync_local_rail_capacity(project_id, settings=settings)

    def _sync_local_rail_capacity(self, project_id: UUID, *, settings: Settings) -> None:
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None:
            return
        funded_cents = max(self._balance._funded_cents(project_id), 0)
        capacity_cents = self._balance._max_acquisition_cents(
            funded_cents,
            int(settings.partizan_managed_spend_fee_pct),
        )
        rail["acquisition_limit_cents"] = capacity_cents
        rail["payment_reversal_effective_funded_cents"] = funded_cents
        rail["payment_reversal_adjusted_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)

    def _charge_for_dispute(self, dispute: object, *, settings: Settings):
        charge_ref = stripe_field(dispute, "charge")
        charge_id = self._object_id(charge_ref)
        if not charge_id:
            raise ValueError("Growth Balance dispute Charge id is missing")
        if not isinstance(charge_ref, str) and stripe_field(charge_ref, "metadata") is not None:
            return charge_ref
        if settings.stripe_secret_key is None:
            raise RuntimeError("Stripe is not configured for Growth Balance dispute reconciliation")
        stripe.api_key = settings.stripe_secret_key.get_secret_value()
        return stripe.Charge.retrieve(charge_id)

    @staticmethod
    def _adjustment_key(binding: _TopUpBinding) -> str:
        return (
            f"{_ADJUSTMENT_PREFIX}{binding.project_id}:"
            f"{binding.checkout_generation}"
        )

    @staticmethod
    def _integer(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _object_id(value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        return str(stripe_field(value, "id", "") or "").strip()


growth_balance_payment_reversal_service = GrowthBalancePaymentReversalService()


def enable_growth_balance_payment_reversal_webhooks() -> None:
    """Reconcile signed Stripe reversal events before the legacy webhook returns 2xx."""

    import app.customer_routes as customer_routes

    if getattr(customer_routes, "_growth_balance_payment_reversal_policy_installed", False):
        return
    original_construct = customer_routes.construct_stripe_event

    def construct_with_reversal_policy(
        *,
        settings: Settings,
        payload: bytes,
        signature: str,
    ):
        event = original_construct(settings=settings, payload=payload, signature=signature)
        growth_balance_payment_reversal_service.handle_event(event, settings=settings)
        return event

    customer_routes.construct_stripe_event = construct_with_reversal_policy
    customer_routes._growth_balance_payment_reversal_policy_installed = True
