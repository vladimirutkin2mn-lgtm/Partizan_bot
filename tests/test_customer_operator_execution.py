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
from app.customer_operator_execution import CustomerOperatorExecutionService
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
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecutionView,
    ExecutionAdapterReceipt,
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


def _approved_state():
    now = datetime.now(UTC)
    action = DistributionActionView(
        id=ACTION_ID,
        platform=DistributionPlatform.REDDIT,
        opportunity_id=OPPORTUNITY_ID,
        distribution_identity_id=None,
        campaign_slot_id=None,
        experiment_id=EXPERIMENT_ID,
        action_type=DistributionActionType.REPLY,
        status=DistributionActionStatus.APPROVED,
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
            "customer_publish_confirmed_at": now.isoformat(),
            "customer_publish_confirmation_fingerprint": fingerprint,
        }
    )
    experiment = DistributionExperimentView(
        id=EXPERIMENT_ID,
        product_id=PRODUCT_ID,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        action_id=ACTION_ID,
        status=DistributionExperimentStatus.APPROVED,
        attribution_level=AttributionLevel.ACTION,
        tracking_url="https://example.com/track",
        referral_token="customer-execution-test",
    )
    request = CustomerExecutionRequestView(
        id=REQUEST_ID,
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        platform=DistributionPlatform.REDDIT,
        publisher_mode=PublisherMode.MANUAL,
        status="OPERATOR_APPROVED",
        source_title="Freelancer bookkeeping discussion",
        source_url=SOURCE_URL,
        draft_title=TITLE,
        context_text=CONTEXT,
        content_text=CONTENT,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        preparation_ready_at=now,
        distribution_action_id=ACTION_ID,
        experiment_id=EXPERIMENT_ID,
        action_prepared_at=now,
        customer_publish_confirmed_at=now,
        customer_publish_confirmation_fingerprint=fingerprint,
        operator_approved_at=now,
        execution_allowed=False,
        customer_publish_confirmation_required=True,
        requested_at=now,
    )
    return request, DistributionExecutionPlanView(action=action, experiment=experiment)


class _FakeExecutionService:
    def __init__(self, plan: DistributionExecutionPlanView) -> None:
        self.plan = plan
        self.mark_executed_calls = 0

    def get_plan(self, action_id: UUID) -> DistributionExecutionPlanView:
        if action_id != self.plan.action.id:
            raise KeyError(action_id)
        return self.plan

    def mark_executed(self, action_id: UUID, _payload) -> DistributionExecutionPlanView:
        if action_id != self.plan.action.id:
            raise KeyError(action_id)
        self.mark_executed_calls += 1
        self.plan = DistributionExecutionPlanView(
            action=self.plan.action.model_copy(update={"status": DistributionActionStatus.EXECUTED}),
            experiment=self.plan.experiment.model_copy(
                update={"status": DistributionExperimentStatus.RUNNING}
            ),
        )
        return self.plan


class _FakeAdapterService:
    def __init__(
        self,
        execution_service: _FakeExecutionService,
        receipt: ExecutionAdapterReceipt | None = None,
    ) -> None:
        self.execution_service = execution_service
        self.receipt = receipt
        self.execute_calls = 0

    def get_receipt(self, action_id: UUID) -> ExecutionAdapterReceipt | None:
        if action_id != ACTION_ID:
            raise KeyError(action_id)
        return self.receipt

    def execute(self, action_id: UUID, payload) -> DistributionAdapterExecutionView:
        assert payload.retry is False
        self.execute_calls += 1
        self.receipt = ExecutionAdapterReceipt(
            action_id=action_id,
            adapter_name="assisted-community",
            provider="operator",
            outcome=AdapterExecutionOutcome.ASSISTED,
            message="Operator handoff required; no automatic provider mutation exists.",
            requires_operator_confirmation=True,
            created_at=datetime.now(UTC),
        )
        return DistributionAdapterExecutionView(
            receipt=self.receipt,
            plan=self.execution_service.plan,
        )


def _receipt(outcome: AdapterExecutionOutcome) -> ExecutionAdapterReceipt:
    return ExecutionAdapterReceipt(
        action_id=ACTION_ID,
        adapter_name="customer-test-adapter",
        provider="test-provider",
        outcome=outcome,
        message="Durable execution attempt receipt.",
        external_reference="provider:123" if outcome == AdapterExecutionOutcome.EXECUTED else None,
        created_at=datetime.now(UTC),
    )


def _service(receipt: ExecutionAdapterReceipt | None = None):
    store = MemoryRuntimeStateStore()
    request, plan = _approved_state()
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
    adapter_service = _FakeAdapterService(execution_service, receipt)
    service = CustomerOperatorExecutionService(
        request_service=request_service,
        execution_service=execution_service,
        approval_service=approval_service,
        adapter_service=adapter_service,
    )
    return request_service, execution_service, adapter_service, service


def test_customer_bound_execution_is_one_shot_and_request_bound() -> None:
    _, _, adapter_service, service = _service()

    first = service.execute(REQUEST_ID)
    second = service.execute(REQUEST_ID)

    assert first.request_id == REQUEST_ID
    assert first.distribution_action_id == ACTION_ID
    assert first.receipt is not None
    assert first.receipt.outcome == AdapterExecutionOutcome.ASSISTED
    assert first.retry_allowed is False
    assert second.receipt == first.receipt
    assert adapter_service.execute_calls == 1


def test_customer_bound_execution_rejects_changed_exact_content_before_adapter() -> None:
    _, execution_service, adapter_service, service = _service()
    execution_service.plan = execution_service.plan.model_copy(
        update={
            "action": execution_service.plan.action.model_copy(
                update={"content_text": "Changed after exact customer confirmation."}
            )
        }
    )

    with pytest.raises(ValueError, match="content no longer matches"):
        service.execute(REQUEST_ID)

    assert adapter_service.execute_calls == 0


def test_customer_bound_execution_never_retries_existing_in_progress_receipt() -> None:
    _, _, adapter_service, service = _service(_receipt(AdapterExecutionOutcome.IN_PROGRESS))

    first = service.execute(REQUEST_ID)
    second = service.execute(REQUEST_ID)

    assert first.receipt is not None
    assert first.receipt.outcome == AdapterExecutionOutcome.IN_PROGRESS
    assert second.receipt == first.receipt
    assert adapter_service.execute_calls == 0


def test_customer_bound_execution_recovers_executed_receipt_without_provider_retry() -> None:
    _, execution_service, adapter_service, service = _service(
        _receipt(AdapterExecutionOutcome.EXECUTED)
    )

    result = service.execute(REQUEST_ID)

    assert adapter_service.execute_calls == 0
    assert execution_service.mark_executed_calls == 1
    assert result.action_status == DistributionActionStatus.EXECUTED
    assert result.experiment_status == DistributionExperimentStatus.RUNNING
    assert result.receipt is not None
    assert result.receipt.outcome == AdapterExecutionOutcome.EXECUTED
