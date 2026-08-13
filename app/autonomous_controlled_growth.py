from __future__ import annotations

from uuid import UUID

from app.autonomous_growth import AutonomousGrowthDecisionView
from app.autonomous_growth_control import (
    AutonomousGrowthControlService,
    autonomous_growth_control_service,
)
from app.autonomous_owned_creative_growth import AutonomousOwnedCreativeGrowthSweepService
from app.autonomy_schemas import GrowthMandateView
from app.creative_provider_finalization import provider_aware_creative_generation_service
from app.organic_creative_execution import (
    organic_creative_distribution_execution_adapter_service,
)


class AutonomousControlledGrowthSweepService(AutonomousOwnedCreativeGrowthSweepService):
    def __init__(
        self,
        *,
        control_service: AutonomousGrowthControlService | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._control_service = control_service or autonomous_growth_control_service

    async def _run_product(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
    ) -> list[AutonomousGrowthDecisionView]:
        self._control_service.evaluate_running(mandate)
        return await super()._run_product(run_id, mandate)


autonomous_controlled_growth_sweep_service = AutonomousControlledGrowthSweepService(
    adapter_service=organic_creative_distribution_execution_adapter_service,
    generation_service=provider_aware_creative_generation_service,
)
