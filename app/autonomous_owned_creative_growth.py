from __future__ import annotations

from uuid import UUID

from app.autonomous_creative_paid_growth import AutonomousCreativePaidGrowthSweepService
from app.autonomous_growth import AutonomousGrowthDecisionView, AutonomousGrowthOutcome
from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest, GrowthMandateView
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionStatus, DistributionActionType
from app.execution_adapters import DistributionAdapterExecuteRequest


class AutonomousOwnedCreativeGrowthSweepService(AutonomousCreativePaidGrowthSweepService):
    """Resumes approved owned-video actions after creative/provider setup changes."""

    async def _run_product(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
    ) -> list[AutonomousGrowthDecisionView]:
        pending = self._pending_owned_video_plan(mandate)
        if pending is not None:
            plan, play = pending
            return self._resume_owned_video(
                run_id=run_id,
                mandate=mandate,
                plan=plan,
                play=play,
            )
        return await super()._run_product(run_id, mandate)

    def _pending_owned_video_plan(self, mandate: GrowthMandateView):
        candidates = []
        for experiment in distribution_execution_service.list_experiments(mandate.product_id):
            if experiment.status != DistributionExperimentStatus.APPROVED:
                continue
            try:
                action = distribution_execution_service.get_action(experiment.action_id)
            except KeyError:
                continue
            if action.status != DistributionActionStatus.APPROVED:
                continue
            if action.action_type != DistributionActionType.ORGANIC_VIDEO:
                continue
            if action.platform not in mandate.allowed_platforms:
                continue
            if action.action_type not in mandate.allowed_actions:
                continue
            candidates.append((experiment, action))
        if not candidates:
            return None
        candidates.sort(key=lambda item: str(item[0].id))
        experiment, action = candidates[0]
        play = distribution_play_service.find(
            mandate.product_id,
            experiment.distribution_play_id,
        )
        return distribution_execution_service.get_plan(action.id), play

    def _resume_owned_video(
        self,
        *,
        run_id: UUID,
        mandate: GrowthMandateView,
        plan,
        play,
    ) -> list[AutonomousGrowthDecisionView]:
        execution_check = self._mandate_service.evaluate(
            mandate.product_id,
            AutonomyEvaluationRequest(
                platform=play.platform,
                action_type=play.action_type,
                proposed_budget=0,
                requires_prepare=False,
                requires_approval=False,
                requests_paid_activation=False,
            ),
        )
        if self._mandate_changed(mandate, execution_check):
            distribution_execution_service.skip(plan.action.id)
            return [
                self._record(
                    run_id=run_id,
                    mandate=self._current_mandate(mandate),
                    play_id=play.id,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=execution_check.decision,
                    outcome=AutonomousGrowthOutcome.BLOCKED,
                    reasons=[
                        "Growth Mandate changed while owned organic video was waiting; "
                        "the pending action was cancelled."
                    ],
                )
            ]
        if execution_check.decision != AutonomyDecision.ALLOW:
            if execution_check.decision == AutonomyDecision.BLOCK:
                distribution_execution_service.skip(plan.action.id)
                outcome = AutonomousGrowthOutcome.BLOCKED
            else:
                outcome = AutonomousGrowthOutcome.WAITING_APPROVAL
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=execution_check.decision,
                    outcome=outcome,
                    reasons=execution_check.reasons,
                )
            ]

        try:
            execution = self._adapter_service.execute(
                plan.action.id,
                DistributionAdapterExecuteRequest(retry=True),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=execution_check.decision,
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=[str(exc)[:1000]],
                )
            ]

        adapter_outcome = execution.receipt.outcome
        return [
            self._record(
                run_id=run_id,
                mandate=mandate,
                play_id=play.id,
                action_id=execution.plan.action.id,
                experiment_id=execution.plan.experiment.id,
                platform=play.platform.value,
                action_type=play.action_type.value,
                evaluation_decision=execution_check.decision,
                outcome=self._adapter_outcome(adapter_outcome),
                adapter_outcome=adapter_outcome.value,
                reasons=[execution.receipt.message],
            )
        ]
