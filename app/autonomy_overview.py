from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.autonomous_growth import (
    AUTONOMOUS_GROWTH_DECISION_NAMESPACE,
    AutonomousGrowthDecisionView,
)
from app.autonomous_growth_control import (
    AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE,
    AutonomousGrowthControlAuditView,
)
from app.autonomous_paid import (
    AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE,
    AutonomousPaidActivationAuditView,
    AutonomousPaidBudgetExposure,
    autonomous_paid_activation_coordinator,
)
from app.autonomy_schemas import GrowthMandateView
from app.autonomy_service import growth_mandate_service
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.execution_adapters import distribution_execution_adapter_service
from app.paid_campaign import paid_campaign_spec_service
from app.runtime_store import RuntimeStateStore, get_runtime_store


class AutonomyTimelineKind(StrEnum):
    LAUNCH = "LAUNCH"
    PAID_ACTIVATION = "PAID_ACTIVATION"
    GROWTH_CONTROL = "GROWTH_CONTROL"


class AutonomyExperimentSummary(BaseModel):
    experiment_id: UUID
    action_id: UUID
    play_id: UUID
    platform: str
    action_type: str
    experiment_status: str
    action_status: str
    adapter_outcome: str | None = None
    budget_cap: float | None = Field(default=None, ge=0)


class AutonomyTimelineItem(BaseModel):
    id: UUID
    kind: AutonomyTimelineKind
    recorded_at: datetime
    mandate_id: UUID
    mandate_version: int = Field(ge=1)
    experiment_id: UUID | None = None
    action_id: UUID | None = None
    platform: str | None = None
    outcome: str
    decision: str | None = None
    budget: float | None = Field(default=None, ge=0)
    reasons: list[str] = Field(default_factory=list)


class AutonomyOverviewView(BaseModel):
    product_id: UUID
    mandate: GrowthMandateView | None = None
    budget_exposure: AutonomousPaidBudgetExposure
    remaining_total_budget: float = Field(ge=0)
    remaining_daily_budget: float = Field(ge=0)
    running_experiments: list[AutonomyExperimentSummary]
    waiting_approval: list[AutonomyExperimentSummary]
    recent_decisions: list[AutonomyTimelineItem]


class AutonomyOverviewService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def get(self, product_id: UUID, *, timeline_limit: int = 30) -> AutonomyOverviewView:
        if timeline_limit < 1 or timeline_limit > 100:
            raise ValueError("timeline_limit must be between 1 and 100")
        mandate = self._get_mandate(product_id)
        exposure = autonomous_paid_activation_coordinator.budget_exposure(product_id)
        running: list[AutonomyExperimentSummary] = []
        waiting: list[AutonomyExperimentSummary] = []
        for experiment in distribution_execution_service.list_experiments(product_id):
            if experiment.status not in {
                DistributionExperimentStatus.DRAFT,
                DistributionExperimentStatus.APPROVED,
                DistributionExperimentStatus.RUNNING,
            }:
                continue
            try:
                summary = self._experiment_summary(experiment.id)
            except KeyError:
                continue
            if experiment.status == DistributionExperimentStatus.RUNNING:
                running.append(summary)
            else:
                waiting.append(summary)
        running.sort(key=lambda item: (item.platform, str(item.experiment_id)))
        waiting.sort(key=lambda item: (item.platform, str(item.experiment_id)))

        if mandate is None:
            remaining_total = 0.0
            remaining_daily = 0.0
        else:
            remaining_total = max(
                round(
                    mandate.total_budget_cap - exposure.total_exposure_before_proposal,
                    2,
                ),
                0.0,
            )
            remaining_daily = max(
                round(
                    mandate.max_autonomous_spend_per_day
                    - exposure.daily_exposure_before_proposal,
                    2,
                ),
                0.0,
            )
        return AutonomyOverviewView(
            product_id=product_id,
            mandate=mandate,
            budget_exposure=exposure,
            remaining_total_budget=remaining_total,
            remaining_daily_budget=remaining_daily,
            running_experiments=running,
            waiting_approval=waiting,
            recent_decisions=self._timeline(product_id, timeline_limit),
        )

    def _get_mandate(self, product_id: UUID) -> GrowthMandateView | None:
        try:
            return growth_mandate_service.get(product_id)
        except KeyError:
            return None

    def _experiment_summary(self, experiment_id: UUID) -> AutonomyExperimentSummary:
        experiment = distribution_execution_service.get_experiment(experiment_id)
        action = distribution_execution_service.get_action(experiment.action_id)
        receipt = distribution_execution_adapter_service.get_receipt(action.id)
        spec = paid_campaign_spec_service.get(action.id)
        return AutonomyExperimentSummary(
            experiment_id=experiment.id,
            action_id=action.id,
            play_id=experiment.distribution_play_id,
            platform=action.platform.value,
            action_type=action.action_type.value,
            experiment_status=experiment.status.value,
            action_status=action.status.value,
            adapter_outcome=receipt.outcome.value if receipt is not None else None,
            budget_cap=round(spec.budget_cap, 2) if spec is not None else None,
        )

    def _timeline(self, product_id: UUID, limit: int) -> list[AutonomyTimelineItem]:
        items: list[AutonomyTimelineItem] = []
        for payload in self._store.list_namespace(AUTONOMOUS_GROWTH_DECISION_NAMESPACE):
            try:
                row = AutonomousGrowthDecisionView.model_validate(payload)
            except ValueError:
                continue
            if row.product_id != product_id:
                continue
            items.append(
                AutonomyTimelineItem(
                    id=row.id,
                    kind=AutonomyTimelineKind.LAUNCH,
                    recorded_at=row.recorded_at,
                    mandate_id=row.mandate_id,
                    mandate_version=row.mandate_version,
                    experiment_id=row.experiment_id,
                    action_id=row.action_id,
                    platform=row.platform,
                    outcome=row.outcome.value,
                    decision=(
                        row.evaluation_decision.value
                        if row.evaluation_decision is not None
                        else None
                    ),
                    budget=row.proposed_budget,
                    reasons=row.reasons,
                )
            )
        for payload in self._store.list_namespace(
            AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE
        ):
            try:
                row = AutonomousPaidActivationAuditView.model_validate(payload)
            except ValueError:
                continue
            if row.product_id != product_id:
                continue
            items.append(
                AutonomyTimelineItem(
                    id=row.id,
                    kind=AutonomyTimelineKind.PAID_ACTIVATION,
                    recorded_at=row.recorded_at,
                    mandate_id=row.mandate_id,
                    mandate_version=row.mandate_version,
                    experiment_id=row.experiment_id,
                    action_id=row.action_id,
                    platform=row.platform.value,
                    outcome=row.outcome.value,
                    budget=row.exact_budget_cap,
                    reasons=row.reasons,
                )
            )
        for payload in self._store.list_namespace(AUTONOMOUS_GROWTH_CONTROL_AUDIT_NAMESPACE):
            try:
                row = AutonomousGrowthControlAuditView.model_validate(payload)
            except ValueError:
                continue
            if row.product_id != product_id:
                continue
            items.append(
                AutonomyTimelineItem(
                    id=row.id,
                    kind=AutonomyTimelineKind.GROWTH_CONTROL,
                    recorded_at=row.recorded_at,
                    mandate_id=row.mandate_id,
                    mandate_version=row.mandate_version,
                    experiment_id=row.experiment_id,
                    action_id=row.action_id,
                    platform=row.platform.value,
                    outcome=row.outcome.value,
                    decision=row.growth_action,
                    reasons=row.reasons,
                )
            )
        items.sort(key=lambda item: (item.recorded_at, str(item.id)), reverse=True)
        return items[:limit]


autonomy_overview_service = AutonomyOverviewService()
