from __future__ import annotations

from uuid import UUID

from app.autonomous_growth import AutonomousGrowthDecisionView, AutonomousGrowthOutcome
from app.autonomous_paid import (
    AutonomousPaidActivationOutcome,
    AutonomousPaidActivationResult,
)
from app.autonomous_paid_growth import AutonomousPaidGrowthSweepService
from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest, GrowthMandateView
from app.creative_assets import CreativeReadinessStatus, creative_asset_service
from app.creative_generation import (
    CreativeGenerationOutcome,
    CreativeGenerationService,
    creative_generation_service,
)
from app.distribution_execution_schemas import (
    DistributionExecutionPlanView,
    DistributionExperimentStatus,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionStatus, DistributionActionType
from app.execution_adapters import AdapterExecutionOutcome, DistributionAdapterExecuteRequest
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service


class AutonomousCreativePaidGrowthSweepService(AutonomousPaidGrowthSweepService):
    """Paid growth worker that requires an action-level provider-ready creative before approval."""

    def __init__(
        self,
        *,
        generation_service: CreativeGenerationService | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._generation_service = generation_service or creative_generation_service

    async def _run_product(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
    ) -> list[AutonomousGrowthDecisionView]:
        pending = self._pending_paid_plan(mandate)
        if pending is not None:
            plan, play = pending
            return self._advance_paid_plan(
                run_id=run_id,
                mandate=mandate,
                play=play,
                plan=plan,
                precheck_decision=None,
            )
        return await super()._run_product(run_id, mandate)

    async def _run_paid_candidate(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
        play,
    ) -> list[AutonomousGrowthDecisionView]:
        precheck = self._mandate_service.evaluate(
            mandate.product_id,
            AutonomyEvaluationRequest(
                platform=play.platform,
                action_type=play.action_type,
                proposed_budget=0,
                requires_prepare=True,
                requires_approval=False,
                requests_paid_activation=False,
            ),
        )
        if self._mandate_changed(mandate, precheck):
            return [
                self._record(
                    run_id=run_id,
                    mandate=self._current_mandate(mandate),
                    play_id=play.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=precheck.decision,
                    outcome=AutonomousGrowthOutcome.BLOCKED,
                    reasons=["Growth Mandate changed before paid preparation"],
                )
            ]
        if precheck.decision != AutonomyDecision.ALLOW:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=precheck.decision,
                    outcome=(
                        AutonomousGrowthOutcome.BLOCKED
                        if precheck.decision == AutonomyDecision.BLOCK
                        else AutonomousGrowthOutcome.WAITING_APPROVAL
                    ),
                    reasons=precheck.reasons,
                )
            ]

        product = product_intake_service.get_product(mandate.product_id)
        try:
            plan = await self._drafting_service.auto_prepare(product=product, play=play)
        except (KeyError, RuntimeError, ValueError) as exc:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=precheck.decision,
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=[str(exc)[:1000]],
                )
            ]

        return self._advance_paid_plan(
            run_id=run_id,
            mandate=mandate,
            play=play,
            plan=plan,
            precheck_decision=precheck.decision,
        )

    def _advance_paid_plan(
        self,
        *,
        run_id: UUID,
        mandate: GrowthMandateView,
        play,
        plan: DistributionExecutionPlanView,
        precheck_decision: AutonomyDecision | None,
    ) -> list[AutonomousGrowthDecisionView]:
        spec = self._spec_service.get(plan.action.id)
        if spec is None:
            if plan.action.status in {
                DistributionActionStatus.PREPARED,
                DistributionActionStatus.APPROVED,
            }:
                distribution_execution_service.skip(plan.action.id)
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=precheck_decision,
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=["PaidCampaignSpec is required before creative preflight"],
                )
            ]
        exact_budget = round(spec.budget_cap, 2)

        try:
            generation = self._generation_service.ensure_ready(plan.action.id)
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
                    evaluation_decision=precheck_decision,
                    proposed_budget=exact_budget,
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=[f"Creative preflight failed: {str(exc)[:900]}"],
                )
            ]

        if generation.outcome != CreativeGenerationOutcome.READY:
            outcome = (
                AutonomousGrowthOutcome.UNAVAILABLE
                if generation.outcome == CreativeGenerationOutcome.UNAVAILABLE
                else AutonomousGrowthOutcome.FAILED
            )
            reasons = [generation.message, *generation.readiness.reasons]
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=precheck_decision,
                    proposed_budget=exact_budget,
                    outcome=outcome,
                    reasons=self._dedupe_reasons(reasons),
                )
            ]

        if plan.action.status == DistributionActionStatus.PREPARED:
            approval_check = self._mandate_service.evaluate(
                mandate.product_id,
                AutonomyEvaluationRequest(
                    platform=play.platform,
                    action_type=play.action_type,
                    proposed_budget=exact_budget,
                    requires_prepare=False,
                    requires_approval=True,
                    requests_paid_activation=False,
                ),
            )
            if self._mandate_changed(mandate, approval_check):
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
                        evaluation_decision=approval_check.decision,
                        proposed_budget=exact_budget,
                        outcome=AutonomousGrowthOutcome.BLOCKED,
                        reasons=["Growth Mandate changed after creative preflight"],
                    )
                ]
            if approval_check.decision == AutonomyDecision.BLOCK:
                distribution_execution_service.skip(plan.action.id)
                return [
                    self._record(
                        run_id=run_id,
                        mandate=mandate,
                        play_id=play.id,
                        action_id=plan.action.id,
                        experiment_id=plan.experiment.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=approval_check.decision,
                        proposed_budget=exact_budget,
                        outcome=AutonomousGrowthOutcome.BLOCKED,
                        reasons=approval_check.reasons,
                    )
                ]
            if approval_check.decision == AutonomyDecision.REQUIRE_APPROVAL:
                return [
                    self._record(
                        run_id=run_id,
                        mandate=mandate,
                        play_id=play.id,
                        action_id=plan.action.id,
                        experiment_id=plan.experiment.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=approval_check.decision,
                        proposed_budget=exact_budget,
                        outcome=AutonomousGrowthOutcome.WAITING_APPROVAL,
                        reasons=approval_check.reasons,
                    )
                ]
            approved = distribution_execution_service.approve(plan.action.id)
        elif plan.action.status == DistributionActionStatus.APPROVED:
            approved = distribution_execution_service.get_plan(plan.action.id)
        else:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    proposed_budget=exact_budget,
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=[
                        "Pending paid creative action is neither PREPARED nor APPROVED; "
                        "operator reconciliation is required"
                    ],
                )
            ]

        staging_check = self._mandate_service.evaluate(
            mandate.product_id,
            AutonomyEvaluationRequest(
                platform=play.platform,
                action_type=play.action_type,
                proposed_budget=exact_budget,
                requires_prepare=False,
                requires_approval=False,
                requests_paid_activation=False,
            ),
        )
        if self._mandate_changed(mandate, staging_check) or staging_check.decision != AutonomyDecision.ALLOW:
            distribution_execution_service.skip(approved.action.id)
            return [
                self._record(
                    run_id=run_id,
                    mandate=self._current_mandate(mandate),
                    play_id=play.id,
                    action_id=approved.action.id,
                    experiment_id=approved.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=staging_check.decision,
                    proposed_budget=exact_budget,
                    outcome=AutonomousGrowthOutcome.BLOCKED,
                    reasons=(
                        ["Growth Mandate changed immediately before paid provider staging"]
                        if self._mandate_changed(mandate, staging_check)
                        else staging_check.reasons
                    ),
                )
            ]

        final_readiness = creative_asset_service.readiness(approved.action.id)
        if final_readiness.status != CreativeReadinessStatus.READY:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=approved.action.id,
                    experiment_id=approved.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=staging_check.decision,
                    proposed_budget=exact_budget,
                    outcome=AutonomousGrowthOutcome.BLOCKED,
                    reasons=[
                        "Creative readiness changed before provider staging; no provider objects "
                        "were created.",
                        *final_readiness.reasons,
                    ],
                )
            ]

        try:
            staged = self._adapter_service.execute(
                approved.action.id,
                DistributionAdapterExecuteRequest(retry=False),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=approved.action.id,
                    experiment_id=approved.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    proposed_budget=exact_budget,
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=[str(exc)[:1000]],
                )
            ]

        if staged.receipt.outcome != AdapterExecutionOutcome.STAGED:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=staged.plan.action.id,
                    experiment_id=staged.plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    proposed_budget=exact_budget,
                    adapter_outcome=staged.receipt.outcome.value,
                    outcome=self._adapter_outcome(staged.receipt.outcome),
                    reasons=[staged.receipt.message],
                )
            ]

        paid = self._paid_coordinator.activate_staged(
            mandate=mandate,
            action_id=approved.action.id,
        )
        return [self._paid_result_decision(run_id, mandate, play, approved, paid)]

    def _pending_paid_plan(self, mandate: GrowthMandateView):
        pending = []
        for experiment in distribution_execution_service.list_experiments(mandate.product_id):
            if experiment.status not in {
                DistributionExperimentStatus.DRAFT,
                DistributionExperimentStatus.APPROVED,
            }:
                continue
            try:
                action = distribution_execution_service.get_action(experiment.action_id)
            except KeyError:
                continue
            if action.action_type != DistributionActionType.PAID_CAMPAIGN:
                continue
            if action.platform not in mandate.allowed_platforms:
                continue
            if action.action_type not in mandate.allowed_actions:
                continue
            pending.append((experiment, action))
        if not pending:
            return None
        pending.sort(key=lambda item: str(item[0].id))
        experiment, action = pending[0]
        play = distribution_play_service.find(
            mandate.product_id,
            experiment.distribution_play_id,
        )
        return distribution_execution_service.get_plan(action.id), play

    def _paid_result_decision(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
        play,
        approved: DistributionExecutionPlanView,
        paid: AutonomousPaidActivationResult,
    ) -> AutonomousGrowthDecisionView:
        if paid.outcome == AutonomousPaidActivationOutcome.ACTIVATED:
            assert paid.execution is not None
            return self._record(
                run_id=run_id,
                mandate=mandate,
                play_id=play.id,
                action_id=paid.execution.plan.action.id,
                experiment_id=paid.execution.plan.experiment.id,
                platform=play.platform.value,
                action_type=play.action_type.value,
                evaluation_decision=AutonomyDecision.ALLOW,
                proposed_budget=paid.exact_budget_cap,
                adapter_outcome=paid.execution.receipt.outcome.value,
                outcome=AutonomousGrowthOutcome.EXECUTED,
                reasons=paid.reasons,
            )
        if paid.outcome == AutonomousPaidActivationOutcome.REQUIRE_APPROVAL:
            outcome = AutonomousGrowthOutcome.WAITING_APPROVAL
        elif paid.outcome == AutonomousPaidActivationOutcome.BLOCKED:
            outcome = AutonomousGrowthOutcome.BLOCKED
        else:
            outcome = AutonomousGrowthOutcome.FAILED
        adapter_outcome = (
            paid.execution.receipt.outcome.value if paid.execution is not None else "STAGED"
        )
        return self._record(
            run_id=run_id,
            mandate=mandate,
            play_id=play.id,
            action_id=approved.action.id,
            experiment_id=approved.experiment.id,
            platform=play.platform.value,
            action_type=play.action_type.value,
            proposed_budget=paid.exact_budget_cap,
            adapter_outcome=adapter_outcome,
            outcome=outcome,
            reasons=paid.reasons,
        )

    def _dedupe_reasons(self, reasons: list[str]) -> list[str]:
        result: list[str] = []
        for reason in reasons:
            if reason and reason not in result:
                result.append(reason[:1000])
        return result


autonomous_creative_paid_growth_sweep_service = AutonomousCreativePaidGrowthSweepService()
