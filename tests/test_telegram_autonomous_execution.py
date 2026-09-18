from datetime import UTC, datetime
from uuid import uuid4

import pytest

import app.telegram_autonomous_execution as autonomous
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
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
from app.growth_balance import GROWTH_BALANCE_TOPUP_NAMESPACE, GrowthBalanceService
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_autonomous_execution import CustomerTelegramAutonomousExecutionService
from app.telegram_client_governance import CustomerTelegramClientPublishError
from app.telegram_client_publishing import (
    TelegramClientPublishOutcome,
    TelegramClientPublishReceipt,
)


class FakeGovernance:
    def __init__(self, receipt: TelegramClientPublishReceipt) -> None:
        self.receipt = receipt
        self.calls: list[tuple] = []

    async def automated_publish_internal(self, project_id, action_id, payload):
        self.calls.append((project_id, action_id, payload.retry))
        return self.receipt


class FakeDistributionExecution:
    def __init__(self, plan: DistributionExecutionPlanView) -> None:
        self.plan = plan

    def get_plan(self, action_id):
        assert action_id == self.plan.action.id
        return self.plan


class FakeAnalytics:
    def __init__(self) -> None:
        self.spend_by_id: dict[str, object] = {}

    def product_analytics(self, product_id):
        del product_id
        return type(
            "Analytics",
            (),
            {"total_costs": type("Costs", (), {"distribution_spend": 0.0})()},
        )()

    def add_spend(self, experiment_id, payload):
        key = str(payload.spend_id)
        self.spend_by_id.setdefault(key, (experiment_id, payload))
        return self.spend_by_id[key]


def _plan(product_id, action_id, experiment_id) -> DistributionExecutionPlanView:
    opportunity_id = uuid4()
    return DistributionExecutionPlanView(
        action=DistributionActionView(
            id=action_id,
            platform=DistributionPlatform.TELEGRAM,
            opportunity_id=opportunity_id,
            experiment_id=experiment_id,
            action_type=DistributionActionType.STANDALONE_POST,
            status=DistributionActionStatus.EXECUTED,
            automation_level=AutomationLevel.FULL,
            attribution_level=AttributionLevel.ACTION,
            target_url="https://t.me/example_group",
            content_text="Try the product",
            tracking_url="https://partizanlabs.com/r/1234567890abcdef",
        ),
        experiment=DistributionExperimentView(
            id=experiment_id,
            product_id=product_id,
            distribution_play_id=uuid4(),
            opportunity_id=opportunity_id,
            action_id=action_id,
            status=DistributionExperimentStatus.RUNNING,
            attribution_level=AttributionLevel.ACTION,
            tracking_url="https://partizanlabs.com/r/1234567890abcdef",
            referral_token="1234567890abcdef",
        ),
    )


@pytest.mark.asyncio
async def test_autonomous_telegram_routes_through_client_owned_governance(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    action_id = uuid4()
    experiment_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
            "channel_preferences": {"TELEGRAM": "AUTO"},
            "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
        },
    )
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "telegram-autonomous-balance",
        {
            "project_id": str(project_id),
            "amount_cents": 100,
            "currency": "usd",
            "state": "PAID",
        },
    )
    receipt = TelegramClientPublishReceipt(
        action_id=action_id,
        outcome=TelegramClientPublishOutcome.EXECUTED,
        message="Telegram confirmed the client-owned publish.",
        external_reference="telegram-client:123:456",
        executed_url="https://t.me/example_group/456",
        published_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        created_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    )
    governance = FakeGovernance(receipt)
    monkeypatch.setattr(
        autonomous,
        "customer_telegram_governance_service",
        governance,
    )
    monkeypatch.setattr(
        autonomous,
        "distribution_execution_service",
        FakeDistributionExecution(_plan(product_id, action_id, experiment_id)),
    )
    analytics = FakeAnalytics()
    monkeypatch.setattr(autonomous, "distribution_analytics_service", analytics)
    balance = GrowthBalanceService(store)
    service = CustomerTelegramAutonomousExecutionService(
        store=store,
        balance_service=balance,
    )

    result = await service.execute(
        product_id=product_id,
        action_id=action_id,
    )

    assert result.receipt.provider == "telegram-client-owned"
    assert result.receipt.adapter_name == "telegram-client-owned-autonomous"
    assert result.receipt.outcome.value == "EXECUTED"
    assert governance.calls == [(project_id, action_id, False)]
    assert balance.summary(project_id, 0.0).execution_fee_usd == 0.001
    assert balance.summary(project_id, 0.0).available_usd == 0.999
    assert len(analytics.spend_by_id) == 1
    _, spend = next(iter(analytics.spend_by_id.values()))
    assert spend.amount == 0.001
    assert spend.category.value == "EXECUTION_FEE"

    duplicate = await service.execute(
        product_id=product_id,
        action_id=action_id,
    )
    assert duplicate.receipt.outcome.value == "EXECUTED"
    assert balance.summary(project_id, 0.0).execution_fee_usd == 0.001
    assert len(analytics.spend_by_id) == 1


@pytest.mark.asyncio
async def test_failed_telegram_publish_does_not_charge_growth_balance(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    action_id = uuid4()
    experiment_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
            "channel_preferences": {"TELEGRAM": "AUTO"},
            "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
        },
    )
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "telegram-failed-balance",
        {
            "project_id": str(project_id),
            "amount_cents": 100,
            "currency": "usd",
            "state": "PAID",
        },
    )
    receipt = TelegramClientPublishReceipt(
        action_id=action_id,
        outcome=TelegramClientPublishOutcome.FAILED,
        message="Telegram rejected the publish.",
        created_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    )
    governance = FakeGovernance(receipt)
    monkeypatch.setattr(autonomous, "customer_telegram_governance_service", governance)
    monkeypatch.setattr(
        autonomous,
        "distribution_execution_service",
        FakeDistributionExecution(_plan(product_id, action_id, experiment_id)),
    )
    analytics = FakeAnalytics()
    monkeypatch.setattr(autonomous, "distribution_analytics_service", analytics)
    balance = GrowthBalanceService(store)
    service = CustomerTelegramAutonomousExecutionService(
        store=store,
        balance_service=balance,
    )

    result = await service.execute(product_id=product_id, action_id=action_id)

    assert result.receipt.outcome.value == "FAILED"
    assert balance.summary(project_id, 0.0).execution_fee_usd == 0
    assert balance.summary(project_id, 0.0).available_usd == 1.0
    assert analytics.spend_by_id == {}


@pytest.mark.asyncio
async def test_autonomous_telegram_requires_available_growth_balance(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    action_id = uuid4()
    experiment_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
            "channel_preferences": {"TELEGRAM": "AUTO"},
            "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
        },
    )
    receipt = TelegramClientPublishReceipt(
        action_id=action_id,
        outcome=TelegramClientPublishOutcome.EXECUTED,
        message="confirmed",
        created_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    )
    governance = FakeGovernance(receipt)
    monkeypatch.setattr(autonomous, "customer_telegram_governance_service", governance)
    monkeypatch.setattr(
        autonomous,
        "distribution_execution_service",
        FakeDistributionExecution(_plan(product_id, action_id, experiment_id)),
    )
    monkeypatch.setattr(autonomous, "distribution_analytics_service", FakeAnalytics())
    service = CustomerTelegramAutonomousExecutionService(
        store=store,
        balance_service=GrowthBalanceService(store),
    )

    with pytest.raises(CustomerTelegramClientPublishError, match="Growth Balance"):
        await service.execute(product_id=product_id, action_id=action_id)

    assert governance.calls == []


@pytest.mark.asyncio
async def test_autonomous_telegram_fails_closed_outside_auto_mode(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    action_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
            "channel_preferences": {"TELEGRAM": "RESEARCH_ONLY"},
        },
    )
    receipt = TelegramClientPublishReceipt(
        action_id=action_id,
        outcome=TelegramClientPublishOutcome.EXECUTED,
        message="confirmed",
        created_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
    )
    governance = FakeGovernance(receipt)
    monkeypatch.setattr(
        autonomous,
        "customer_telegram_governance_service",
        governance,
    )
    service = CustomerTelegramAutonomousExecutionService(store=store)

    with pytest.raises(CustomerTelegramClientPublishError, match="not in AUTO mode"):
        await service.execute(
            product_id=product_id,
            action_id=action_id,
        )

    assert governance.calls == []
