from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

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
from app.meta_paid_control import META_PAID_CONTROL_NAMESPACE, MetaPaidControlSnapshotView
from app.paid_activation import (
    PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
    PaidActivationAuthorizationView,
)
from app.paid_campaign import PAID_CAMPAIGN_SPEC_NAMESPACE, PaidCampaignSpec
from app.paid_control_sweep import (
    PaidControlSweepItemOutcome,
    PaidControlSweepItemView,
    PaidControlSweepRegistry,
    PaidControlSweepService,
)
from app.paid_lifecycle_audit import (
    PAID_AUDIT_EVENT_NAMESPACE,
    PaidAuditActor,
    PaidAuditEventType,
    PaidAuditLedger,
    PaidAuditResult,
    PaidLifecycleNextAction,
    PaidLifecycleService,
    PaidLifecycleState,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_paid_activation import (
    TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
    TikTokPaidActivationAuthorizationView,
)
from app.tiktok_paid_control import (
    TIKTOK_PAID_CONTROL_NAMESPACE,
    TikTokPaidControlSnapshotView,
)


def _action(
    platform: DistributionPlatform,
    *,
    status: DistributionActionStatus = DistributionActionStatus.APPROVED,
) -> DistributionActionView:
    return DistributionActionView(
        id=uuid4(),
        platform=platform,
        opportunity_id=uuid4(),
        experiment_id=uuid4(),
        action_type=DistributionActionType.PAID_CAMPAIGN,
        status=status,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.PAID,
        tracking_url="https://example.com/tracked",
        operational_metadata={"tactic_id": "paid_test"},
        executed_at=datetime.now(UTC) if status == DistributionActionStatus.EXECUTED else None,
    )


def _persist_action(store: MemoryRuntimeStateStore, action: DistributionActionView) -> None:
    store.put(DISTRIBUTION_ACTION_NAMESPACE, str(action.id), action.model_dump(mode="json"))


def _persist_spec(store: MemoryRuntimeStateStore, action: DistributionActionView) -> None:
    assert action.experiment_id is not None
    spec = PaidCampaignSpec(
        action_id=action.id,
        experiment_id=action.experiment_id,
        product_id=uuid4(),
        play_id=uuid4(),
        opportunity_id=action.opportunity_id,
        platform=action.platform,
        tactic_id="paid_test",
        destination_url="https://example.com/tracked",
        budget_cap=20.0,
        audience={"theme": "test"},
        creative_brief={"message_hook": "test"},
        success_metric="paid conversions",
        kill_criteria="stop at the approved budget cap",
        created_at=datetime.now(UTC),
    )
    store.put(PAID_CAMPAIGN_SPEC_NAMESPACE, str(action.id), spec.model_dump(mode="json"))


def _persist_receipt(
    store: MemoryRuntimeStateStore,
    action: DistributionActionView,
    *,
    provider: str,
    outcome: AdapterExecutionOutcome,
    metadata: dict | None = None,
) -> None:
    receipt = ExecutionAdapterReceipt(
        action_id=action.id,
        adapter_name="test-provider",
        provider=provider,
        outcome=outcome,
        message="provider result",
        metadata=metadata or {},
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
    paused: bool,
    spend: float = 0.0,
    requires_reconciliation: bool = False,
) -> None:
    assert action.experiment_id is not None
    snapshot = MetaPaidControlSnapshotView(
        action_id=action.id,
        experiment_id=action.experiment_id,
        product_id=uuid4(),
        campaign_id="meta-campaign",
        configured_status="PAUSED" if paused else "ACTIVE",
        effective_status="PAUSED" if paused else "ACTIVE",
        provider_spend=spend,
        synced_spend=spend,
        last_spend_delta=0,
        impressions=100,
        clicks=5,
        account_currency="USD",
        budget_cap=20,
        sync_state="SYNCED",
        pause_state="CONFIRMED" if paused else "NOT_REQUESTED",
        pause_reason="BUDGET_CAP" if paused else None,
        requires_reconciliation=requires_reconciliation,
        synced_at=datetime.now(UTC),
        paused_at=datetime.now(UTC) if paused else None,
    )
    store.put(
        META_PAID_CONTROL_NAMESPACE,
        str(action.id),
        snapshot.model_dump(mode="json"),
    )


def _persist_tiktok_control(
    store: MemoryRuntimeStateStore,
    action: DistributionActionView,
    *,
    paused: bool,
    spend: float = 0.0,
    requires_reconciliation: bool = False,
) -> None:
    assert action.experiment_id is not None
    snapshot = TikTokPaidControlSnapshotView(
        action_id=action.id,
        experiment_id=action.experiment_id,
        product_id=uuid4(),
        campaign_id="tiktok-campaign",
        operation_status="DISABLE" if paused else "ENABLE",
        primary_status="STATUS_DISABLE" if paused else "STATUS_ENABLE",
        provider_spend=spend,
        synced_spend=spend,
        last_spend_delta=0,
        impressions=100,
        clicks=5,
        currency="USD",
        budget_cap=20,
        sync_state="SYNCED",
        pause_state="CONFIRMED" if paused else "NOT_REQUESTED",
        pause_reason="BUDGET_CAP" if paused else None,
        requires_reconciliation=requires_reconciliation,
        synced_at=datetime.now(UTC),
        paused_at=datetime.now(UTC) if paused else None,
    )
    store.put(
        TIKTOK_PAID_CONTROL_NAMESPACE,
        str(action.id),
        snapshot.model_dump(mode="json"),
    )


def test_meta_and_tiktok_staged_provider_pause_normalizes_to_staged() -> None:
    store = MemoryRuntimeStateStore()
    meta = _action(DistributionPlatform.INSTAGRAM)
    tiktok = _action(DistributionPlatform.TIKTOK)
    for action in (meta, tiktok):
        _persist_action(store, action)
        _persist_spec(store, action)
    _persist_receipt(
        store,
        meta,
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        metadata={"provider_ids": {"campaign_id": "m1", "ad_set_id": "m2", "ad_id": "m3"}},
    )
    _persist_receipt(
        store,
        tiktok,
        provider="tiktok-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        metadata={"provider_ids": {"campaign_id": "t1", "adgroup_id": "t2", "ad_id": "t3"}},
    )
    _persist_meta_control(store, meta, paused=True)
    _persist_tiktok_control(store, tiktok, paused=True)
    service = PaidLifecycleService(store)

    meta_view = service.get(meta.id)
    tiktok_view = service.get(tiktok.id)

    assert meta_view.state == PaidLifecycleState.STAGED
    assert tiktok_view.state == PaidLifecycleState.STAGED
    assert meta_view.safe_next_action == PaidLifecycleNextAction.AUTHORIZE_ACTIVATION
    assert tiktok_view.safe_next_action == PaidLifecycleNextAction.AUTHORIZE_ACTIVATION


def test_authorized_state_is_provider_neutral_for_meta_and_tiktok() -> None:
    store = MemoryRuntimeStateStore()
    now = datetime.now(UTC)
    meta = _action(DistributionPlatform.INSTAGRAM)
    tiktok = _action(DistributionPlatform.TIKTOK)
    for action in (meta, tiktok):
        _persist_action(store, action)
        _persist_spec(store, action)
    _persist_receipt(
        store,
        meta,
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        metadata={"provider_ids": {"campaign_id": "m1", "ad_set_id": "m2", "ad_id": "m3"}},
    )
    _persist_receipt(
        store,
        tiktok,
        provider="tiktok-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        metadata={"provider_ids": {"campaign_id": "t1", "adgroup_id": "t2", "ad_id": "t3"}},
    )
    meta_auth = PaidActivationAuthorizationView(
        id=uuid4(),
        action_id=meta.id,
        product_id=uuid4(),
        approved_budget_cap=20,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    tiktok_auth = TikTokPaidActivationAuthorizationView(
        id=uuid4(),
        action_id=tiktok.id,
        product_id=uuid4(),
        approved_budget_cap=20,
        created_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    store.put(
        PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
        str(meta_auth.id),
        meta_auth.model_dump(mode="json"),
    )
    store.put(
        TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
        str(tiktok_auth.id),
        tiktok_auth.model_dump(mode="json"),
    )
    service = PaidLifecycleService(store)

    assert service.get(meta.id).state == PaidLifecycleState.AUTHORIZED
    assert service.get(tiktok.id).state == PaidLifecycleState.AUTHORIZED
    assert service.get(meta.id).safe_next_action == PaidLifecycleNextAction.ACTIVATE
    assert service.get(tiktok.id).safe_next_action == PaidLifecycleNextAction.ACTIVATE


def test_partial_provider_creation_requires_reconciliation_not_retry() -> None:
    store = MemoryRuntimeStateStore()
    action = _action(DistributionPlatform.INSTAGRAM)
    _persist_action(store, action)
    _persist_spec(store, action)
    _persist_receipt(
        store,
        action,
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.FAILED,
        metadata={"partial_provider_ids": {"campaign_id": "partial-campaign"}},
    )

    view = PaidLifecycleService(store).get(action.id)

    assert view.state == PaidLifecycleState.RECONCILIATION_REQUIRED
    assert view.safe_next_action == PaidLifecycleNextAction.RECONCILE
    assert view.provider_object_ids == {"campaign_id": "partial-campaign"}


def test_executed_provider_pause_normalizes_to_terminal_paused() -> None:
    store = MemoryRuntimeStateStore()
    meta = _action(DistributionPlatform.INSTAGRAM, status=DistributionActionStatus.EXECUTED)
    tiktok = _action(DistributionPlatform.TIKTOK, status=DistributionActionStatus.EXECUTED)
    for action in (meta, tiktok):
        _persist_action(store, action)
        _persist_spec(store, action)
    _persist_receipt(
        store,
        meta,
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.EXECUTED,
        metadata={"provider_ids": {"campaign_id": "m1"}},
    )
    _persist_receipt(
        store,
        tiktok,
        provider="tiktok-marketing-api",
        outcome=AdapterExecutionOutcome.EXECUTED,
        metadata={"provider_ids": {"campaign_id": "t1"}},
    )
    _persist_meta_control(store, meta, paused=True, spend=20)
    _persist_tiktok_control(store, tiktok, paused=True, spend=20)
    service = PaidLifecycleService(store)

    for action in (meta, tiktok):
        view = service.get(action.id)
        assert view.state == PaidLifecycleState.PAUSED
        assert view.safe_next_action == PaidLifecycleNextAction.NONE
        assert view.provider_spend == 20
        assert view.budget_cap == 20


def test_audit_is_append_only_filtered_and_sanitizes_secret_like_text() -> None:
    store = MemoryRuntimeStateStore()
    action = _action(DistributionPlatform.INSTAGRAM)
    _persist_action(store, action)
    _persist_spec(store, action)
    _persist_receipt(
        store,
        action,
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        metadata={
            "provider_ids": {"campaign_id": "m1", "ad_id": "m2", "access_token": "do-not-copy"}
        },
    )
    lifecycle = PaidLifecycleService(store)
    ledger = PaidAuditLedger(store, lifecycle)
    current = lifecycle.get(action.id)

    event = ledger.record(
        action_id=action.id,
        event_type=PaidAuditEventType.PROVIDER_STAGING,
        actor=PaidAuditActor.OPERATOR,
        result=PaidAuditResult.FAILED,
        before=current,
        after=current,
        reason="access_token=supersecret Bearer abc.def operator_key=topsecret",
    )

    assert "supersecret" not in (event.reason or "")
    assert "abc.def" not in (event.reason or "")
    assert "topsecret" not in (event.reason or "")
    assert event.provider_object_ids == {"campaign_id": "m1", "ad_id": "m2"}
    assert ledger.query(action_id=action.id, actor=PaidAuditActor.OPERATOR) == [event]
    assert len(store.list_namespace(PAID_AUDIT_EVENT_NAMESPACE)) == 1


def test_control_sync_dedup_ignores_worker_run_correlation_id() -> None:
    store = MemoryRuntimeStateStore()
    action = _action(DistributionPlatform.INSTAGRAM, status=DistributionActionStatus.EXECUTED)
    _persist_action(store, action)
    _persist_spec(store, action)
    _persist_receipt(
        store,
        action,
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.EXECUTED,
        metadata={"provider_ids": {"campaign_id": "m1"}},
    )
    _persist_meta_control(store, action, paused=False, spend=5)
    lifecycle = PaidLifecycleService(store)
    ledger = PaidAuditLedger(store, lifecycle)
    current = lifecycle.get(action.id)

    first = ledger.record(
        action_id=action.id,
        event_type=PaidAuditEventType.CONTROL_SYNC,
        actor=PaidAuditActor.WORKER,
        result=PaidAuditResult.SUCCESS,
        before=current,
        after=current,
        correlation_id=uuid4(),
        deduplicate=True,
    )
    second = ledger.record(
        action_id=action.id,
        event_type=PaidAuditEventType.CONTROL_SYNC,
        actor=PaidAuditActor.WORKER,
        result=PaidAuditResult.SUCCESS,
        before=current,
        after=current,
        correlation_id=uuid4(),
        deduplicate=True,
    )

    assert second.id == first.id
    assert len(ledger.query(action_id=action.id)) == 1


class _FakeWorkerProvider:
    provider = "meta-marketing-api"

    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def sync(self, action_id: UUID) -> PaidControlSweepItemView:
        self.calls.append(action_id)
        return PaidControlSweepItemView(
            action_id=action_id,
            provider=self.provider,
            outcome=PaidControlSweepItemOutcome.SYNCED,
            provider_status="ACTIVE/ACTIVE",
            provider_spend=0,
            synced_spend=0,
            pause_state="NOT_REQUESTED",
        )


def test_autonomous_sweep_writes_worker_actor_without_duplicate_noop_events() -> None:
    store = MemoryRuntimeStateStore()
    action = _action(DistributionPlatform.INSTAGRAM, status=DistributionActionStatus.EXECUTED)
    _persist_action(store, action)
    _persist_receipt(
        store,
        action,
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.EXECUTED,
        metadata={"provider_ids": {"campaign_id": "m1"}},
    )
    provider = _FakeWorkerProvider()
    service = PaidControlSweepService(
        store=store,
        registry=PaidControlSweepRegistry([provider]),
    )

    service.run_once()
    service.run_once()

    events = PaidAuditLedger(store, PaidLifecycleService(store)).query(action_id=action.id)
    assert len(events) == 1
    assert events[0].actor == PaidAuditActor.WORKER
    assert events[0].event_type == PaidAuditEventType.CONTROL_SYNC
    assert provider.calls == [action.id, action.id]
