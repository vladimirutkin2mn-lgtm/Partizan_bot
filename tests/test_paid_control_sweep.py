from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.distribution_execution_service import DISTRIBUTION_ACTION_NAMESPACE
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
from app.paid_control_sweep import (
    PaidControlSweepItemOutcome,
    PaidControlSweepItemView,
    PaidControlSweepRegistry,
    PaidControlSweepService,
)
from app.paid_control_worker import PaidControlWorker
from app.runtime_store import MemoryRuntimeStateStore


class DurableMemoryStore(MemoryRuntimeStateStore):
    ephemeral = False


class FakeSweepProvider:
    def __init__(self, provider: str, *, fail: bool = False) -> None:
        self.provider = provider
        self.fail = fail
        self.calls: list[UUID] = []

    def sync(self, action_id: UUID) -> PaidControlSweepItemView:
        self.calls.append(action_id)
        if self.fail:
            raise ValueError(f"{self.provider} sync failed")
        return PaidControlSweepItemView(
            action_id=action_id,
            provider=self.provider,
            outcome=PaidControlSweepItemOutcome.SYNCED,
            provider_status="ACTIVE",
            provider_spend=12.0,
            synced_spend=12.0,
            pause_state="NOT_REQUESTED",
        )


def _action(
    platform: DistributionPlatform,
    *,
    action_type: DistributionActionType = DistributionActionType.PAID_CAMPAIGN,
    status: DistributionActionStatus = DistributionActionStatus.EXECUTED,
) -> DistributionActionView:
    return DistributionActionView(
        id=uuid4(),
        platform=platform,
        opportunity_id=uuid4(),
        experiment_id=uuid4(),
        action_type=action_type,
        status=status,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        tracking_url="https://example.com/tracked",
        operational_metadata={"tactic_id": "test_paid"},
        executed_at=datetime.now(UTC) if status == DistributionActionStatus.EXECUTED else None,
    )


def _persist_action(store: MemoryRuntimeStateStore, action: DistributionActionView) -> None:
    store.put(
        DISTRIBUTION_ACTION_NAMESPACE,
        str(action.id),
        action.model_dump(mode="json"),
    )


def _persist_receipt(
    store: MemoryRuntimeStateStore,
    action: DistributionActionView,
    provider: str,
) -> None:
    receipt = ExecutionAdapterReceipt(
        action_id=action.id,
        adapter_name=f"{provider}-adapter",
        provider=provider,
        outcome=AdapterExecutionOutcome.EXECUTED,
        message="provider execution",
        metadata={"provider_ids": {"campaign_id": "cmp"}},
        created_at=datetime.now(UTC),
    )
    store.put(
        EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
        str(action.id),
        receipt.model_dump(mode="json"),
    )


def test_sweep_routes_meta_and_tiktok_and_skips_unsupported_provider() -> None:
    store = MemoryRuntimeStateStore()
    meta_action = _action(DistributionPlatform.INSTAGRAM)
    tiktok_action = _action(
        DistributionPlatform.TIKTOK,
        status=DistributionActionStatus.APPROVED,
    )
    reddit_action = _action(DistributionPlatform.REDDIT)
    organic = _action(
        DistributionPlatform.INSTAGRAM,
        action_type=DistributionActionType.COMMENT,
        status=DistributionActionStatus.APPROVED,
    )
    for action in (meta_action, tiktok_action, reddit_action, organic):
        _persist_action(store, action)
    _persist_receipt(store, meta_action, "meta-marketing-api")
    _persist_receipt(store, tiktok_action, "tiktok-marketing-api")
    _persist_receipt(store, reddit_action, "reddit-ads-unavailable")

    meta = FakeSweepProvider("meta-marketing-api")
    tiktok = FakeSweepProvider("tiktok-marketing-api")
    service = PaidControlSweepService(
        store=store,
        registry=PaidControlSweepRegistry([meta, tiktok]),
    )

    result = service.run_once()

    assert result.candidate_count == 3
    assert result.synced_count == 2
    assert result.skipped_count == 1
    assert result.error_count == 0
    assert meta.calls == [meta_action.id]
    assert tiktok.calls == [tiktok_action.id]
    reddit_row = next(item for item in result.items if item.action_id == reddit_action.id)
    assert reddit_row.outcome == PaidControlSweepItemOutcome.SKIPPED
    assert "No autonomous paid-control provider" in (reddit_row.reason or "")
    assert all(item.action_id != organic.id for item in result.items)


def test_one_provider_failure_does_not_abort_remaining_paid_actions() -> None:
    store = MemoryRuntimeStateStore()
    meta_action = _action(DistributionPlatform.INSTAGRAM)
    tiktok_action = _action(DistributionPlatform.TIKTOK)
    for action in (meta_action, tiktok_action):
        _persist_action(store, action)
    _persist_receipt(store, meta_action, "meta-marketing-api")
    _persist_receipt(store, tiktok_action, "tiktok-marketing-api")
    meta = FakeSweepProvider("meta-marketing-api", fail=True)
    tiktok = FakeSweepProvider("tiktok-marketing-api")
    service = PaidControlSweepService(
        store=store,
        registry=PaidControlSweepRegistry([meta, tiktok]),
    )

    result = service.run_once()

    assert result.error_count == 1
    assert result.synced_count == 1
    failed = next(item for item in result.items if item.action_id == meta_action.id)
    assert failed.outcome == PaidControlSweepItemOutcome.ERROR
    assert failed.requires_reconciliation is True
    assert "sync failed" in (failed.reason or "")
    assert tiktok.calls == [tiktok_action.id]


def test_paid_action_without_provider_receipt_is_skipped_not_failed() -> None:
    store = MemoryRuntimeStateStore()
    action = _action(DistributionPlatform.TIKTOK, status=DistributionActionStatus.APPROVED)
    _persist_action(store, action)
    service = PaidControlSweepService(store=store, registry=PaidControlSweepRegistry([]))

    result = service.run_once()

    assert result.candidate_count == 1
    assert result.skipped_count == 1
    assert result.error_count == 0
    assert "no execution-provider receipt" in (result.items[0].reason or "")


def test_recurring_worker_refuses_ephemeral_memory_store() -> None:
    service = PaidControlSweepService(
        store=MemoryRuntimeStateStore(),
        registry=PaidControlSweepRegistry([]),
    )
    worker = PaidControlWorker(sweep_service=service, sleep=lambda _: None)

    with pytest.raises(RuntimeError, match="RUNTIME_STORAGE=database"):
        worker.run(once=False, interval_seconds=60, max_runs=1, emit=lambda _: None)


def test_once_worker_allows_memory_and_recurring_durable_worker_repeats() -> None:
    once_service = PaidControlSweepService(
        store=MemoryRuntimeStateStore(),
        registry=PaidControlSweepRegistry([]),
    )
    once_output: list[str] = []
    once_worker = PaidControlWorker(sweep_service=once_service, sleep=lambda _: None)

    assert once_worker.run(
        once=True,
        interval_seconds=60,
        emit=once_output.append,
    ) == 0
    assert len(once_output) == 1

    durable_service = PaidControlSweepService(
        store=DurableMemoryStore(),
        registry=PaidControlSweepRegistry([]),
    )
    sleeps: list[float] = []
    recurring_output: list[str] = []
    recurring_worker = PaidControlWorker(
        sweep_service=durable_service,
        sleep=sleeps.append,
    )

    assert recurring_worker.run(
        once=False,
        interval_seconds=30,
        max_runs=2,
        emit=recurring_output.append,
    ) == 0
    assert len(recurring_output) == 2
    assert sleeps == [30.0]


def test_worker_rejects_too_fast_control_interval() -> None:
    service = PaidControlSweepService(
        store=DurableMemoryStore(),
        registry=PaidControlSweepRegistry([]),
    )
    worker = PaidControlWorker(sweep_service=service, sleep=lambda _: None)

    with pytest.raises(ValueError, match="at least 15"):
        worker.run(once=False, interval_seconds=5, max_runs=1, emit=lambda _: None)
