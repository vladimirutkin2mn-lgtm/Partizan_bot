from __future__ import annotations

from uuid import UUID

from app.growth_balance import (
    GrowthBalanceService,
    GrowthBalanceSettlementService,
    growth_balance_service,
)


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


def enable_checkout_first_growth_balance_funding() -> GrowthBalanceService:
    """Wire the temporary MVP funding policy into the shared customer balance service."""

    current = growth_balance_service._settlement
    if isinstance(current, CheckoutFirstGrowthBalanceSettlementService):
        return growth_balance_service
    growth_balance_service._settlement = CheckoutFirstGrowthBalanceSettlementService(
        growth_balance_service._store
    )
    return growth_balance_service
