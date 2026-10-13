from __future__ import annotations

from uuid import UUID

from app.autonomous_opportunity_refresh import AutonomousOpportunityRefreshService
from app.discovery_adjacent_expansion import (
    build_adjacent_discovery_hints,
    interleave_adjacent_hints,
)
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


class AdjacentAutonomousOpportunityRefreshService(AutonomousOpportunityRefreshService):
    """Use observed strong surfaces to reserve one adaptive adjacent-search hypothesis.

    The parent service still owns cadence, preflight, round count, four-hint selection,
    negative-learning flow and all execution boundaries. This subclass only enriches the
    hypothesis list; it does not create a second discovery loop.
    """

    def _adaptive_discovery_hints(
        self,
        *,
        product,
        icps,
        product_id: UUID,
        preflight_by_url: dict[str, TelegramOpportunityPreflightResult],
        round_number: int,
    ) -> list[str]:
        base_hints = super()._adaptive_discovery_hints(
            product=product,
            icps=icps,
            product_id=product_id,
            preflight_by_url=preflight_by_url,
            round_number=round_number,
        )
        adjacent_hints = self._adjacent_discovery_hints(
            product=product,
            icps=icps,
            product_id=product_id,
        )
        return self._dedupe_hints(
            interleave_adjacent_hints(base_hints, adjacent_hints)
        )

    def _adjacent_discovery_hints(
        self,
        *,
        product,
        icps,
        product_id: UUID,
    ) -> list[str]:
        get_distribution = getattr(self._audience_service, "get", None)
        if not callable(get_distribution):
            return []
        try:
            distribution = get_distribution(product_id)
        except (KeyError, TypeError, ValueError):
            return []
        opportunities = list(getattr(distribution, "opportunities", []) or [])
        if not opportunities:
            return []
        return build_adjacent_discovery_hints(
            product=product,
            icps=icps,
            opportunities=opportunities,
        )


adjacent_autonomous_opportunity_refresh_service = (
    AdjacentAutonomousOpportunityRefreshService()
)
