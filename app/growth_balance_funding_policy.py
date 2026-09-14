from __future__ import annotations

from datetime import UTC, datetime
from types import MethodType
from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, CustomerProjectNotFoundError
from app.growth_balance import (
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GrowthBalanceService,
    GrowthBalanceSettlementService,
    growth_balance_service,
)

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
        if self._settings().growth_balance_settlement_provider == "stripe_issuing":
            return super().provision_or_update(project_id, acquisition_capacity_cents)
        return {
            "project_id": str(project_id),
            "provider": "checkout_only",
            "settlement_ready": False,
            "settlement_status": "SPEND_RAIL_DEFERRED",
            "acquisition_limit_cents": int(acquisition_capacity_cents),
        }


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


def _install_paid_checkout_project_recovery(service: GrowthBalanceService) -> None:
    """Repair project entitlement state when a paid top-up survived a process crash.

    `GrowthBalanceService.credit_paid_checkout()` durably marks the top-up PAID before
    persisting project-level entitlement/customer fields. A crash in that narrow gap
    leaves the balance funded but the project partially locked. Stripe retries are
    idempotent at the top-up ledger, so wrap the shared service and reconcile those
    project fields on every verified paid-session replay as well as on the first call.
    """

    if getattr(service, "_paid_checkout_project_recovery_installed", False):
        return

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


def enable_checkout_first_growth_balance_funding() -> GrowthBalanceService:
    """Wire the temporary MVP funding policy into the shared customer balance service."""

    current = growth_balance_service._settlement
    if not isinstance(current, CheckoutFirstGrowthBalanceSettlementService):
        growth_balance_service._settlement = CheckoutFirstGrowthBalanceSettlementService(
            growth_balance_service._store
        )
    _install_checkout_first_liquidity_policy(growth_balance_service)
    _install_paid_checkout_project_recovery(growth_balance_service)
    return growth_balance_service
