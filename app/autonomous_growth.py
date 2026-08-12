from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.action_drafting import (
    DistributionActionDraftingService,
    distribution_action_drafting_service,
)
from app.autonomy_schemas import (
    AutonomyDecision,
    AutonomyEvaluationRequest,
    AutonomyEvaluationView,
    GrowthMandateStatus,
    GrowthMandateView,
)
from app.autonomy_service import (
    GROWTH_MANDATE_NAMESPACE,
    GrowthMandateService,
    growth_mandate_service,
)
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_types import DistributionActionType
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    distribution_execution_adapter_service,
)
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

AUTONOMOUS_GROWTH_RUN_NAMESPACE = "autonomous_growth_run"
AUTONOMOUS_GROWTH_DECISION_NAMESPACE = "autonomous_growth_decision"
AUTONOMOUS_PORTFOLIO_LIMIT = 12


class AutonomousGrowthOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    ASSISTED = "ASSISTED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class AutonomousGrowthDecisionView(BaseModel):
    id: UUID
    run_id: UUID
    product_id: UUID
    mandate_id: UUID
    mandate_version: int = Field(ge=1)
    play_id: UUID | None = None
    action_id: UUID | None = None
    experiment_id: UUID | None = None
    platform: str | None = None
    action_type: str | None = None
    evaluation_decision: AutonomyDecision | None = None
    proposed_budget: float = Field(default=0, ge=0)
    outcome: AutonomousGrowthOutcome
    adapter_outcome: str | None = None
    reasons: list[str] = Field(default_factory=list)
    recorded_at: datetime


class AutonomousGrowthSweepView(BaseModel):
    run_id: UUID
    started_at: datetime
    finished_at: datetime
    product_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    executed_count: int = Field(ge=0)
    waiting_approval_count: int = Field(ge=0)
    assisted_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    decisions: list[AutonomousGrowthDecisionView]


class AutonomousGrowthSweepService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        mandate_service: GrowthMandateService | None = None,
        drafting_service: DistributionActionDraftingService | None = None,
        adapter_service: DistributionExecutionAdapterService | None = None,
        history_retention: int = 100,
    ) -> None:
        if history_retention < 1:
            raise ValueError("history_retention must be positive")
        self._store = store or get_runtime_store()
        self._mandate_service = mandate_service or growth_mandate_service
        self._drafting_service = drafting_service or distribution_action_drafting_service
        self._adapter_service = adapter_service or distribution_execution_adapter_service
        self._history_retention = history_retention
        self._lock = Lock()

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    async def run_once(
        self,
        product_id: UUID | None = None,
    ) -> AutonomousGrowthSweepView:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("An autonomous-growth sweep is already running in this process")
        try:
            started_at = datetime.now(UTC)
            run_id = uuid4()
            mandates = self._candidate_mandates(product_id)
            decisions: list[AutonomousGrowthDecisionView] = []
            for mandate in mandates:
                decisions.extend(await self._run_product(run_id, mandate))
            result = AutonomousGrowthSweepView(
                run_id=run_id,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                product_count=len(mandates),
                decision_count=len(decisions),
                executed_count=self._count(decisions, AutonomousGrowthOutcome.EXECUTED),
                waiting_approval_count=self._count(
                    decisions,
                    AutonomousGrowthOutcome.WAITING_APPROVAL,
                ),
                assisted_count=self._count(decisions, AutonomousGrowthOutcome.ASSISTED),
                unavailable_count=self._count(
                    decisions,
                    AutonomousGrowthOutcome.UNAVAILABLE,
                ),
                blocked_count=self._count(decisions, AutonomousGrowthOutcome.BLOCKED),
                failed_count=self._count(decisions, AutonomousGrowthOutcome.FAILED),
                error_count=self._count(decisions, AutonomousGrowthOutcome.ERROR),
                decisions=decisions,
            )
            self._persist_run(result)
            return result
        finally:
            self._lock.release()

    def recent_runs(self, limit: int = 20) -> list[AutonomousGrowthSweepView]:
        if limit < 1 or limit > self._history_retention:
            raise ValueError(
                f"limit must be between 1 and {self._history_retention}"
            )
        runs = [
            AutonomousGrowthSweepView.model_validate(payload)
            for payload in self._store.list_namespace(AUTONOMOUS_GROWTH_RUN_NAMESPACE)
        ]
        runs.sort(key=lambda run: (run.finished_at, str(run.run_id)), reverse=True)
        return runs[:limit]

    async def _run_product(
        self,
        run_id: UUID,
        mandate: GrowthMandateView,
    ) -> list[AutonomousGrowthDecisionView]:
        pending = self._pending_human_resolution(mandate.product_id)
        if pending:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    outcome=AutonomousGrowthOutcome.SKIPPED,
                    reasons=[
                        "An existing DRAFT/APPROVED experiment requires resolution before "
                        "another autonomous action is prepared"
                    ],
                )
            ]

        try:
            product = product_intake_service.get_product(mandate.product_id)
            portfolio = distribution_growth_manager_service.portfolio(
                mandate.product_id,
                max_items=AUTONOMOUS_PORTFOLIO_LIMIT,
            )
        except KeyError:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    outcome=AutonomousGrowthOutcome.SKIPPED,
                    reasons=[
                        "Product or Distribution Plays are unavailable; generate the distribution "
                        "portfolio before autonomous execution"
                    ],
                )
            ]
        except ValueError as exc:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    outcome=AutonomousGrowthOutcome.ERROR,
                    reasons=[str(exc)[:1000]],
                )
            ]

        pending_play_ids = self._pending_play_ids(mandate.product_id)
        eligible = [
            item
            for item in portfolio.items
            if item.play.id not in pending_play_ids
            and item.play.action_type != DistributionActionType.PAID_CAMPAIGN
            and item.play.platform in mandate.allowed_platforms
            and item.play.action_type in mandate.allowed_actions
        ]
        if not eligible:
            return [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    outcome=AutonomousGrowthOutcome.SKIPPED,
                    reasons=[
                        "No eligible non-paid READY portfolio item is available inside the "
                        "current Growth Mandate"
                    ],
                )
            ]

        decisions: list[AutonomousGrowthDecisionView] = []
        for item in eligible:
            play = item.play
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
            changed = self._mandate_changed(mandate, precheck)
            if changed:
                current = self._current_mandate(mandate)
                return [
                    self._record(
                        run_id=run_id,
                        mandate=current,
                        play_id=play.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=precheck.decision,
                        outcome=AutonomousGrowthOutcome.BLOCKED,
                        reasons=[
                            "Growth Mandate changed during the sweep; retry under the current "
                            "mandate version"
                        ],
                    )
                ]
            if precheck.decision != AutonomyDecision.ALLOW:
                outcome = (
                    AutonomousGrowthOutcome.BLOCKED
                    if precheck.decision == AutonomyDecision.BLOCK
                    else AutonomousGrowthOutcome.WAITING_APPROVAL
                )
                return [
                    self._record(
                        run_id=run_id,
                        mandate=mandate,
                        play_id=play.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=precheck.decision,
                        outcome=outcome,
                        reasons=precheck.reasons,
                    )
                ]

            try:
                plan = await self._drafting_service.auto_prepare(
                    product=product,
                    play=play,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                decisions.append(
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
                )
                continue

            approval_check = self._mandate_service.evaluate(
                mandate.product_id,
                AutonomyEvaluationRequest(
                    platform=play.platform,
                    action_type=play.action_type,
                    proposed_budget=0,
                    requires_prepare=False,
                    requires_approval=True,
                    requests_paid_activation=False,
                ),
            )
            if self._mandate_changed(mandate, approval_check):
                distribution_execution_service.skip(plan.action.id)
                current = self._current_mandate(mandate)
                return decisions + [
                    self._record(
                        run_id=run_id,
                        mandate=current,
                        play_id=play.id,
                        action_id=plan.action.id,
                        experiment_id=plan.experiment.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=approval_check.decision,
                        outcome=AutonomousGrowthOutcome.BLOCKED,
                        reasons=[
                            "Growth Mandate changed after preparation; the prepared action was "
                            "cancelled"
                        ],
                    )
                ]
            if approval_check.decision == AutonomyDecision.BLOCK:
                distribution_execution_service.skip(plan.action.id)
                return decisions + [
                    self._record(
                        run_id=run_id,
                        mandate=mandate,
                        play_id=play.id,
                        action_id=plan.action.id,
                        experiment_id=plan.experiment.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=approval_check.decision,
                        outcome=AutonomousGrowthOutcome.BLOCKED,
                        reasons=approval_check.reasons,
                    )
                ]
            if approval_check.decision == AutonomyDecision.REQUIRE_APPROVAL:
                return decisions + [
                    self._record(
                        run_id=run_id,
                        mandate=mandate,
                        play_id=play.id,
                        action_id=plan.action.id,
                        experiment_id=plan.experiment.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=approval_check.decision,
                        outcome=AutonomousGrowthOutcome.WAITING_APPROVAL,
                        reasons=approval_check.reasons,
                    )
                ]

            try:
                approved = distribution_execution_service.approve(plan.action.id)
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
                    distribution_execution_service.skip(approved.action.id)
                    current = self._current_mandate(mandate)
                    return decisions + [
                        self._record(
                            run_id=run_id,
                            mandate=current,
                            play_id=play.id,
                            action_id=approved.action.id,
                            experiment_id=approved.experiment.id,
                            platform=play.platform.value,
                            action_type=play.action_type.value,
                            evaluation_decision=execution_check.decision,
                            outcome=AutonomousGrowthOutcome.BLOCKED,
                            reasons=[
                                "Growth Mandate changed immediately before execution; the "
                                "approved action was cancelled"
                            ],
                        )
                    ]
                if execution_check.decision != AutonomyDecision.ALLOW:
                    distribution_execution_service.skip(approved.action.id)
                    return decisions + [
                        self._record(
                            run_id=run_id,
                            mandate=mandate,
                            play_id=play.id,
                            action_id=approved.action.id,
                            experiment_id=approved.experiment.id,
                            platform=play.platform.value,
                            action_type=play.action_type.value,
                            evaluation_decision=execution_check.decision,
                            outcome=AutonomousGrowthOutcome.BLOCKED,
                            reasons=execution_check.reasons,
                        )
                    ]
                execution = self._adapter_service.execute(
                    approved.action.id,
                    DistributionAdapterExecuteRequest(retry=False),
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                return decisions + [
                    self._record(
                        run_id=run_id,
                        mandate=self._current_mandate(mandate),
                        play_id=play.id,
                        action_id=plan.action.id,
                        experiment_id=plan.experiment.id,
                        platform=play.platform.value,
                        action_type=play.action_type.value,
                        evaluation_decision=approval_check.decision,
                        outcome=AutonomousGrowthOutcome.ERROR,
                        reasons=[str(exc)[:1000]],
                    )
                ]

            adapter_outcome = execution.receipt.outcome
            outcome = self._adapter_outcome(adapter_outcome)
            return decisions + [
                self._record(
                    run_id=run_id,
                    mandate=mandate,
                    play_id=play.id,
                    action_id=execution.plan.action.id,
                    experiment_id=execution.plan.experiment.id,
                    platform=play.platform.value,
                    action_type=play.action_type.value,
                    evaluation_decision=approval_check.decision,
                    outcome=outcome,
                    adapter_outcome=adapter_outcome.value,
                    reasons=[execution.receipt.message],
                )
            ]

        return decisions or [
            self._record(
                run_id=run_id,
                mandate=mandate,
                outcome=AutonomousGrowthOutcome.SKIPPED,
                reasons=["No autonomous action could be prepared from the current portfolio"],
            )
        ]

    def _candidate_mandates(
        self,
        product_id: UUID | None,
    ) -> list[GrowthMandateView]:
        mandates = [
            GrowthMandateView.model_validate(payload)
            for payload in self._store.list_namespace(GROWTH_MANDATE_NAMESPACE)
        ]
        if product_id is not None:
            mandates = [item for item in mandates if item.product_id == product_id]
        else:
            mandates = [
                item for item in mandates if item.status == GrowthMandateStatus.ACTIVE
            ]
        return sorted(mandates, key=lambda item: str(item.product_id))

    def _pending_human_resolution(self, product_id: UUID) -> bool:
        return any(
            experiment.status
            in {
                DistributionExperimentStatus.DRAFT,
                DistributionExperimentStatus.APPROVED,
            }
            for experiment in distribution_execution_service.list_experiments(product_id)
        )

    def _pending_play_ids(self, product_id: UUID) -> set[UUID]:
        return {
            experiment.distribution_play_id
            for experiment in distribution_execution_service.list_experiments(product_id)
            if experiment.status
            in {
                DistributionExperimentStatus.DRAFT,
                DistributionExperimentStatus.APPROVED,
                DistributionExperimentStatus.RUNNING,
            }
        }

    def _mandate_changed(
        self,
        mandate: GrowthMandateView,
        evaluation: AutonomyEvaluationView,
    ) -> bool:
        return (
            evaluation.mandate_id != mandate.id
            or evaluation.mandate_version != mandate.version
        )

    def _current_mandate(self, fallback: GrowthMandateView) -> GrowthMandateView:
        try:
            return self._mandate_service.get(fallback.product_id)
        except KeyError:
            return fallback

    def _adapter_outcome(
        self,
        outcome: AdapterExecutionOutcome,
    ) -> AutonomousGrowthOutcome:
        mapping = {
            AdapterExecutionOutcome.EXECUTED: AutonomousGrowthOutcome.EXECUTED,
            AdapterExecutionOutcome.ASSISTED: AutonomousGrowthOutcome.ASSISTED,
            AdapterExecutionOutcome.UNAVAILABLE: AutonomousGrowthOutcome.UNAVAILABLE,
            AdapterExecutionOutcome.FAILED: AutonomousGrowthOutcome.FAILED,
            AdapterExecutionOutcome.IN_PROGRESS: AutonomousGrowthOutcome.FAILED,
            AdapterExecutionOutcome.STAGED: AutonomousGrowthOutcome.ERROR,
        }
        return mapping[outcome]

    def _record(
        self,
        *,
        run_id: UUID,
        mandate: GrowthMandateView,
        outcome: AutonomousGrowthOutcome,
        reasons: list[str],
        play_id: UUID | None = None,
        action_id: UUID | None = None,
        experiment_id: UUID | None = None,
        platform: str | None = None,
        action_type: str | None = None,
        evaluation_decision: AutonomyDecision | None = None,
        proposed_budget: float = 0,
        adapter_outcome: str | None = None,
    ) -> AutonomousGrowthDecisionView:
        decision = AutonomousGrowthDecisionView(
            id=uuid4(),
            run_id=run_id,
            product_id=mandate.product_id,
            mandate_id=mandate.id,
            mandate_version=mandate.version,
            play_id=play_id,
            action_id=action_id,
            experiment_id=experiment_id,
            platform=platform,
            action_type=action_type,
            evaluation_decision=evaluation_decision,
            proposed_budget=proposed_budget,
            outcome=outcome,
            adapter_outcome=adapter_outcome,
            reasons=reasons,
            recorded_at=datetime.now(UTC),
        )
        self._store.put(
            AUTONOMOUS_GROWTH_DECISION_NAMESPACE,
            str(decision.id),
            decision.model_dump(mode="json"),
        )
        return decision

    def _persist_run(self, result: AutonomousGrowthSweepView) -> None:
        self._store.put(
            AUTONOMOUS_GROWTH_RUN_NAMESPACE,
            str(result.run_id),
            result.model_dump(mode="json"),
        )
        runs = [
            AutonomousGrowthSweepView.model_validate(payload)
            for payload in self._store.list_namespace(AUTONOMOUS_GROWTH_RUN_NAMESPACE)
        ]
        if len(runs) <= self._history_retention:
            return
        runs.sort(key=lambda run: (run.finished_at, str(run.run_id)), reverse=True)
        for stale in runs[self._history_retention :]:
            self._store.delete(AUTONOMOUS_GROWTH_RUN_NAMESPACE, str(stale.run_id))

    def _count(
        self,
        decisions: list[AutonomousGrowthDecisionView],
        outcome: AutonomousGrowthOutcome,
    ) -> int:
        return sum(item.outcome == outcome for item in decisions)


autonomous_growth_sweep_service = AutonomousGrowthSweepService()
