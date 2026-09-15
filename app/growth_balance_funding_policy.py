from __future__ import annotations

from datetime import UTC, datetime
from types import MethodType
from uuid import UUID

import stripe

from app.growth_balance import (
    ADVERTISING_MERCHANT_CATEGORY,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GrowthBalanceService,
    GrowthBalanceSettlementService,
    growth_balance_service,
)
from app.growth_balance_rail_safety import require_growth_balance_reactivation_allowed
from app.stripe_objects import stripe_field

_CHECKOUT_ONLY_LOCK_TOKEN = "checkout_only_no_provider_liquidity"


class CheckoutFirstGrowthBalanceSettlementService(GrowthBalanceSettlementService):
    """Decouple customer funding from the downstream provider-spend rail.

    Stripe Checkout can accept and credit Growth Balance while Stripe Issuing is
    deliberately deferred. Paid acquisition still uses the inherited `readiness()`
    contract, so `settlement_ready` remains false until the real spend rail is live.
    """

    def funding_readiness(
        self,
        project_id: UUID,
        *,
        required_liquidity_cents: int,
    ) -> tuple[bool, str]:
        settings = self._settings()
        if settings.growth_balance_settlement_provider == "stripe_issuing":
            return super().funding_readiness(
                project_id,
                required_liquidity_cents=required_liquidity_cents,
            )
        if settings.stripe_secret_key is None:
            return False, "STRIPE_CHECKOUT_NOT_CONFIGURED"
        return True, "STRIPE_CHECKOUT_READY_SPEND_RAIL_DEFERRED"

    def requires_provider_liquidity_lock(self) -> bool:
        """Return whether funding must reserve Partizan-owned provider liquidity."""

        return self._settings().growth_balance_settlement_provider == "stripe_issuing"

    def provision_or_update(self, project_id: UUID, acquisition_capacity_cents: int) -> dict:
        if self._settings().growth_balance_settlement_provider != "stripe_issuing":
            return {
                "project_id": str(project_id),
                "provider": "checkout_only",
                "settlement_ready": False,
                "settlement_status": "SPEND_RAIL_DEFERRED",
                "acquisition_limit_cents": int(acquisition_capacity_cents),
            }
        if acquisition_capacity_cents <= 0:
            raise ValueError("Growth Balance acquisition capacity must be positive")

        settings = self._require_configured()
        controls = self._spending_controls(acquisition_capacity_cents)
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        bound = bool(rail and rail.get("binding_status") == "BOUND")
        paused = bool(rail and rail.get("paused_reason"))
        target_status = "active" if bound and not paused else "inactive"
        try:
            if rail and rail.get("card_id"):
                card = self._modify_card(
                    str(rail["card_id"]),
                    status=target_status,
                    spending_controls=controls,
                    metadata={
                        "partizan_project_id": str(project_id),
                        "partizan_purpose": "growth_balance_acquisition",
                    },
                )
            else:
                card = self._create_card(
                    cardholder=str(settings.stripe_issuing_cardholder_id),
                    currency=settings.stripe_issuing_currency,
                    type="virtual",
                    status="inactive",
                    spending_controls=controls,
                    metadata={
                        "partizan_project_id": str(project_id),
                        "partizan_purpose": "growth_balance_acquisition",
                    },
                    idempotency_key=f"partizan-issuing-card-{project_id}",
                )
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card provisioning failed") from exc

        payload = dict(rail or {})
        payload.update(
            {
                "project_id": str(project_id),
                "provider": "stripe_issuing",
                "card_id": str(stripe_field(card, "id") or payload.get("card_id") or ""),
                "card_last4": str(
                    stripe_field(card, "last4") or payload.get("card_last4") or ""
                ),
                "card_status": str(stripe_field(card, "status", target_status)),
                "currency": settings.stripe_issuing_currency,
                "allowed_categories": [ADVERTISING_MERCHANT_CATEGORY],
                "acquisition_limit_cents": int(acquisition_capacity_cents),
                "binding_status": payload.get("binding_status") or "UNBOUND",
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        payload.setdefault("created_at", datetime.now(UTC).isoformat())
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), payload)
        return payload

    def confirm_meta_binding(self, project_id: UUID, ad_account_id: str) -> dict:
        if self._settings().growth_balance_settlement_provider != "stripe_issuing":
            return super().confirm_meta_binding(project_id, ad_account_id)
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None or not rail.get("card_id"):
            raise ValueError("Partizan-funded Stripe Issuing card is not provisioned")
        if int(rail.get("acquisition_limit_cents") or 0) <= 0:
            raise ValueError("Fund the Growth Balance before binding provider billing")
        self._require_configured()
        target_status = "inactive" if rail.get("paused_reason") else "active"
        try:
            card = self._modify_card(
                str(rail["card_id"]),
                status=target_status,
                spending_controls=self._spending_controls(
                    int(rail["acquisition_limit_cents"])
                ),
                metadata={
                    "partizan_project_id": str(project_id),
                    "partizan_purpose": "growth_balance_acquisition",
                    "partizan_meta_ad_account_id": ad_account_id,
                },
            )
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card activation failed") from exc
        rail["binding_status"] = "BOUND"
        rail["bound_provider"] = "meta"
        rail["bound_provider_account_id"] = ad_account_id
        rail["bound_at"] = datetime.now(UTC).isoformat()
        rail["card_status"] = str(stripe_field(card, "status", target_status))
        rail["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)
        return rail

    def pause(self, project_id: UUID, reason: str) -> None:
        if self._settings().growth_balance_settlement_provider != "stripe_issuing":
            return super().pause(project_id, reason)
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None or not rail.get("card_id"):
            return
        self._require_configured()
        try:
            card = self._modify_card(str(rail["card_id"]), status="inactive")
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card pause failed") from exc
        rail["card_status"] = str(stripe_field(card, "status", "inactive"))
        rail["paused_reason"] = reason
        rail["paused_at"] = datetime.now(UTC).isoformat()
        rail["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)

    def activate(self, project_id: UUID) -> None:
        if self._settings().growth_balance_settlement_provider != "stripe_issuing":
            return super().activate(project_id)
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None or rail.get("binding_status") != "BOUND" or not rail.get("card_id"):
            raise ValueError("Partizan-funded card must be bound to Meta before activation")
        require_growth_balance_reactivation_allowed(project_id, rail.get("paused_reason"))
        self._require_configured()
        try:
            card = self._modify_card(
                str(rail["card_id"]),
                status="active",
                spending_controls=self._spending_controls(
                    int(rail.get("acquisition_limit_cents") or 0)
                ),
            )
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card activation failed") from exc
        rail["card_status"] = str(stripe_field(card, "status", "active"))
        rail["paused_reason"] = None
        rail["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)


def _install_checkout_first_liquidity_policy(service: GrowthBalanceService) -> None:
    """Keep Issuing's global liquidity lock out of checkout-only customer funding.

    `GrowthBalanceService.prepare_checkout()` predates checkout-first funding and
    acquires a global `stripe_issuing_liquidity` lock before it asks the settlement
    policy whether provider liquidity is needed. Until the spend rail tracked by
    #160 is connected, ordinary Stripe Checkout must not depend on that lock.

    We keep the original lock path intact whenever Stripe Issuing is explicitly
    configured. The per-project Checkout reservation in `prepare_checkout()` is
    unchanged, so payment recovery and duplicate-session protection remain intact.
    """

    if getattr(service, "_checkout_first_liquidity_policy_installed", False):
        return

    original_acquire = service._acquire_liquidity_lock
    original_release = service._release_liquidity_lock

    def acquire(instance: GrowthBalanceService) -> str:
        settlement = instance._settlement
        if (
            isinstance(settlement, CheckoutFirstGrowthBalanceSettlementService)
            and not settlement.requires_provider_liquidity_lock()
        ):
            return _CHECKOUT_ONLY_LOCK_TOKEN
        return original_acquire()

    def release(instance: GrowthBalanceService, token: str) -> None:
        if token == _CHECKOUT_ONLY_LOCK_TOKEN:
            return
        original_release(token)

    service._acquire_liquidity_lock = MethodType(acquire, service)
    service._release_liquidity_lock = MethodType(release, service)
    service._checkout_first_liquidity_policy_installed = True


def enable_checkout_first_growth_balance_funding() -> GrowthBalanceService:
    """Wire the temporary MVP funding policy into the shared customer balance service."""

    current = growth_balance_service._settlement
    if not isinstance(current, CheckoutFirstGrowthBalanceSettlementService):
        growth_balance_service._settlement = CheckoutFirstGrowthBalanceSettlementService(
            growth_balance_service._store
        )
    _install_checkout_first_liquidity_policy(growth_balance_service)
    return growth_balance_service
