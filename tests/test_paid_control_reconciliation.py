from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

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
from app.main import app
from app.meta_paid_control import (
    META_PAID_CONTROL_NAMESPACE,
    MetaPaidControlSnapshotView,
)
from app.paid_control_reconciliation import PaidControlReconciliationService
from app.paid_control_sweep import (
    PAID_CONTROL_SWEEP_RUN_NAMESPACE,
    PaidControlSweepItemOutcome,
    PaidControlSweepItemView,
    PaidControlSweepRegistry,
    PaidControlSweepView,
)
from app.runtime_store import MemoryRuntimeStateStore, get_runtime_store

client = TestClient(app)


class ReconcilingProvider:
    provider = "meta-marketing-api"

    def __init__(self, store: MemoryRuntimeStateStore, *, resolve: bool) -> None:
        self.store = store
        self.resolve = resolve
        self.calls: list[UUID] = []

    def sync(self, action_id: UUID) -> PaidControlSweepItemView:
        self.calls.append(action_id)
        receipt_payload = self.store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action_id))
        assert receipt_payload is not None
        receipt = ExecutionAdapterReceipt.model_validate(receipt_payload)
        metadata = dict(receipt.metadata)
        metadata["requires_reconciliation"] = not self.resolve
        self.store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(action_id),
            receipt.model_copy(update={"metadata": metadata}).model_dump(mode="json"),
        )
        control_payload = self.store.get(META_PAID_CONTROL_NAMESPACE, str(action_id))
        assert control_payload is not None
        control = MetaPaidControlSnapshotView.model_validate(control_payload)
        updated = control.model_copy(
            update={
                "sync_state": "SYNCED" if self.resolve else "UNKNOWN",
                "pause_state": "CONFIRMED" if self.resolve else "UNKNOWN",
                "requires_reconciliation": not self.resolve,
                "last_error": None if self.resolve else "still ambiguous",
                "synced_at": datetime.now(UTC),
            }
        )
        self.store.put(
            META_PAID_CONTROL_NAMESPACE,
            str(action_id),
            updated.model_dump(mode="json"),
        )
        return PaidControlSweepItemView(
            action_id=action_id,
            provider=self.provider,
            outcome=(
                PaidControlSweepItemOutcome.SYNCED
                if self.resolve
                else PaidControlSweepItemOutcome.ERROR
            ),
            provider_status="PAUSED/PAUSED" if self.resolve else "UNKNOWN/UNKNOWN",
            provider_spend=updated.provider_spend,
            synced_spend=updated.synced_spend,
            pause_state=updated.pause_state,
            requires_reconciliation=not self.resolve,
            reason=updated.last_error,
        )


def _action(platform: DistributionPlatform = DistributionPlatform.INSTAGRAM) -> DistributionActionView:
    return DistributionActionView(
        id=uuid4(),
        platform=platform,
        opportunity_id=uuid4(),
        experiment_id=uuid4(),
        action_type=DistributionActionType.PAID_CAMPAIGN,
        status=DistributionActionStatus.EXECUTED,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        tracking_url="https://example.com/tracked",
        operational_metadata={"tactic_id": "instagram_ads"},
        executed_at=datetime.now(UTC),
    )


def _persist_action(store: MemoryRuntimeStateStore, action: DistributionActionView) -> None:
    store.put(DISTRIBUTION_ACTION_NAMESPACE, str(action.id), action.model_dump(mode="json"))


def _persist_receipt(
    store: MemoryRuntimeStateStore,
    action: DistributionActionView,
    *,
    provider: str = "meta-marketing-api",
    requires_reconciliation: bool = False,
) -> None:
    receipt = ExecutionAdapterReceipt(
        action_id=action.id,
        adapter_name="provider-adapter",
        provider=provider,
        outcome=AdapterExecutionOutcome.EXECUTED,
        message="provider receipt",
        metadata={
            "provider_ids": {"campaign_id": "cmp-1"},
            "requires_reconciliation": requires_reconciliation,
        },
        created_at=datetime.now(UTC),
    )
    store.put(
        EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
        str(action.id),
        receipt.model_dump(mode="json"),
    )


def _persist_meta_control(
    store: MemoryRuntimeStateStore,
    action: DistributionActionView,
    *,
    requires_reconciliation: bool,
    error: str | None = None,
) -> None:
    snapshot = MetaPaidControlSnapshotView(
        action_id=action.id,
        experiment_id=action.experiment_id,
        product_id=uuid4(),
        campaign_id="cmp-1",
        configured_status="UNKNOWN" if requires_reconciliation else "PAUSED",
        effective_status="UNKNOWN" if requires_reconciliation else "PAUSED",
        provider_spend=12,
        synced_spend=12,
        last_spend_delta=0,
        impressions=100,
        clicks=10,
        account_currency="USD",
        budget_cap=20,
        sync_state="UNKNOWN" if requires_reconciliation else "SYNCED",
        pause_state="UNKNOWN" if requires_reconciliation else "CONFIRMED",
        requires_reconciliation=requires_reconciliation,
        last_error=error,
        synced_at=datetime.now(UTC),
    )
    store.put(
        META_PAID_CONTROL_NAMESPACE,
        str(action.id),
        snapshot.model_dump(mode="json"),
    )


def _persist_sweep(
    store: MemoryRuntimeStateStore,
    action: DistributionActionView,
    *,
    outcome: PaidControlSweepItemOutcome,
    requires_reconciliation: bool,
    finished_at: datetime,
    reason: str | None,
) -> None:
    item = PaidControlSweepItemView(
        action_id=action.id,
        provider="meta-marketing-api",
        outcome=outcome,
        provider_status="UNKNOWN/UNKNOWN" if requires_reconciliation else "PAUSED/PAUSED",
        provider_spend=12,
        synced_spend=12,
        pause_state="UNKNOWN" if requires_reconciliation else "CONFIRMED",
        requires_reconciliation=requires_reconciliation,
        reason=reason,
    )
    run = PaidControlSweepView(
        run_id=uuid4(),
        started_at=finished_at - timedelta(seconds=1),
        finished_at=finished_at,
        candidate_count=1,
        synced_count=1 if outcome == PaidControlSweepItemOutcome.SYNCED else 0,
        skipped_count=1 if outcome == PaidControlSweepItemOutcome.SKIPPED else 0,
        error_count=1 if outcome == PaidControlSweepItemOutcome.ERROR else 0,
        items=[item],
    )
    store.put(
        PAID_CONTROL_SWEEP_RUN_NAMESPACE,
        str(run.run_id),
        run.model_dump(mode="json"),
    )


def test_receipt_control_and_latest_sweep_merge_into_one_queue_item() -> None:
    store = MemoryRuntimeStateStore()
    action = _action()
    _persist_action(store, action)
    _persist_receipt(store, action, requires_reconciliation=True)
    _persist_meta_control(store, action, requires_reconciliation=True, error="provider ambiguous")
    _persist_sweep(
        store,
        action,
        outcome=PaidControlSweepItemOutcome.ERROR,
        requires_reconciliation=True,
        finished_at=datetime.now(UTC),
        reason="latest sweep failed",
    )
    service = PaidControlReconciliationService(
        store=store,
        registry=PaidControlSweepRegistry([]),
    )

    queue = service.queue()

    assert queue.count == 1
    item = queue.items[0]
    assert item.action_id == action.id
    assert set(item.sources) == {"EXECUTION_RECEIPT", "META_CONTROL", "LATEST_SWEEP"}
    assert "provider ambiguous" in item.reasons
    assert "latest sweep failed" in item.reasons
    assert item.provider_spend == 12
    assert item.pause_state == "UNKNOWN"


def test_old_sweep_error_does_not_survive_newer_safe_sweep() -> None:
    store = MemoryRuntimeStateStore()
    action = _action()
    _persist_action(store, action)
    _persist_receipt(store, action, requires_reconciliation=False)
    _persist_meta_control(store, action, requires_reconciliation=False)
    now = datetime.now(UTC)
    _persist_sweep(
        store,
        action,
        outcome=PaidControlSweepItemOutcome.ERROR,
        requires_reconciliation=True,
        finished_at=now - timedelta(minutes=1),
        reason="old failure",
    )
    _persist_sweep(
        store,
        action,
        outcome=PaidControlSweepItemOutcome.SYNCED,
        requires_reconciliation=False,
        finished_at=now,
        reason=None,
    )
    service = PaidControlReconciliationService(
        store=store,
        registry=PaidControlSweepRegistry([]),
    )

    assert service.queue().count == 0


def test_unsupported_provider_skip_is_not_a_reconciliation_alert() -> None:
    store = MemoryRuntimeStateStore()
    action = _action(DistributionPlatform.REDDIT)
    _persist_action(store, action)
    _persist_receipt(store, action, provider="reddit-ads-unavailable")
    _persist_sweep(
        store,
        action,
        outcome=PaidControlSweepItemOutcome.SKIPPED,
        requires_reconciliation=False,
        finished_at=datetime.now(UTC),
        reason="unsupported provider",
    )
    service = PaidControlReconciliationService(
        store=store,
        registry=PaidControlSweepRegistry([]),
    )

    assert service.queue().count == 0


def test_safe_reconcile_resync_clears_current_reconciliation_evidence() -> None:
    store = MemoryRuntimeStateStore()
    action = _action()
    _persist_action(store, action)
    _persist_receipt(store, action, requires_reconciliation=True)
    _persist_meta_control(store, action, requires_reconciliation=True, error="ambiguous")
    provider = ReconcilingProvider(store, resolve=True)
    service = PaidControlReconciliationService(
        store=store,
        registry=PaidControlSweepRegistry([provider]),
    )

    result = service.reconcile(action.id)

    assert provider.calls == [action.id]
    assert result.sync.outcome == PaidControlSweepItemOutcome.SYNCED
    assert result.resolved is True
    assert result.remaining is None
    assert service.queue().count == 0


def test_failed_reconcile_remains_queued_without_activation_or_budget_write() -> None:
    store = MemoryRuntimeStateStore()
    action = _action()
    _persist_action(store, action)
    _persist_receipt(store, action, requires_reconciliation=True)
    _persist_meta_control(store, action, requires_reconciliation=True, error="ambiguous")
    provider = ReconcilingProvider(store, resolve=False)
    service = PaidControlReconciliationService(
        store=store,
        registry=PaidControlSweepRegistry([provider]),
    )

    result = service.reconcile(action.id)

    assert provider.calls == [action.id]
    assert result.resolved is False
    assert result.remaining is not None
    assert result.sync.outcome == PaidControlSweepItemOutcome.ERROR
    receipt = ExecutionAdapterReceipt.model_validate(
        store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action.id))
    )
    assert receipt.outcome == AdapterExecutionOutcome.EXECUTED
    assert receipt.metadata["requires_reconciliation"] is True


def test_ops_queue_and_sweep_history_responses_do_not_expose_secrets() -> None:
    store = get_runtime_store()
    for namespace in (
        DISTRIBUTION_ACTION_NAMESPACE,
        EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
        META_PAID_CONTROL_NAMESPACE,
        PAID_CONTROL_SWEEP_RUN_NAMESPACE,
    ):
        store.clear_namespace(namespace)
    action = _action()
    _persist_action(store, action)
    _persist_receipt(store, action, requires_reconciliation=True)
    _persist_meta_control(store, action, requires_reconciliation=True, error="provider ambiguous")
    _persist_sweep(
        store,
        action,
        outcome=PaidControlSweepItemOutcome.ERROR,
        requires_reconciliation=True,
        finished_at=datetime.now(UTC),
        reason="provider error",
    )

    queue = client.get("/v1/ops/paid-control/reconciliation")
    sweeps = client.get("/v1/ops/paid-control/sweeps?limit=20")

    assert queue.status_code == 200
    assert queue.json()["count"] == 1
    assert sweeps.status_code == 200
    assert len(sweeps.json()) == 1
    combined = queue.text + sweeps.text
    assert "secret-token" not in combined
    assert "ACCESS_TOKEN" not in combined
