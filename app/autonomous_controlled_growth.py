from __future__ import annotations

from uuid import UUID

from app.autonomous_growth import (
    AutonomousGrowthDecisionView,
    AutonomousGrowthOutcome,
)
from app.autonomous_growth_control import (
    AutonomousGrowthControlService,
    autonomous_growth_control_service,
)
from app.autonomous_owned_creative_growth import AutonomousOwnedCreativeGrowthSweepService
from app.autonomy_schemas import AutonomyDecision, GrowthMandateView
from app.creative_provider_finalization import provider_aware_creative_generation_service
from app.execution_adapters import AdapterExecutionOutcome
from app.organic_creative_execution import (
    organic_creative_distribution_execution_adapter_service,
)
from app.outreach_policy import (
    OutreachAutonomousPreparationService,
    outreach_autonomous_preparation_service,
)


class AutonomousControlledGrowthSweepService(AutonomousOwnedCreativeGrowthSweepService):
    def __init__(
        self,
        *,
        control_service: AutonomousGrowthControlService | None = None,
        outreach_preparation_service: OutreachAutonomousPreparationService | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._control_service = control_service or autonomous_growth_control_service
        self._outreach_preparation_service = (
            outreach_preparation_service or outreach_autonomous_preparation_service
        )

    async def _run_product(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
    ) -> list[AutonomousGrowthDecisionView]:
        self._control_service.evaluate_running(mandate)
        outreach = await self._outreach_preparation_service.prepare_next(mandate.product_id)
        if outreach is not None and outreach.prepared:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=outreach.play_id,
                    action_id=outreach.action_id,
                    experiment_id=outreach.experiment_id,
                    platform=outreach.platform,
                    action_type="OUTREACH_EMAIL",
                    evaluation_decision=AutonomyDecision.REQUIRE_APPROVAL,
                    outcome=AutonomousGrowthOutcome.WAITING_APPROVAL,
                    reasons=outreach.reasons,
                )
            ]
        return await super()._run_product(run_id, mandate)

    def _adapter_outcome(
        self,
        outcome: AdapterExecutionOutcome,
    ) -> AutonomousGrowthOutcome:
        if outcome == AdapterExecutionOutcome.IN_PROGRESS:
            return AutonomousGrowthOutcome.ASSISTED
        return super()._adapter_outcome(outcome)


autonomous_controlled_growth_sweep_service = AutonomousControlledGrowthSweepService(
    adapter_service=organic_creative_distribution_execution_adapter_service,
    generation_service=provider_aware_creative_generation_service,
)
