from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from app.customer_paid_campaign_lifecycle import AUTOPILOT_CUSTOMER_PAUSE_REASON
from app.distribution_types import DistributionActionStatus
from app.meta_paid_control import MetaPaidControlService, MetaPaidControlSnapshotView
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_paid_control import TikTokPaidControlService, TikTokPaidControlSnapshotView

ACTION_ID = UUID("11111111-1111-1111-1111-111111111111")
EXPERIMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
PRODUCT_ID = UUID("33333333-3333-3333-3333-333333333333")


class FakeMetaClient:
    def __init__(self, spend: float) -> None:
        self.spend = spend

    def get_campaign_state(self, **kwargs):
        return SimpleNamespace(
            campaign_id="meta-campaign",
            configured_status="PAUSED",
            effective_status="PAUSED",
        )

    def get_campaign_insights(self, **kwargs):
        return SimpleNamespace(
            spend=self.spend,
            impressions=0,
            clicks=0,
            account_currency="USD",
        )


class FakeTikTokClient:
    def __init__(self, spend: float) -> None:
        self.spend = spend

    def get_campaign_state(self, **kwargs):
        return SimpleNamespace(
            campaign_id="tiktok-campaign",
            operation_status="DISABLE",
            primary_status="STATUS_DISABLE",
            secondary_status=None,
        )

    def get_campaign_insights(self, **kwargs):
        return SimpleNamespace(
            spend=self.spend,
            impressions=0,
            clicks=0,
            currency="USD",
        )


def _context(campaign_id: str):
    return SimpleNamespace(
        action_id=ACTION_ID,
        action_status=DistributionActionStatus.EXECUTED,
        experiment_id=EXPERIMENT_ID,
        product_id=PRODUCT_ID,
        campaign_id=campaign_id,
        receipt=SimpleNamespace(metadata={}),
        spec=SimpleNamespace(budget_cap=100.0),
        connection=SimpleNamespace(),
        access_token="token",
    )


def _meta_service(*, spend: float, prior_spend: float) -> MetaPaidControlService:
    store = MemoryRuntimeStateStore()
    service = MetaPaidControlService(store=store, meta_client=FakeMetaClient(spend))
    service._context = lambda action_id: _context("meta-campaign")
    service._update_receipt_control = lambda receipt, snapshot: None
    now = datetime.now(UTC)
    service._persist(
        MetaPaidControlSnapshotView(
            action_id=ACTION_ID,
            experiment_id=EXPERIMENT_ID,
            product_id=PRODUCT_ID,
            campaign_id="meta-campaign",
            configured_status="PAUSED",
            effective_status="PAUSED",
            provider_spend=prior_spend,
            synced_spend=prior_spend,
            last_spend_delta=0.0,
            impressions=0,
            clicks=0,
            account_currency="USD",
            budget_cap=100.0,
            sync_state="SYNCED",
            budget_guardrail_triggered=False,
            pause_state="CONFIRMED",
            pause_reason=AUTOPILOT_CUSTOMER_PAUSE_REASON,
            requires_reconciliation=False,
            synced_at=now,
            paused_at=now,
        )
    )
    return service


def _tiktok_service(*, spend: float, prior_spend: float) -> TikTokPaidControlService:
    store = MemoryRuntimeStateStore()
    service = TikTokPaidControlService(store=store, client=FakeTikTokClient(spend))
    service._context = lambda action_id: _context("tiktok-campaign")
    service._update_receipt_control = lambda receipt, snapshot: None
    now = datetime.now(UTC)
    service._persist(
        TikTokPaidControlSnapshotView(
            action_id=ACTION_ID,
            experiment_id=EXPERIMENT_ID,
            product_id=PRODUCT_ID,
            campaign_id="tiktok-campaign",
            operation_status="DISABLE",
            primary_status="STATUS_DISABLE",
            secondary_status=None,
            provider_spend=prior_spend,
            synced_spend=prior_spend,
            last_spend_delta=0.0,
            impressions=0,
            clicks=0,
            currency="USD",
            budget_cap=100.0,
            sync_state="SYNCED",
            budget_guardrail_triggered=False,
            pause_state="CONFIRMED",
            pause_reason=AUTOPILOT_CUSTOMER_PAUSE_REASON,
            requires_reconciliation=False,
            synced_at=now,
            paused_at=now,
        )
    )
    return service


def test_meta_sync_preserves_confirmed_customer_pause_reason() -> None:
    result = _meta_service(spend=0.0, prior_spend=0.0).sync(ACTION_ID)

    assert result.pause_state == "CONFIRMED"
    assert result.pause_reason == AUTOPILOT_CUSTOMER_PAUSE_REASON


def test_tiktok_sync_preserves_confirmed_customer_pause_reason() -> None:
    result = _tiktok_service(spend=0.0, prior_spend=0.0).sync(ACTION_ID)

    assert result.pause_state == "CONFIRMED"
    assert result.pause_reason == AUTOPILOT_CUSTOMER_PAUSE_REASON


def test_meta_budget_cap_overrides_customer_pause_reason() -> None:
    result = _meta_service(spend=100.0, prior_spend=100.0).sync(ACTION_ID)

    assert result.pause_state == "CONFIRMED"
    assert result.pause_reason == "BUDGET_CAP"


def test_tiktok_budget_cap_overrides_customer_pause_reason() -> None:
    result = _tiktok_service(spend=100.0, prior_spend=100.0).sync(ACTION_ID)

    assert result.pause_state == "CONFIRMED"
    assert result.pause_reason == "BUDGET_CAP"
