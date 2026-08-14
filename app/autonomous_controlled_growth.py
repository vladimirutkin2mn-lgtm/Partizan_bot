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
from app.outreach_autosend import (
    OutreachAutonomousSendOutcome,
    OutreachAutonomousSendService,
    outreach_autonomous_send_service,
)
from app.outreach_autosend_lifecycle import outreach_autosend_lifecycle_service
from app.outreach_learning import OutreachLearningFeedService, outreach_learning_feed_service
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
        outreach_send_service: OutreachAutonomousSendService | None = None,
        outreach_learning_service: OutreachLearningFeedService | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._control_service = control_service or autonomous_growth_control_service
        self._outreach_preparation_service = (
            outreach_preparation_service or outreach_autonomous_preparation_service
        )
        self._outreach_send_service = outreach_send_service or outreach_autonomous_send_service
        self._outreach_learning_service = outreach_learning_service or outreach_learning_feed_service

    async def _run_product(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
    ) -> list[AutonomousGrowthDecisionView]:
        self._control_service.evaluate_running(mandate)

        try:
            learning = self._outreach_learning_service.feed(mandate.product_id)
        except (KeyError, RuntimeError, ValueError) as exc:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    action_type="OUTREACH_EMAIL",
                    evaluation_decision=AutonomyDecision.BLOCK,
                    outcome=AutonomousGrowthOutcome.BLOCKED,
                    reasons=[
                        "Outreach learning failed closed before another autonomous action: "
                        f"{str(exc)[:900]}"
                    ],
                )
            ]
        learning_note = (
            f"Growth Manager learned from {len(learning.evaluated)} updated outreach experiment(s)."
            if learning.evaluated
            else None
        )

        try:
            auto_send = await self._outreach_send_service.run_next(mandate.product_id)
        except (KeyError, RuntimeError, ValueError) as exc:
            reasons = [
                "Outreach auto-send failed closed before a confirmed external mutation: "
                f"{str(exc)[:900]}"
            ]
            if learning_note:
                reasons.append(learning_note)
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    action_type="OUTREACH_EMAIL",
                    evaluation_decision=AutonomyDecision.BLOCK,
                    outcome=AutonomousGrowthOutcome.BLOCKED,
                    reasons=reasons,
                )
            ]
        if auto_send is not None:
            if (
                auto_send.outcome == OutreachAutonomousSendOutcome.REJECTED
                and auto_send.brief_id is not None
            ):
                outreach_autosend_lifecycle_service.finalize_rejected(auto_send.brief_id)
            if auto_send.outcome == OutreachAutonomousSendOutcome.SENT:
                evaluation = AutonomyDecision.ALLOW
                outcome = AutonomousGrowthOutcome.EXECUTED
            elif auto_send.outcome == OutreachAutonomousSendOutcome.REJECTED:
                evaluation = AutonomyDecision.ALLOW
                outcome = AutonomousGrowthOutcome.FAILED
            else:
                evaluation = AutonomyDecision.BLOCK
                outcome = AutonomousGrowthOutcome.BLOCKED
            reasons = list(auto_send.reasons)
            if learning_note:
                reasons.append(learning_note)
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=auto_send.play_id,
                    action_id=auto_send.action_id,
                    experiment_id=auto_send.experiment_id,
                    platform=auto_send.platform,
                    action_type="OUTREACH_EMAIL",
                    evaluation_decision=evaluation,
                    outcome=outcome,
                    reasons=reasons,
                )
            ]

        outreach = await self._outreach_preparation_service.prepare_next(mandate.product_id)
        if outreach is not None and outreach.prepared:
            reasons = list(outreach.reasons)
            if learning_note:
                reasons.append(learning_note)
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
                    reasons=reasons,
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
