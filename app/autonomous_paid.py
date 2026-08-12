from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.autonomy_schemas import AutonomyDecision, AutonomyEvaluationRequest, GrowthMandateView
from app.autonomy_service import GrowthMandateService, growth_mandate_service
from app.distribution_analytics_service import DISTRIBUTION_SPEND_NAMESPACE
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.execution_adapters import AdapterExecutionOutcome, DistributionAdapterExecutionView
from app.paid_activation import (
    PaidActivationAuthorizationRequest,
    PaidActivationRequest,
    PaidActivationService,
    paid_activation_service,
)
from app.paid_campaign import PaidCampaignSpecService, paid_campaign_spec_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_paid_activation import (
    TikTokPaidActivationAuthorizationRequest,
    TikTokPaidActivationRequest,
    TikTokPaidActivationService,
    tiktok_paid_activation_service,
)

AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE = "autonomous_paid_activation_audit"


class AutonomousPaidActivationOutcome(StrEnum):
    ACTIVATED = "ACTIVATED"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class AutonomousPaidBudgetExposure(BaseModel):
    observed_total_spend: float = Field(ge=0)
    observed_daily_spend: float = Field(ge=0)
    reserved_running_paid_budget: float = Field(ge=0)
    total_exposure_before_proposal: float = Field(ge=0)
    daily_exposure_before_proposal: float = Field(ge=0)


class AutonomousPaidActivationAuditView(BaseModel):
    id: UUID
    product_id: UUID
    mandate_id: UUID
    mandate_version: int = Field(ge=1)
    action_id: UUID
    experiment_id: UUID | None = None
    platform: DistributionPlatform
    exact_budget_cap: float = Field(gt=0)
    authorization_id: UUID | None = None
    outcome: AutonomousPaidActivationOutcome
    reasons: list[str] = Field(default_factory=list)
    recorded_at: datetime


class AutonomousPaidActivationResult(BaseModel):
    outcome: AutonomousPaidActivationOutcome
    exact_budget_cap: float = Field(gt=0)
    authorization_id: UUID | None = None
    reasons: list[str] = Field(default_factory=list)
    execution: DistributionAdapterExecutionView | None = None


class MetaActivationBoundary(Protocol):
    def authorize(self, action_id: UUID, payload: PaidActivationAuthorizationRequest): ...

    def activate(self, action_id: UUID, payload: PaidActivationRequest): ...


class TikTokActivationBoundary(Protocol):
    def authorize(self, action_id: UUID, payload: TikTokPaidActivationAuthorizationRequest): ...

    def activate(self, action_id: UUID, payload: TikTokPaidActivationRequest): ...


class AutonomousPaidActivationCoordinator:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        mandate_service: GrowthMandateService | None = None,
        spec_service: PaidCampaignSpecService | None = None,
        meta_activation: PaidActivationService | MetaActivationBoundary | None = None,
        tiktok_activation: TikTokPaidActivationService | TikTokActivationBoundary | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._mandate_service = mandate_service or growth_mandate_service
        self._spec_service = spec_service or paid_campaign_spec_service
        self._meta_activation = meta_activation or paid_activation_service
        self._tiktok_activation = tiktok_activation or tiktok_paid_activation_service

    def activate_staged(
        self,
        *,
        mandate: GrowthMandateView,
        action_id: UUID,
    ) -> AutonomousPaidActivationResult:
        action = distribution_execution_service.get_action(action_id)
        if action.action_type != DistributionActionType.PAID_CAMPAIGN:
            raise ValueError("Autonomous paid activation requires a PAID_CAMPAIGN action")
        if action.platform not in {DistributionPlatform.INSTAGRAM, DistributionPlatform.TIKTOK}:
            raise ValueError("Autonomous paid activation supports Meta/Instagram and TikTok only")
        spec = self._spec_service.get(action_id)
        if spec is None:
            raise ValueError("PaidCampaignSpec is required for autonomous paid activation")
        exact_budget = round(spec.budget_cap, 2)

        evaluation = self._mandate_service.evaluate(
            mandate.product_id,
            AutonomyEvaluationRequest(
                platform=action.platform,
                action_type=DistributionActionType.PAID_CAMPAIGN,
                proposed_budget=exact_budget,
                requires_prepare=False,
                requires_approval=False,
                requests_paid_activation=True,
            ),
        )
        if self._mandate_changed(mandate, evaluation.mandate_id, evaluation.mandate_version):
            return self._finish(
                mandate=mandate,
                action_id=action_id,
                platform=action.platform,
                exact_budget=exact_budget,
                outcome=AutonomousPaidActivationOutcome.BLOCKED,
                reasons=["Growth Mandate changed before paid activation authorization"],
            )
        exposure_reasons = self._exposure_blocks(mandate, exact_budget)
        if exposure_reasons:
            return self._finish(
                mandate=mandate,
                action_id=action_id,
                platform=action.platform,
                exact_budget=exact_budget,
                outcome=AutonomousPaidActivationOutcome.BLOCKED,
                reasons=exposure_reasons,
            )
        if evaluation.decision != AutonomyDecision.ALLOW:
            outcome = (
                AutonomousPaidActivationOutcome.BLOCKED
                if evaluation.decision == AutonomyDecision.BLOCK
                else AutonomousPaidActivationOutcome.REQUIRE_APPROVAL
            )
            return self._finish(
                mandate=mandate,
                action_id=action_id,
                platform=action.platform,
                exact_budget=exact_budget,
                outcome=outcome,
                reasons=evaluation.reasons,
            )

        authorization = self._authorize(action.platform, action_id, exact_budget)
        authorization_id = authorization.id

        final_evaluation = self._mandate_service.evaluate(
            mandate.product_id,
            AutonomyEvaluationRequest(
                platform=action.platform,
                action_type=DistributionActionType.PAID_CAMPAIGN,
                proposed_budget=exact_budget,
                requires_prepare=False,
                requires_approval=False,
                requests_paid_activation=True,
            ),
        )
        final_exposure_reasons = self._exposure_blocks(mandate, exact_budget)
        if self._mandate_changed(
            mandate,
            final_evaluation.mandate_id,
            final_evaluation.mandate_version,
        ):
            return self._finish(
                mandate=mandate,
                action_id=action_id,
                platform=action.platform,
                exact_budget=exact_budget,
                authorization_id=authorization_id,
                outcome=AutonomousPaidActivationOutcome.BLOCKED,
                reasons=[
                    "Growth Mandate changed after authorization; provider remains STAGED and "
                    "the unattempted authorization will expire"
                ],
            )
        if final_exposure_reasons or final_evaluation.decision != AutonomyDecision.ALLOW:
            reasons = final_exposure_reasons or final_evaluation.reasons
            return self._finish(
                mandate=mandate,
                action_id=action_id,
                platform=action.platform,
                exact_budget=exact_budget,
                authorization_id=authorization_id,
                outcome=AutonomousPaidActivationOutcome.BLOCKED,
                reasons=reasons,
            )

        execution = self._activate(action.platform, action_id, authorization_id)
        if execution.receipt.outcome == AdapterExecutionOutcome.EXECUTED:
            return self._finish(
                mandate=mandate,
                action_id=action_id,
                platform=action.platform,
                exact_budget=exact_budget,
                authorization_id=authorization_id,
                outcome=AutonomousPaidActivationOutcome.ACTIVATED,
                reasons=[execution.receipt.message],
                execution=execution,
            )
        return self._finish(
            mandate=mandate,
            action_id=action_id,
            platform=action.platform,
            exact_budget=exact_budget,
            authorization_id=authorization_id,
            outcome=AutonomousPaidActivationOutcome.FAILED,
            reasons=[execution.receipt.message],
            execution=execution,
        )

    def budget_exposure(self, product_id: UUID) -> AutonomousPaidBudgetExposure:
        observed_total = self._observed_spend(product_id, daily=False)
        observed_daily = self._observed_spend(product_id, daily=True)
        reserved = 0.0
        spend_by_experiment = self._spend_by_experiment(product_id)
        for experiment in distribution_execution_service.list_experiments(product_id):
            if experiment.status != DistributionExperimentStatus.RUNNING:
                continue
            try:
                action = distribution_execution_service.get_action(experiment.action_id)
            except KeyError:
                continue
            if action.action_type != DistributionActionType.PAID_CAMPAIGN:
                continue
            spec = self._spec_service.get(action.id)
            if spec is None:
                continue
            already_spent = spend_by_experiment.get(experiment.id, 0.0)
            reserved += max(float(spec.budget_cap) - already_spent, 0.0)
        reserved = round(reserved, 2)
        return AutonomousPaidBudgetExposure(
            observed_total_spend=observed_total,
            observed_daily_spend=observed_daily,
            reserved_running_paid_budget=reserved,
            total_exposure_before_proposal=round(observed_total + reserved, 2),
            daily_exposure_before_proposal=round(observed_daily + reserved, 2),
        )

    def _exposure_blocks(self, mandate: GrowthMandateView, proposed_budget: float) -> list[str]:
        exposure = self.budget_exposure(mandate.product_id)
        reasons: list[str] = []
        if exposure.total_exposure_before_proposal + proposed_budget > mandate.total_budget_cap:
            reasons.append(
                "Observed spend plus reserved RUNNING paid budgets and the proposed campaign "
                "would exceed the total Growth Mandate cap"
            )
        if (
            exposure.daily_exposure_before_proposal + proposed_budget
            > mandate.max_autonomous_spend_per_day
        ):
            reasons.append(
                "Today's spend plus reserved RUNNING paid budgets and the proposed campaign "
                "would exceed the daily autonomous spend cap"
            )
        return reasons

    def _authorize(self, platform: DistributionPlatform, action_id: UUID, budget: float):
        if platform == DistributionPlatform.INSTAGRAM:
            return self._meta_activation.authorize(
                action_id,
                PaidActivationAuthorizationRequest(
                    approved_budget_cap=budget,
                    confirm_spend=True,
                ),
            )
        return self._tiktok_activation.authorize(
            action_id,
            TikTokPaidActivationAuthorizationRequest(
                approved_budget_cap=budget,
                confirm_spend=True,
            ),
        )

    def _activate(
        self,
        platform: DistributionPlatform,
        action_id: UUID,
        authorization_id: UUID,
    ) -> DistributionAdapterExecutionView:
        if platform == DistributionPlatform.INSTAGRAM:
            return self._meta_activation.activate(
                action_id,
                PaidActivationRequest(authorization_id=authorization_id),
            )
        return self._tiktok_activation.activate(
            action_id,
            TikTokPaidActivationRequest(authorization_id=authorization_id),
        )

    def _observed_spend(self, product_id: UUID, *, daily: bool) -> float:
        experiment_ids = {
            experiment.id
            for experiment in distribution_execution_service.list_experiments(product_id)
        }
        today = datetime.now(UTC).date()
        total = 0.0
        for row in self._store.list_namespace(DISTRIBUTION_SPEND_NAMESPACE):
            try:
                experiment_id = UUID(str(row["experiment_id"]))
                amount = float(row.get("amount", 0))
                occurred_at = datetime.fromisoformat(str(row["occurred_at"]))
            except (KeyError, TypeError, ValueError):
                continue
            if experiment_id not in experiment_ids:
                continue
            if daily and self._as_utc(occurred_at).date() != today:
                continue
            total += amount
        return round(total, 2)

    def _spend_by_experiment(self, product_id: UUID) -> dict[UUID, float]:
        experiment_ids = {
            experiment.id
            for experiment in distribution_execution_service.list_experiments(product_id)
        }
        result: dict[UUID, float] = {}
        for row in self._store.list_namespace(DISTRIBUTION_SPEND_NAMESPACE):
            try:
                experiment_id = UUID(str(row["experiment_id"]))
                amount = float(row.get("amount", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if experiment_id in experiment_ids:
                result[experiment_id] = result.get(experiment_id, 0.0) + amount
        return result

    def _mandate_changed(
        self,
        mandate: GrowthMandateView,
        mandate_id: UUID | None,
        mandate_version: int | None,
    ) -> bool:
        return mandate_id != mandate.id or mandate_version != mandate.version

    def _finish(
        self,
        *,
        mandate: GrowthMandateView,
        action_id: UUID,
        platform: DistributionPlatform,
        exact_budget: float,
        outcome: AutonomousPaidActivationOutcome,
        reasons: list[str],
        authorization_id: UUID | None = None,
        execution: DistributionAdapterExecutionView | None = None,
    ) -> AutonomousPaidActivationResult:
        action = distribution_execution_service.get_action(action_id)
        audit = AutonomousPaidActivationAuditView(
            id=uuid4(),
            product_id=mandate.product_id,
            mandate_id=mandate.id,
            mandate_version=mandate.version,
            action_id=action_id,
            experiment_id=action.experiment_id,
            platform=platform,
            exact_budget_cap=exact_budget,
            authorization_id=authorization_id,
            outcome=outcome,
            reasons=[str(reason)[:1000] for reason in reasons],
            recorded_at=datetime.now(UTC),
        )
        self._store.put(
            AUTONOMOUS_PAID_ACTIVATION_AUDIT_NAMESPACE,
            str(audit.id),
            audit.model_dump(mode="json"),
        )
        return AutonomousPaidActivationResult(
            outcome=outcome,
            exact_budget_cap=exact_budget,
            authorization_id=authorization_id,
            reasons=audit.reasons,
            execution=execution,
        )

    def _as_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


autonomous_paid_activation_coordinator = AutonomousPaidActivationCoordinator()
