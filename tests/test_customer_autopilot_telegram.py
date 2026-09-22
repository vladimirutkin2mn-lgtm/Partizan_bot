from types import SimpleNamespace
from uuid import uuid4

import app.customer_autopilot as autopilot
from app.autonomy_service import GrowthMandateService
from app.customer_autopilot import CustomerAutopilotService
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.growth_balance import GROWTH_BALANCE_TOPUP_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore


class FakeChannels:
    def __init__(self, platforms):
        self.platforms = list(platforms)

    def autonomous_platforms(self, project: dict):
        del project
        return list(self.platforms)


class FakeProductIntake:
    def get_product(self, product_id):
        del product_id
        return SimpleNamespace(reference_links=["https://t.me/example_app"])


class FakeAnalytics:
    def product_analytics(self, product_id):
        del product_id
        return SimpleNamespace(
            total_spend=0.0,
            total_costs=SimpleNamespace(distribution_spend=0.0),
        )


class FakeAudience:
    def __init__(self, platforms):
        self.platforms = list(platforms)

    def get(self, product_id):
        del product_id
        return SimpleNamespace(
            opportunities=[
                SimpleNamespace(platform=platform)
                for platform in self.platforms
            ]
        )


class FakePlays:
    def get(self, product_id):
        del product_id
        return SimpleNamespace()


class FakePaidConnections:
    def get_meta(self, product_id):
        del product_id
        return None


def _patch_common(monkeypatch, store, platforms):
    mandate_service = GrowthMandateService(store=store)
    monkeypatch.setattr(autopilot, "growth_mandate_service", mandate_service)
    monkeypatch.setattr(autopilot, "customer_channel_service", FakeChannels(platforms))
    monkeypatch.setattr(autopilot, "product_intake_service", FakeProductIntake())
    monkeypatch.setattr(autopilot, "distribution_analytics_service", FakeAnalytics())
    monkeypatch.setattr(
        autopilot,
        "audience_intelligence_service",
        FakeAudience(platforms),
    )
    monkeypatch.setattr(autopilot, "distribution_play_service", FakePlays())
    monkeypatch.setattr(autopilot, "paid_provider_connection_service", FakePaidConnections())
    return mandate_service


def test_telegram_only_autopilot_uses_funded_balance_without_paid_guardrails(
    monkeypatch,
) -> None:
    store = MemoryRuntimeStateStore()
    mandate_service = _patch_common(
        monkeypatch,
        store,
        [DistributionPlatform.TELEGRAM],
    )
    service = CustomerAutopilotService(store=store)
    project_id = uuid4()
    product_id = uuid4()
    project = {
        "id": str(project_id),
        "product_id": str(product_id),
        "research_state": "READY",
    }

    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "telegram-autopilot-balance",
        {
            "project_id": str(project_id),
            "amount_cents": 100,
            "currency": "usd",
            "state": "PAID",
        },
    )

    mandate = service._ensure_mandate_if_ready(
        project_id,
        project,
        product_id,
        force_update=True,
    )

    assert mandate is not None
    assert mandate.total_budget_cap == 1.0
    assert mandate.max_autonomous_spend_per_experiment == 0.001
    assert mandate.max_autonomous_spend_per_day == 1.0
    assert mandate.target_max_cac is None
    assert mandate.allowed_platforms == [DistributionPlatform.TELEGRAM]
    assert set(mandate.allowed_actions) == {
        DistributionActionType.COMMENT,
        DistributionActionType.REPLY,
        DistributionActionType.STANDALONE_POST,
    }
    assert mandate.autonomous_prepare is True
    assert mandate.autonomous_approve is True
    assert mandate.autonomous_paid_activation is False
    assert mandate_service.get(product_id).status.value == "ACTIVE"


def test_telegram_autopilot_waits_when_no_execution_opportunity_exists(
    monkeypatch,
) -> None:
    store = MemoryRuntimeStateStore()
    mandate_service = _patch_common(
        monkeypatch,
        store,
        [DistributionPlatform.TELEGRAM],
    )
    monkeypatch.setattr(
        autopilot,
        "audience_intelligence_service",
        FakeAudience([]),
    )
    service = CustomerAutopilotService(store=store)
    project_id = uuid4()
    product_id = uuid4()
    project = {
        "id": str(project_id),
        "product_id": str(product_id),
        "research_state": "READY",
    }

    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "telegram-autopilot-balance",
        {
            "project_id": str(project_id),
            "amount_cents": 100,
            "currency": "usd",
            "state": "PAID",
        },
    )

    mandate = service._ensure_mandate_if_ready(
        project_id,
        project,
        product_id,
        force_update=True,
    )

    assert mandate is None
    try:
        mandate_service.get(product_id)
    except KeyError:
        pass
    else:
        raise AssertionError("No mandate should exist without a real execution opportunity")


def test_meta_autopilot_still_requires_paid_spend_guardrails(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    mandate_service = _patch_common(
        monkeypatch,
        store,
        [DistributionPlatform.INSTAGRAM],
    )
    service = CustomerAutopilotService(store=store)
    project_id = uuid4()
    product_id = uuid4()
    project = {
        "id": str(project_id),
        "product_id": str(product_id),
        "research_state": "READY",
    }

    mandate = service._ensure_mandate_if_ready(
        project_id,
        project,
        product_id,
        force_update=True,
    )

    assert mandate is None
    try:
        mandate_service.get(product_id)
    except KeyError:
        pass
    else:
        raise AssertionError("Meta paid mandate must not exist without spend guardrails")
