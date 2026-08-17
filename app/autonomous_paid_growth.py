from __future__ import annotations

from uuid import UUID

from app.autonomous_growth import (
    AUTONOMOUS_PORTFOLIO_LIMIT,
    AutonomousGrowthDecisionView,
    AutonomousGrowthOutcome,
    AutonomousGrowthSweepService,
)
from app.autonomous_paid import (
    AutonomousPaidActivationCoordinator,
    AutonomousPaidActivationOutcome,
    autonomous_paid_activation_coordinator,
)
from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest, GrowthMandateView
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.execution_adapters import AdapterExecutionOutcome, DistributionAdapterExecuteRequest
from app.paid_campaign import PaidCampaignSpecService, paid_campaign_spec_service
from app.product_intake import product_intake_service


class AutonomousPaidGrowthSweepService(AutonomousGrowthSweepService):
    def __init__(
        self,
        *,
        paid_coordinator: AutonomousPaidActivationCoordinator | None = None,
        spec_service: PaidCampaignSpecService | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._paid_coordinator = paid_coordinator or autonomous_paid_activation_coordinator
        self._spec_service = spec_service or paid_campaign_spec_service

    async def _run_product(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
    ) -> list[AutonomousGrowthDecisionView]:
        if self._pending_human_resolution(mandate.product_id):
            return await super()._run_product(run_id, mandate)

        try:
            product_intake_service.get_product(mandate.product_id)
            portfolio = distribution_growth_manager_service.portfolio(
                mandate.product_id,
                max_items=AUTONOMOUS_PORTFOLIO_LIMIT,
                allowed_platforms=mandate.allowed_platforms,
                allowed_actions=mandate.allowed_actions,
            )
        except (KeyError, ValueError):
            return await super()._run_product(run_id, mandate)

        pending_play_ids = self._pending_play_ids(mandate.product_id)
        candidates = [
            item
            for item in portfolio.items
            if item.play.id not in pending_play_ids
            and item.play.platform in mandate.allowed_platforms
            and item.play.action_type in mandate.allowed_actions
            and (
                item.play.action_type != DistributionActionType.PAID_CAMPAIGN
                or item.play.platform
                in {DistributionPlatform.INSTAGRAM, DistributionPlatform.TIKTOK}
            )
        ]
        if not candidates:
            return await super()._run_product(run_id, mandate)

        top = candidates[0].play
        if top.action_type != DistributionActionType.PAID_CAMPAIGN:
            return await super()._run_product(run_id, mandate)
        return await self._run_paid_candidate(run_id, mandate, top)

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

        spec = self._spec_service.get(plan.action.id)
        if spec is None:
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
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=["PaidCampaignSpec was not created during paid action preparation"],
                )
            ]
        exact_budget = round(spec.budget_cap, 2)

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
                    reasons=["Growth Mandate changed after paid preparation"],
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
        if paid.outcome == AutonomousPaidActivationOutcome.ACTIVATED:
            assert paid.execution is not None
            return [
                self._record(
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
            ]
        if paid.outcome == AutonomousPaidActivationOutcome.REQUIRE_APPROVAL:
            outcome = AutonomousGrowthOutcome.WAITING_APPROVAL
        elif paid.outcome == AutonomousPaidActivationOutcome.BLOCKED:
            outcome = AutonomousGrowthOutcome.BLOCKED
        else:
            outcome = AutonomousGrowthOutcome.FAILED
        adapter_outcome = (
            paid.execution.receipt.outcome.value if paid.execution is not None else "STAGED"
        )
        return [
            self._record(
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
        ]


autonomous_paid_growth_sweep_service = AutonomousPaidGrowthSweepService()
