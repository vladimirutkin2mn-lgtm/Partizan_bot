from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.channel_execution import PublisherMode
from app.customer_execution_boundary import require_customer_bound_mutation_scope
from app.customer_execution_request_schemas import CustomerExecutionRequestView
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
from app.execution_adapters import AdapterExecutionOutcome, ExecutionAdapterReceipt
from app.tiktok_direct_post_reconciliation import (
    TikTokDirectPostReconciliationStatus,
    TikTokDirectPostReconciliationView,
    TikTokProviderPostStatus,
)

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRODUCT_ID = UUID("33333333-3333-4333-8333-333333333333")
PLAY_ID = UUID("44444444-4444-4444-8444-444444444444")
OPPORTUNITY_ID = UUID("55555555-5555-4555-8555-555555555555")
ACTION_ID = UUID("66666666-6666-4666-8666-666666666666")
EXPERIMENT_ID = UUID("77777777-7777-4777-8777-777777777777")
ATTEMPT_ID = UUID("88888888-8888-4888-8888-888888888888")
SOURCE_URL = "https://www.tiktok.com/@partizan/video/123456789"
PUBLISH_ID = "v_pub_url~v2.customer-request"


def _approved_state() -> tuple[CustomerExecutionRequestView, DistributionExecutionPlanView]:
    now = datetime.now(UTC)
    action = DistributionActionView(
        id=ACTION_ID,
        platform=DistributionPlatform.TIKTOK,
        opportunity_id=OPPORTUNITY_ID,
        distribution_identity_id=UUID("99999999-9999-4999-8999-999999999999"),
        campaign_slot_id=None,
        experiment_id=EXPERIMENT_ID,
        action_type=DistributionActionType.ORGANIC_VIDEO,
        status=DistributionActionStatus.APPROVED,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.ACTION,
        target_url=SOURCE_URL,
        content_text="Exact customer-confirmed TikTok video caption and creative brief.",
        content_payload={
            "title": "Exact TikTok video",
            "context_text": "Customer-confirmed TikTok execution context.",
        },
        tracking_url="https://example.com/track",
        operational_metadata={
            "distribution_play_id": str(PLAY_ID),
            "customer_execution_request_id": str(REQUEST_ID),
            "customer_exact_content_locked": True,
            "customer_publish_confirmation_required": True,
            "customer_publish_confirmed_at": now.isoformat(),
            "customer_publish_confirmation_fingerprint": "a" * 64,
        },
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
        referral_token="customer-tiktok-reconciliation",
    )
    request = CustomerExecutionRequestView(
        id=REQUEST_ID,
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        platform=DistributionPlatform.TIKTOK,
        publisher_mode=PublisherMode.CLIENT_OWNED,
        status="OPERATOR_APPROVED",
        source_title="Exact TikTok execution",
        source_url=SOURCE_URL,
        draft_title="Exact TikTok video",
        context_text="Customer-confirmed TikTok execution context.",
        content_text="Exact customer-confirmed TikTok video caption and creative brief.",
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        preparation_ready_at=now,
        distribution_action_id=ACTION_ID,
        experiment_id=EXPERIMENT_ID,
        action_prepared_at=now,
        customer_publish_confirmed_at=now,
        customer_publish_confirmation_fingerprint="a" * 64,
        operator_approved_at=now,
        execution_allowed=False,
        customer_publish_confirmation_required=True,
        requested_at=now,
    )
    return request, DistributionExecutionPlanView(action=action, experiment=experiment)


class _RequestService:
    def __init__(self, request: CustomerExecutionRequestView) -> None:
        self.request = request

    def get_request(self, request_id: UUID) -> CustomerExecutionRequestView:
        assert request_id == self.request.id
        return self.request


class _ApprovalService:
    def __init__(self) -> None:
        self.validation_calls = 0

    def validate_exact_confirmation(self, *, request, plan) -> None:
        assert request.id == REQUEST_ID
        assert plan.action.id == ACTION_ID
        self.validation_calls += 1


class _ExecutionService:
    def __init__(self, plan: DistributionExecutionPlanView) -> None:
        self.plan = plan
        self.mark_calls = 0

    def get_plan(self, action_id: UUID) -> DistributionExecutionPlanView:
        assert action_id == ACTION_ID
        return self.plan

    def mark_executed(self, action_id: UUID, payload) -> DistributionExecutionPlanView:
        assert action_id == ACTION_ID
        require_customer_bound_mutation_scope(self.plan.action, "completion")
        assert payload.external_reference == PUBLISH_ID
        self.mark_calls += 1
        self.plan = DistributionExecutionPlanView(
            action=self.plan.action.model_copy(
                update={
                    "status": DistributionActionStatus.EXECUTED,
                    "executed_at": datetime.now(UTC),
                }
            ),
            experiment=self.plan.experiment.model_copy(
                update={"status": DistributionExperimentStatus.RUNNING}
            ),
        )
        return self.plan


class _AdapterService:
    def __init__(self, receipt: ExecutionAdapterReceipt) -> None:
        self.receipt = receipt
        self.execute_calls = 0

    def get_receipt(self, action_id: UUID) -> ExecutionAdapterReceipt:
        assert action_id == ACTION_ID
        return self.receipt

    def execute(self, *_args, **_kwargs):
        self.execute_calls += 1
        raise AssertionError("Read-only reconciliation must never rerun the execution adapter")


class _ReconciliationService:
    def __init__(self, result: TikTokDirectPostReconciliationView) -> None:
        self.result = result
        self.reconcile_calls: list[tuple[UUID, bool]] = []
        self.get_latest_calls: list[UUID] = []

    def reconcile(self, action_id: UUID, *, mark_executed: bool):
        self.reconcile_calls.append((action_id, mark_executed))
        return self.result

    def get_latest(self, action_id: UUID):
        self.get_latest_calls.append(action_id)
        return self.result


def _receipt() -> ExecutionAdapterReceipt:
    return ExecutionAdapterReceipt(
        action_id=ACTION_ID,
        adapter_name="tiktok-permissioned-organic-video",
        provider="tiktok-content-posting-api",
        outcome=AdapterExecutionOutcome.IN_PROGRESS,
        message="TikTok Direct Post is processing.",
        external_reference=PUBLISH_ID,
        metadata={"direct_post_attempt_id": str(ATTEMPT_ID)},
        created_at=datetime.now(UTC),
    )


def _reconciliation(
    status: TikTokDirectPostReconciliationStatus,
) -> TikTokDirectPostReconciliationView:
    provider_status = {
        TikTokDirectPostReconciliationStatus.PROCESSING: (
            TikTokProviderPostStatus.PROCESSING_DOWNLOAD
        ),
        TikTokDirectPostReconciliationStatus.PUBLISHED: TikTokProviderPostStatus.PUBLISH_COMPLETE,
        TikTokDirectPostReconciliationStatus.FAILED: TikTokProviderPostStatus.FAILED,
    }[status]
    return TikTokDirectPostReconciliationView(
        action_id=ACTION_ID,
        attempt_id=ATTEMPT_ID,
        provider_publish_id=PUBLISH_ID,
        status=status,
        provider_status=provider_status,
        fail_reason="provider_publish_failed"
        if status == TikTokDirectPostReconciliationStatus.FAILED
        else None,
        public_post_ids=["741852963"]
        if status == TikTokDirectPostReconciliationStatus.PUBLISHED
        else [],
        checked_at=datetime.now(UTC),
    )


def _service(status: TikTokDirectPostReconciliationStatus):
    request, plan = _approved_state()
    approval = _ApprovalService()
    execution = _ExecutionService(plan)
    adapter = _AdapterService(_receipt())
    reconciliation = _ReconciliationService(_reconciliation(status))
    service = CustomerOperatorExecutionService(
        request_service=_RequestService(request),  # type: ignore[arg-type]
        execution_service=execution,  # type: ignore[arg-type]
        approval_service=approval,  # type: ignore[arg-type]
        adapter_service=adapter,  # type: ignore[arg-type]
        tiktok_reconciliation_service=reconciliation,  # type: ignore[arg-type]
    )
    return approval, execution, adapter, reconciliation, service


def test_published_tiktok_receipt_reconciles_local_state_without_provider_retry() -> None:
    approval, execution, adapter, reconciliation, service = _service(
        TikTokDirectPostReconciliationStatus.PUBLISHED
    )

    result = service.view(REQUEST_ID)

    assert reconciliation.reconcile_calls == [(ACTION_ID, False)]
    assert adapter.execute_calls == 0
    assert execution.mark_calls == 1
    assert approval.validation_calls == 2
    assert result.action_status == DistributionActionStatus.EXECUTED
    assert result.experiment_status == DistributionExperimentStatus.RUNNING
    assert result.retry_allowed is False
    assert result.receipt is not None
    assert result.receipt.outcome == AdapterExecutionOutcome.EXECUTED
    assert result.receipt.external_reference == PUBLISH_ID
    assert result.receipt.metadata["public_post_ids"] == ["741852963"]


def test_processing_tiktok_receipt_only_polls_status_and_stays_approved() -> None:
    _, execution, adapter, reconciliation, service = _service(
        TikTokDirectPostReconciliationStatus.PROCESSING
    )

    result = service.view(REQUEST_ID)

    assert reconciliation.reconcile_calls == [(ACTION_ID, False)]
    assert adapter.execute_calls == 0
    assert execution.mark_calls == 0
    assert result.action_status == DistributionActionStatus.APPROVED
    assert result.experiment_status == DistributionExperimentStatus.APPROVED
    assert result.retry_allowed is False
    assert result.receipt is not None
    assert result.receipt.outcome == AdapterExecutionOutcome.IN_PROGRESS
    assert result.receipt.metadata["provider_status"] == "PROCESSING_DOWNLOAD"


def test_failed_tiktok_receipt_never_retries_or_marks_executed() -> None:
    _, execution, adapter, reconciliation, service = _service(
        TikTokDirectPostReconciliationStatus.FAILED
    )

    result = service.view(REQUEST_ID)

    assert reconciliation.reconcile_calls == [(ACTION_ID, False)]
    assert adapter.execute_calls == 0
    assert execution.mark_calls == 0
    assert result.retry_allowed is False
    assert result.receipt is not None
    assert result.receipt.outcome == AdapterExecutionOutcome.FAILED
    assert result.receipt.requires_operator_confirmation is True
    assert result.receipt.metadata["provider_fail_reason"] == "provider_publish_failed"


def test_second_view_of_published_action_uses_durable_reconciliation_without_polling_again() -> None:
    _, execution, adapter, reconciliation, service = _service(
        TikTokDirectPostReconciliationStatus.PUBLISHED
    )

    first = service.view(REQUEST_ID)
    second = service.view(REQUEST_ID)

    assert first.action_status == DistributionActionStatus.EXECUTED
    assert second.action_status == DistributionActionStatus.EXECUTED
    assert reconciliation.reconcile_calls == [(ACTION_ID, False)]
    assert reconciliation.get_latest_calls == [ACTION_ID]
    assert adapter.execute_calls == 0
    assert execution.mark_calls == 1
    assert second.receipt is not None
    assert second.receipt.outcome == AdapterExecutionOutcome.EXECUTED


def test_request_bound_tiktok_reconciliation_has_no_submit_or_retry_path() -> None:
    source = Path("app/customer_operator_execution.py").read_text(encoding="utf-8")

    assert "self._tiktok_reconciliation_service.reconcile(" in source
    assert "mark_executed=False" in source
    assert "with customer_execution_request_scope(request.id):" in source
    assert "DistributionAdapterExecuteRequest(retry=False)" in source
    assert "retry=True" not in source
    assert ".submit(" not in source
