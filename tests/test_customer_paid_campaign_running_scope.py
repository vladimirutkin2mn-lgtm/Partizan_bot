from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.customer_paid_campaign_lifecycle import (
    AUTOPILOT_CUSTOMER_PAUSE_REASON,
    CustomerPaidCampaignLifecycleService,
)
from app.distribution_execution_schemas import (
    DistributionExperimentStatus,
    DistributionExperimentView,
)
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    DISTRIBUTION_EXPERIMENT_NAMESPACE,
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
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    ExecutionAdapterReceipt,
)
from app.runtime_store import MemoryRuntimeStateStore

PRODUCT_ID = UUID("11111111-1111-1111-1111-111111111111")
RUNNING_EXPERIMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
FINISHED_EXPERIMENT_ID = UUID("33333333-3333-3333-3333-333333333333")
RUNNING_ACTION_ID = UUID("44444444-4444-4444-4444-444444444444")
FINISHED_ACTION_ID = UUID("55555555-5555-5555-5555-555555555555")
OPPORTUNITY_ID = UUID("66666666-6666-6666-6666-666666666666")
PLAY_ID = UUID("77777777-7777-7777-7777-777777777777")


class FakeController:
    provider = "fake-provider"

    def __init__(self) -> None:
        self.pause_calls: list[UUID] = []
        self.snapshots: dict[UUID, SimpleNamespace] = {}

    def get(self, action_id: UUID):
        return self.snapshots.get(action_id)

    def pause(self, action_id: UUID, *, reason: str):
        self.pause_calls.append(action_id)
        snapshot = SimpleNamespace(
            pause_state="CONFIRMED",
            pause_reason=reason,
            requires_reconciliation=False,
            budget_guardrail_triggered=False,
        )
        self.snapshots[action_id] = snapshot
        return snapshot

    def resume_customer_pause(self, action_id: UUID, *, expected_reason: str):
        raise AssertionError("resume should not be called by this test")


def _experiment(experiment_id: UUID, action_id: UUID, status: DistributionExperimentStatus):
    return DistributionExperimentView(
        id=experiment_id,
        product_id=PRODUCT_ID,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        action_id=action_id,
        status=status,
        attribution_level=AttributionLevel.PAID,
        tracking_url=f"https://example.com/{experiment_id}",
        referral_token=experiment_id.hex[:16],
    )


def _action(action_id: UUID, experiment_id: UUID):
    return DistributionActionView(
        id=action_id,
        platform=DistributionPlatform.INSTAGRAM,
        opportunity_id=OPPORTUNITY_ID,
        experiment_id=experiment_id,
        action_type=DistributionActionType.PAID_CAMPAIGN,
        status=DistributionActionStatus.EXECUTED,
        automation_level=AutomationLevel.FULL,
        attribution_level=AttributionLevel.PAID,
        tracking_url=f"https://example.com/{experiment_id}",
        executed_at=datetime.now(UTC),
    )


def _receipt(action_id: UUID):
    return ExecutionAdapterReceipt(
        action_id=action_id,
        adapter_name="fake-paid",
        provider="fake-provider",
        outcome=AdapterExecutionOutcome.EXECUTED,
        message="active",
        external_reference=f"fake:{action_id}",
        metadata={"spend_state": "ACTIVE"},
        created_at=datetime.now(UTC),
    )


def test_customer_pause_only_controls_running_paid_experiments() -> None:
    store = MemoryRuntimeStateStore()
    controller = FakeController()
    service = CustomerPaidCampaignLifecycleService(
        store=store,
        controllers={controller.provider: controller},
    )

    running_experiment = _experiment(
        RUNNING_EXPERIMENT_ID,
        RUNNING_ACTION_ID,
        DistributionExperimentStatus.RUNNING,
    )
    finished_experiment = _experiment(
        FINISHED_EXPERIMENT_ID,
        FINISHED_ACTION_ID,
        DistributionExperimentStatus.FINISHED,
    )
    for experiment in (running_experiment, finished_experiment):
        store.put(
            DISTRIBUTION_EXPERIMENT_NAMESPACE,
            str(experiment.id),
            experiment.model_dump(mode="json"),
        )
    for action in (
        _action(RUNNING_ACTION_ID, RUNNING_EXPERIMENT_ID),
        _action(FINISHED_ACTION_ID, FINISHED_EXPERIMENT_ID),
    ):
        store.put(
            DISTRIBUTION_ACTION_NAMESPACE,
            str(action.id),
            action.model_dump(mode="json"),
        )
        receipt = _receipt(action.id)
        store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(action.id),
            receipt.model_dump(mode="json"),
        )

    result = service.pause_product(PRODUCT_ID)

    assert result.candidate_count == 1
    assert result.customer_paused_action_ids == [RUNNING_ACTION_ID]
    assert controller.pause_calls == [RUNNING_ACTION_ID]
    assert FINISHED_ACTION_ID not in controller.snapshots
    assert controller.snapshots[RUNNING_ACTION_ID].pause_reason == AUTOPILOT_CUSTOMER_PAUSE_REASON
