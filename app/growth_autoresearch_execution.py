from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.action_drafting import (
    DistributionActionDraftingService,
    distribution_action_drafting_service,
)
from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest
from app.autonomy_service import GrowthMandateService, growth_mandate_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_schemas import (
    DistributionPlayStatus,
    DistributionPlayView,
    DistributionTacticClass,
)
from app.distribution_play_service import distribution_play_service
from app.distribution_types import DistributionActionStatus, DistributionActionType, DistributionPlatform
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    distribution_execution_adapter_service,
)
from app.growth_autoresearch import GrowthAutoResearchService, growth_autoresearch_service
from app.growth_autoresearch_execution_schemas import (
    GrowthAutoResearchExecutionStatus,
    GrowthAutoResearchExecutionSweepView,
    GrowthAutoResearchExecutionView,
)
from app.growth_autoresearch_schemas import GrowthResearchTrialStatus, GrowthResearchTrialView
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

GROWTH_AUTORESEARCH_EXECUTION_NAMESPACE = "growth_autoresearch_execution"
GROWTH_AUTORESEARCH_EXECUTION_TRIAL_NAMESPACE = "growth_autoresearch_execution_trial"

_TERMINAL = {
    GrowthAutoResearchExecutionStatus.EXECUTED,
    GrowthAutoResearchExecutionStatus.ASSISTED,
    GrowthAutoResearchExecutionStatus.UNAVAILABLE,
    GrowthAutoResearchExecutionStatus.BLOCKED,
    GrowthAutoResearchExecutionStatus.FAILED,
    GrowthAutoResearchExecutionStatus.ERROR,
}


class GrowthAutoResearchExecutionService:
    """Bridge a READY research trial into the existing non-paid execution control plane."""

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        autoresearch: GrowthAutoResearchService | None = None,
        mandate_service: GrowthMandateService | None = None,
        drafting_service: DistributionActionDraftingService | None = None,
        adapter_service: DistributionExecutionAdapterService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._autoresearch = autoresearch or growth_autoresearch_service
        self._mandate_service = mandate_service or growth_mandate_service
        self._drafting_service = drafting_service or distribution_action_drafting_service
        self._adapter_service = adapter_service or distribution_execution_adapter_service

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    async def run_once(
        self,
        *,
        product_id: UUID | None = None,
    ) -> GrowthAutoResearchExecutionSweepView:
        product_ids = [product_id] if product_id is not None else self._configured_product_ids()
        results: list[GrowthAutoResearchExecutionView] = []
        for candidate_product_id in product_ids:
            history = self._autoresearch.history(candidate_product_id)
            ready = [
                trial
                for trial in history.trials
                if trial.status == GrowthResearchTrialStatus.READY
            ]
            if not ready:
                continue
            trial = ready[-1]
            existing = self.get_for_trial(trial.id)
            if existing is not None and existing.status in _TERMINAL:
                continue
            try:
                results.append(await self.execute_trial(trial.id))
            except (KeyError, RuntimeError, ValueError) as exc:
                results.append(
                    self._record(
                        trial=trial,
                        status=GrowthAutoResearchExecutionStatus.ERROR,
                        platform=trial.challenger.platform,
                        reasons=[str(exc)[:1000]],
                        existing=existing,
                    )
                )
        return GrowthAutoResearchExecutionSweepView(
            product_id=product_id,
            attempted_count=len(results),
            executed_count=sum(item.status == "EXECUTED" for item in results),
            blocked_count=sum(
                item.status
                in {
                    GrowthAutoResearchExecutionStatus.BLOCKED,
                    GrowthAutoResearchExecutionStatus.UNAVAILABLE,
                }
                for item in results
            ),
            executions=results,
            created_at=datetime.now(UTC),
        )

    async def execute_trial(self, trial_id: UUID) -> GrowthAutoResearchExecutionView:
        trial = self._autoresearch.get_trial(trial_id)
        if trial.status != GrowthResearchTrialStatus.READY:
            raise ValueError("Only READY Growth AutoResearch trials can enter live execution")
        policy = self._autoresearch.get_policy(trial.product_id)
        existing = self.get_for_trial(trial.id)
        if existing is not None and existing.status in _TERMINAL:
            return existing
        if policy.paused:
            return self._record(
                trial=trial,
                status=GrowthAutoResearchExecutionStatus.BLOCKED,
                platform=trial.challenger.platform,
                reasons=["Growth AutoResearch is paused; no live action was created."],
                existing=existing,
            )

        play = self._resolve_play(trial)
        if play.action_type == DistributionActionType.PAID_CAMPAIGN:
            return self._blocked(
                trial,
                play,
                "Paid campaigns are hard-blocked in AutoResearch Phase 5.",
                existing,
            )
        if play.tactic_class == DistributionTacticClass.PAID_PLATFORM:
            return self._blocked(
                trial,
                play,
                "Paid-platform tactics are hard-blocked in AutoResearch Phase 5.",
                existing,
            )
        if not self._customer_channel_allows_execution(trial.product_id, play.platform):
            return self._blocked(
                trial,
                play,
                (
                    f"Customer channel {play.platform.value} is not in AUTO mode; "
                    "Research only/Off never grants execution permission."
                ),
                existing,
            )

        precheck = self._mandate_service.evaluate(
            trial.product_id,
            AutonomyEvaluationRequest(
                platform=play.platform,
                action_type=play.action_type,
                proposed_budget=0,
                requires_prepare=True,
                requires_approval=False,
                requests_paid_activation=False,
            ),
        )
        if precheck.decision != AutonomyDecision.ALLOW:
            return self._record(
                trial=trial,
                play=play,
                status=GrowthAutoResearchExecutionStatus.BLOCKED,
                platform=play.platform.value,
                action_type=play.action_type.value,
                mandate_id=precheck.mandate_id,
                mandate_version=precheck.mandate_version,
                reasons=precheck.reasons,
                existing=existing,
            )

        if existing is not None and existing.action_id is not None:
            try:
                plan = distribution_execution_service.get_plan(existing.action_id)
            except KeyError:
                return self._record(
                    trial=trial,
                    play=play,
                    status=GrowthAutoResearchExecutionStatus.ERROR,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    reasons=["Persisted AutoResearch execution link points to a missing action."],
                    existing=existing,
                )
        else:
            product = product_intake_service.get_product(trial.product_id)
            trial_play = self._play_with_trial_context(play, trial)
            plan = await self._drafting_service.auto_prepare(
                product=product,
                play=trial_play,
                destination_url=trial.challenger.destination_url,
            )
            existing = self._record(
                trial=trial,
                play=play,
                status=GrowthAutoResearchExecutionStatus.PREPARED,
                platform=play.platform.value,
                action_type=play.action_type.value,
                action_id=plan.action.id,
                experiment_id=plan.experiment.id,
                mandate_id=precheck.mandate_id,
                mandate_version=precheck.mandate_version,
                reasons=["Prepared through the existing DistributionExecutionService."],
                existing=existing,
            )

        if plan.action.status == DistributionActionStatus.PREPARED:
            approval_check = self._mandate_service.evaluate(
                trial.product_id,
                AutonomyEvaluationRequest(
                    platform=play.platform,
                    action_type=play.action_type,
                    proposed_budget=0,
                    requires_prepare=False,
                    requires_approval=True,
                    requests_paid_activation=False,
                ),
            )
            if approval_check.decision == AutonomyDecision.BLOCK:
                distribution_execution_service.skip(plan.action.id)
                return self._record(
                    trial=trial,
                    play=play,
                    status=GrowthAutoResearchExecutionStatus.BLOCKED,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    mandate_id=approval_check.mandate_id,
                    mandate_version=approval_check.mandate_version,
                    reasons=approval_check.reasons,
                    existing=existing,
                )
            if approval_check.decision == AutonomyDecision.REQUIRE_APPROVAL:
                return self._record(
                    trial=trial,
                    play=play,
                    status=GrowthAutoResearchExecutionStatus.WAITING_APPROVAL,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    mandate_id=approval_check.mandate_id,
                    mandate_version=approval_check.mandate_version,
                    reasons=approval_check.reasons,
                    existing=existing,
                )
            try:
                plan = distribution_execution_service.approve(plan.action.id)
            except (KeyError, ValueError) as exc:
                self._cancel_if_possible(plan.action.id)
                return self._record(
                    trial=trial,
                    play=play,
                    status=GrowthAutoResearchExecutionStatus.BLOCKED,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    action_id=plan.action.id,
                    experiment_id=plan.experiment.id,
                    mandate_id=approval_check.mandate_id,
                    mandate_version=approval_check.mandate_version,
                    reasons=[str(exc)[:1000]],
                    existing=existing,
                )

        if plan.action.status != DistributionActionStatus.APPROVED:
            return self._record(
                trial=trial,
                play=play,
                status=GrowthAutoResearchExecutionStatus.ERROR,
                platform=play.platform.value,
                action_type=play.action_type.value,
                action_id=plan.action.id,
                experiment_id=plan.experiment.id,
                reasons=[f"Unexpected action state before execution: {plan.action.status.value}"],
                existing=existing,
            )

        execution_check = self._mandate_service.evaluate(
            trial.product_id,
            AutonomyEvaluationRequest(
                platform=play.platform,
                action_type=play.action_type,
                proposed_budget=0,
                requires_prepare=False,
                requires_approval=False,
                requests_paid_activation=False,
            ),
        )
        if execution_check.decision != AutonomyDecision.ALLOW:
            distribution_execution_service.skip(plan.action.id)
            return self._record(
                trial=trial,
                play=play,
                status=GrowthAutoResearchExecutionStatus.BLOCKED,
                platform=play.platform.value,
                action_type=play.action_type.value,
                action_id=plan.action.id,
                experiment_id=plan.experiment.id,
                mandate_id=execution_check.mandate_id,
                mandate_version=execution_check.mandate_version,
                reasons=execution_check.reasons,
                existing=existing,
            )

        execution = self._adapter_service.execute(
            plan.action.id,
            DistributionAdapterExecuteRequest(retry=False),
        )
        status = self._adapter_status(execution.receipt.outcome)
        return self._record(
            trial=trial,
            play=play,
            status=status,
            platform=play.platform.value,
            action_type=play.action_type.value,
            action_id=execution.plan.action.id,
            experiment_id=execution.plan.experiment.id,
            mandate_id=execution_check.mandate_id,
            mandate_version=execution_check.mandate_version,
            adapter_outcome=execution.receipt.outcome.value,
            reasons=[execution.receipt.message],
            existing=existing,
        )

    def get_for_trial(self, trial_id: UUID) -> GrowthAutoResearchExecutionView | None:
        index = self._store.get(GROWTH_AUTORESEARCH_EXECUTION_TRIAL_NAMESPACE, str(trial_id))
        if not index or not index.get("execution_id"):
            return None
        payload = self._store.get(
            GROWTH_AUTORESEARCH_EXECUTION_NAMESPACE,
            str(index["execution_id"]),
        )
        if payload is None:
            return None
        return GrowthAutoResearchExecutionView.model_validate(payload)

    def list_for_product(self, product_id: UUID) -> list[GrowthAutoResearchExecutionView]:
        items = [
            GrowthAutoResearchExecutionView.model_validate(payload)
            for payload in self._store.list_namespace(GROWTH_AUTORESEARCH_EXECUTION_NAMESPACE)
        ]
        items = [item for item in items if item.product_id == product_id]
        return sorted(items, key=lambda item: (item.updated_at, str(item.id)), reverse=True)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(GROWTH_AUTORESEARCH_EXECUTION_NAMESPACE)
            self._store.clear_namespace(GROWTH_AUTORESEARCH_EXECUTION_TRIAL_NAMESPACE)

    def _configured_product_ids(self) -> list[UUID]:
        values: set[UUID] = set()
        for payload in self._store.list_namespace("growth_autoresearch_trial"):
            try:
                trial = GrowthResearchTrialView.model_validate(payload)
            except ValueError:
                continue
            if trial.status == GrowthResearchTrialStatus.READY:
                values.add(trial.product_id)
        return sorted(values, key=str)

    def _resolve_play(self, trial: GrowthResearchTrialView) -> DistributionPlayView:
        try:
            platform = DistributionPlatform(trial.challenger.platform.strip().upper())
        except ValueError as exc:
            raise ValueError(
                f"Unsupported execution platform: {trial.challenger.platform}"
            ) from exc
        generation = distribution_play_service.get(trial.product_id)
        candidates = [
            play
            for play in generation.plays
            if play.status == DistributionPlayStatus.READY
            and play.platform == platform
            and play.tactic_id == trial.challenger.tactic_id
        ]
        if not candidates:
            raise ValueError(
                "No READY DistributionPlay matches the AutoResearch challenger platform/tactic."
            )
        candidates.sort(key=lambda item: (-item.priority_score, str(item.id)))
        return candidates[0]

    def _customer_channel_allows_execution(
        self,
        product_id: UUID,
        platform: DistributionPlatform,
    ) -> bool:
        projects = [
            payload
            for payload in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE)
            if str(payload.get("product_id") or "") == str(product_id)
        ]
        if not projects:
            return True
        return any(
            platform in customer_channel_service.autonomous_platforms(project)
            for project in projects
        )

    def _play_with_trial_context(
        self,
        play: DistributionPlayView,
        trial: GrowthResearchTrialView,
    ) -> DistributionPlayView:
        parts = [
            trial.hypothesis or "Test the bounded AutoResearch challenger against the champion.",
        ]
        if trial.challenger.audience:
            parts.append(f"Audience: {trial.challenger.audience}")
        if trial.challenger.message_angle:
            parts.append(f"Message angle: {trial.challenger.message_angle}")
        if trial.challenger.offer:
            parts.append(f"Offer: {trial.challenger.offer}")
        if trial.challenger.cta:
            parts.append(f"CTA: {trial.challenger.cta}")
        rationale = list(play.rationale)
        rationale.append(
            "AutoResearch trial context: " + " | ".join(parts)[:3500]
        )
        return play.model_copy(
            update={
                "hypothesis": "\n".join(parts),
                "rationale": rationale,
            }
        )

    def _blocked(
        self,
        trial: GrowthResearchTrialView,
        play: DistributionPlayView,
        reason: str,
        existing: GrowthAutoResearchExecutionView | None,
    ) -> GrowthAutoResearchExecutionView:
        return self._record(
            trial=trial,
            play=play,
            status=GrowthAutoResearchExecutionStatus.BLOCKED,
            platform=play.platform.value,
            action_type=play.action_type.value,
            reasons=[reason],
            existing=existing,
        )

    def _record(
        self,
        *,
        trial: GrowthResearchTrialView,
        status: GrowthAutoResearchExecutionStatus,
        platform: str,
        reasons: list[str],
        play: DistributionPlayView | None = None,
        action_type: str | None = None,
        action_id: UUID | None = None,
        experiment_id: UUID | None = None,
        mandate_id: UUID | None = None,
        mandate_version: int | None = None,
        adapter_outcome: str | None = None,
        existing: GrowthAutoResearchExecutionView | None = None,
    ) -> GrowthAutoResearchExecutionView:
        now = datetime.now(UTC)
        result = GrowthAutoResearchExecutionView(
            id=existing.id if existing is not None else uuid4(),
            product_id=trial.product_id,
            trial_id=trial.id,
            play_id=(play.id if play is not None else (existing.play_id if existing else None)),
            action_id=action_id or (existing.action_id if existing else None),
            experiment_id=experiment_id or (existing.experiment_id if existing else None),
            mandate_id=mandate_id or (existing.mandate_id if existing else None),
            mandate_version=(
                mandate_version
                if mandate_version is not None
                else (existing.mandate_version if existing else None)
            ),
            platform=platform,
            action_type=action_type or (existing.action_type if existing else None),
            status=status,
            adapter_outcome=adapter_outcome,
            proposed_spend=0,
            reasons=reasons,
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )
        self._store.put(
            GROWTH_AUTORESEARCH_EXECUTION_NAMESPACE,
            str(result.id),
            result.model_dump(mode="json"),
        )
        self._store.put(
            GROWTH_AUTORESEARCH_EXECUTION_TRIAL_NAMESPACE,
            str(trial.id),
            {"execution_id": str(result.id)},
        )
        return result

    def _cancel_if_possible(self, action_id: UUID) -> None:
        try:
            action = distribution_execution_service.get_action(action_id)
            if action.status in {
                DistributionActionStatus.PREPARED,
                DistributionActionStatus.APPROVED,
            }:
                distribution_execution_service.skip(action_id)
        except (KeyError, ValueError):
            return

    @staticmethod
    def _adapter_status(outcome: AdapterExecutionOutcome) -> GrowthAutoResearchExecutionStatus:
        mapping = {
            AdapterExecutionOutcome.EXECUTED: GrowthAutoResearchExecutionStatus.EXECUTED,
            AdapterExecutionOutcome.ASSISTED: GrowthAutoResearchExecutionStatus.ASSISTED,
            AdapterExecutionOutcome.UNAVAILABLE: GrowthAutoResearchExecutionStatus.UNAVAILABLE,
            AdapterExecutionOutcome.FAILED: GrowthAutoResearchExecutionStatus.FAILED,
            AdapterExecutionOutcome.IN_PROGRESS: GrowthAutoResearchExecutionStatus.ERROR,
            AdapterExecutionOutcome.STAGED: GrowthAutoResearchExecutionStatus.BLOCKED,
        }
        return mapping[outcome]


growth_autoresearch_execution_service = GrowthAutoResearchExecutionService()
