from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.analytics_schemas import ExperimentMetricsView
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_execution_schemas import DistributionExperimentView
from app.distribution_execution_service import distribution_execution_service
from app.distribution_schemas import DistributionActionView
from app.outreach_briefs import OutreachBriefView, outreach_brief_service
from app.outreach_policy import OutreachPolicyView, outreach_policy_service
from app.outreach_sender import (
    OutreachSenderReadinessView,
    OutreachSendAttemptStatus,
    OutreachSendAttemptView,
    outreach_sender_service,
)
from app.outreach_targets import OutreachTargetView, outreach_target_service


class OutreachWorkspaceItemView(BaseModel):
    target: OutreachTargetView
    brief: OutreachBriefView | None = None
    action: DistributionActionView | None = None
    experiment: DistributionExperimentView | None = None
    send_attempt: OutreachSendAttemptView | None = None
    metrics: ExperimentMetricsView | None = None


class OutreachWorkspaceView(BaseModel):
    product_id: UUID
    sender: OutreachSenderReadinessView
    policy: OutreachPolicyView | None = None
    target_count: int = Field(ge=0)
    draft_count: int = Field(ge=0)
    sent_count: int = Field(ge=0)
    reconciliation_count: int = Field(ge=0)
    paid_users: int = Field(ge=0)
    revenue: float = Field(ge=0)
    items: list[OutreachWorkspaceItemView]


class OutreachWorkspaceService:
    def get(self, product_id: UUID) -> OutreachWorkspaceView:
        targets = outreach_target_service.list_product(product_id).targets
        policy = outreach_policy_service.get_optional(product_id)
        items = [self._item(target) for target in targets]
        return OutreachWorkspaceView(
            product_id=product_id,
            sender=outreach_sender_service.readiness(),
            policy=policy,
            target_count=len(items),
            draft_count=sum(
                item.brief is not None
                and item.send_attempt is None
                and item.experiment is not None
                and item.experiment.status.value in {"DRAFT", "APPROVED"}
                for item in items
            ),
            sent_count=sum(
                item.send_attempt is not None
                and item.send_attempt.status == OutreachSendAttemptStatus.SENT
                for item in items
            ),
            reconciliation_count=sum(
                item.send_attempt is not None
                and item.send_attempt.status == OutreachSendAttemptStatus.RECONCILIATION_REQUIRED
                for item in items
            ),
            paid_users=sum(item.metrics.paid_users for item in items if item.metrics is not None),
            revenue=round(
                sum(item.metrics.revenue for item in items if item.metrics is not None),
                2,
            ),
            items=items,
        )

    def _item(self, target: OutreachTargetView) -> OutreachWorkspaceItemView:
        briefs = outreach_brief_service.list_target(target.id).briefs
        brief = max(briefs, key=lambda item: (item.updated_at, str(item.id))) if briefs else None
        if brief is None:
            return OutreachWorkspaceItemView(target=target)
        try:
            action = distribution_execution_service.get_action(brief.action_id)
            experiment = distribution_execution_service.get_experiment(brief.experiment_id)
        except KeyError:
            return OutreachWorkspaceItemView(target=target, brief=brief)
        send_attempt = outreach_sender_service.get_attempt(brief.id)
        try:
            metrics = distribution_analytics_service.experiment_analytics(experiment.id).metrics
        except (KeyError, ValueError):
            metrics = None
        return OutreachWorkspaceItemView(
            target=target,
            brief=brief,
            action=action,
            experiment=experiment,
            send_attempt=send_attempt,
            metrics=metrics,
        )


outreach_workspace_service = OutreachWorkspaceService()
