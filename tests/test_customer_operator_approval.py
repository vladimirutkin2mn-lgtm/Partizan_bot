import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest

from app.channel_execution import PublisherMode
from app.customer_execution_request_schemas import CustomerExecutionRequestView
from app.customer_execution_requests import (
    CUSTOMER_EXECUTION_REQUEST_NAMESPACE,
    CustomerExecutionRequestService,
)
from app.customer_operator_approval import CustomerOperatorApprovalService
from app.distribution_execution_schemas import (
    DistributionExecutionPlanView,
    DistributionExperimentStatus,
    DistributionExperimentView,
)
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.runtime_store import MemoryRuntimeStateStore

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRODUCT_ID = UUID("33333333-3333-4333-8333-333333333333")
PLAY_ID = UUID("44444444-4444-4444-8444-444444444444")
OPPORTUNITY_ID = UUID("55555555-5555-4555-8555-555555555555")
ACTION_ID = UUID("66666666-6666-4666-8666-666666666666")
EXPERIMENT_ID = UUID("77777777-7777-4777-8777-777777777777")
SOURCE_URL = "https://www.reddit.com/r/freelance/comments/example/thread/"
TITLE = "Useful bookkeeping reply"
CONTEXT = "Freelancers are comparing recurring bookkeeping workflow pain."
CONTENT = "Share a useful bookkeeping workflow perspective without a product link."


def _fingerprint(action: DistributionActionView) -> str:
    payload = {
        "request_id": str(REQUEST_ID),
        "action_id": str(action.id),
        "platform": DistributionPlatform.REDDIT.value,
        "target_url": str(action.target_url),
        "title": action.content_payload.get("title"),
        "context_text": action.content_payload.get("context_text"),
        "content_text": action.content_text,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _confirmed_state(*, approved: bool = False):
    confirmed_at = datetime.now(UTC)
    action_status = (
        DistributionActionStatus.APPROVED if approved else DistributionActionStatus.PREPARED
    )
    experiment_status = (
        DistributionExperimentStatus.APPROVED if approved else DistributionExperimentStatus.DRAFT
    )
    action = DistributionActionView(
        id=ACTION_ID,
        platform=DistributionPlatform.REDDIT,
        opportunity_id=OPPORTUNITY_ID,
        distribution_identity_id=None,
        campaign_slot_id=None,
        experiment_id=EXPERIMENT_ID,
        action_type=DistributionActionType.REPLY,
        status=action_status,
        automation_level=AutomationLevel.MANUAL,
        attribution_level=AttributionLevel.ACTION,
        target_url=SOURCE_URL,
        content_text=CONTENT,
        content_payload={"title": TITLE, "context_text": CONTEXT},
        tracking_url="https://example.com/track",
        operational_metadata={
            "distribution_play_id": str(PLAY_ID),
            "customer_execution_request_id": str(REQUEST_ID),
            "customer_exact_content_locked": True,
            "customer_publish_confirmation_required": True,
        },
    )
    fingerprint = _fingerprint(action)
    action.operational_metadata.update(
        {
            "customer_publish_confirmed_at": confirmed_at.isoformat(),
            "customer_publish_confirmation_fingerprint": fingerprint,
        }
    )
    experiment = DistributionExperimentView(
        id=EXPERIMENT_ID,
        product_id=PRODUCT_ID,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        action_id=ACTION_ID,
        status=experiment_status,
        attribution_level=AttributionLevel.ACTION,
        tracking_url="https://example.com/track",
        referral_token="customer-approval-test",
    )
    request = CustomerExecutionRequestView(
        id=REQUEST_ID,
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        platform=DistributionPlatform.REDDIT,
        publisher_mode=PublisherMode.MANUAL,
        status="PUBLISH_CONFIRMED",
        source_title="Freelancer bookkeeping discussion",
        source_url=SOURCE_URL,
        draft_title=TITLE,
        context_text=CONTEXT,
        content_text=CONTENT,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        preparation_ready_at=confirmed_at,
        distribution_action_id=ACTION_ID,
        experiment_id=EXPERIMENT_ID,
        action_prepared_at=confirmed_at,
        customer_publish_confirmed_at=confirmed_at,
        customer_publish_confirmation_fingerprint=fingerprint,
        execution_allowed=False,
        customer_publish_confirmation_required=True,
        requested_at=confirmed_at,
    )
    return request, DistributionExecutionPlanView(action=action, experiment=experiment)


class _FakeExecutionService:
    def __init__(self, plan: DistributionExecutionPlanView) -> None:
        self.plan = plan
        self.approve_calls = 0

    def get_plan(self, action_id: UUID) -> DistributionExecutionPlanView:
        if action_id != self.plan.action.id:
            raise KeyError(action_id)
        return self.plan

    def approve(self, action_id: UUID) -> DistributionExecutionPlanView:
        if action_id != self.plan.action.id:
            raise KeyError(action_id)
        self.approve_calls += 1
        self.plan = DistributionExecutionPlanView(
            action=self.plan.action.model_copy(update={"status": DistributionActionStatus.APPROVED}),
            experiment=self.plan.experiment.model_copy(
                update={"status": DistributionExperimentStatus.APPROVED}
            ),
        )
        return self.plan


def _service(*, approved: bool = False):
    store = MemoryRuntimeStateStore()
    request, plan = _confirmed_state(approved=approved)
    store.put(
        CUSTOMER_EXECUTION_REQUEST_NAMESPACE,
        str(request.id),
        request.model_dump(mode="json"),
    )
    request_service = CustomerExecutionRequestService(store)
    execution_service = _FakeExecutionService(plan)
    approval_service = CustomerOperatorApprovalService(
        request_service=request_service,
        execution_service=execution_service,
    )
    return request_service, execution_service, approval_service


def test_operator_approval_is_request_bound_and_idempotent() -> None:
    request_service, execution_service, approval_service = _service()

    first = approval_service.approve(REQUEST_ID)
    second = approval_service.approve(REQUEST_ID)

    assert first.status == "OPERATOR_APPROVED"
    assert first.operator_approved_at is not None
    assert second.operator_approved_at == first.operator_approved_at
    assert execution_service.approve_calls == 1
    stored = request_service.get_request(REQUEST_ID)
    assert stored.status == "OPERATOR_APPROVED"
    assert stored.execution_allowed is False
    assert execution_service.plan.action.status == DistributionActionStatus.APPROVED
    assert execution_service.plan.experiment.status == DistributionExperimentStatus.APPROVED


def test_operator_approval_rejects_changed_exact_content_before_approve() -> None:
    request_service, execution_service, approval_service = _service()
    execution_service.plan = execution_service.plan.model_copy(
        update={
            "action": execution_service.plan.action.model_copy(
                update={"content_text": "Changed after exact customer confirmation."}
            )
        }
    )

    with pytest.raises(ValueError, match="content no longer matches"):
        approval_service.approve(REQUEST_ID)

    assert execution_service.approve_calls == 0
    assert request_service.get_request(REQUEST_ID).status == "PUBLISH_CONFIRMED"


def test_operator_approval_recovers_after_action_write_without_reapproving() -> None:
    request_service, execution_service, approval_service = _service(approved=True)

    approved = approval_service.approve(REQUEST_ID)

    assert approved.status == "OPERATOR_APPROVED"
    assert approved.operator_approved_at is not None
    assert execution_service.approve_calls == 0
    assert request_service.get_request(REQUEST_ID).status == "OPERATOR_APPROVED"
