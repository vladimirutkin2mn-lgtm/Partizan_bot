from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.customer_channels as channels
from app.channel_execution import PublisherMode
from app.customer_channel_schemas import CustomerChannelPreferencesUpdateRequest
from app.customer_channels import CustomerChannelService
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_types import DistributionPlatform
from app.growth_balance import GROWTH_BALANCE_TOPUP_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_governance import TelegramAutomationStatus


class StoreBackedFunnel:
    def __init__(self, store: MemoryRuntimeStateStore) -> None:
        self.store = store

    def get_project_payload(self, project_id, customer_token: str) -> dict:
        assert customer_token == "customer-token"
        payload = self.store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        assert payload is not None
        return payload


class FakeTelegramPublishing:
    def __init__(self) -> None:
        self.connected = True
        self.blocker: str | None = None

    def readiness_blocker(self) -> str | None:
        return self.blocker

    def is_connected(self, project_id) -> bool:
        del project_id
        return self.connected


class FakeTelegramGovernance:
    def __init__(self) -> None:
        self.status = TelegramAutomationStatus.DISABLED
        self.readiness_ok = True
        self.blockers: list[str] = []

    def _view(self):
        return SimpleNamespace(
            status=self.status,
            readiness_ok=self.readiness_ok,
            blockers=list(self.blockers),
        )

    def automation_status(self, project_id, customer_token: str):
        del project_id
        assert customer_token == "customer-token"
        return self._view()

    def automation_status_internal(self, project_id):
        del project_id
        return self._view()


def _request(**change) -> CustomerChannelPreferencesUpdateRequest:
    return CustomerChannelPreferencesUpdateRequest.model_validate(
        {"channels": [{"platform": "TELEGRAM", **change}]}
    )


def test_telegram_auto_requires_explicit_automation_but_not_paid_settlement(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "channel_preferences": {"TELEGRAM": "RESEARCH_ONLY"},
            "channel_publisher_modes": {"TELEGRAM": "MANUAL"},
        },
    )
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "telegram-test-balance",
        {
            "project_id": str(project_id),
            "amount_cents": 100,
            "currency": "usd",
            "state": "PAID",
        },
    )
    publishing = FakeTelegramPublishing()
    governance = FakeTelegramGovernance()
    monkeypatch.setattr(channels, "customer_funnel_service", StoreBackedFunnel(store))
    monkeypatch.setattr(channels, "customer_telegram_client_publish_service", publishing)
    monkeypatch.setattr(channels, "customer_telegram_governance_service", governance)
    service = CustomerChannelService(
        store=store,
        settings=SimpleNamespace(
            meta_oauth_public_ready=False,
            partizan_telegram_execution_fee_usd=0.001,
        ),
    )

    service.update(
        project_id,
        "customer-token",
        _request(publisher_mode=PublisherMode.CLIENT_OWNED),
    )

    with pytest.raises(ValueError, match="enable bounded Telegram automation"):
        service.update(
            project_id,
            "customer-token",
            _request(mode="AUTO"),
        )

    governance.status = TelegramAutomationStatus.ENABLED
    rows = service.update(
        project_id,
        "customer-token",
        _request(mode="AUTO"),
    )
    telegram = next(item for item in rows if item.platform == DistributionPlatform.TELEGRAM)

    assert telegram.mode == "AUTO"
    assert telegram.execution_ready is True
    assert telegram.autonomous_execution_available is True
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    assert project is not None
    assert service.autonomous_platforms(project) == [DistributionPlatform.TELEGRAM]


def test_telegram_auto_fails_closed_when_connection_or_live_readiness_disappears(
    monkeypatch,
) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "channel_preferences": {"TELEGRAM": "AUTO"},
            "channel_publisher_modes": {"TELEGRAM": "CLIENT_OWNED"},
        },
    )
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "telegram-test-balance",
        {
            "project_id": str(project_id),
            "amount_cents": 100,
            "currency": "usd",
            "state": "PAID",
        },
    )
    publishing = FakeTelegramPublishing()
    governance = FakeTelegramGovernance()
    governance.status = TelegramAutomationStatus.ENABLED
    monkeypatch.setattr(channels, "customer_funnel_service", StoreBackedFunnel(store))
    monkeypatch.setattr(channels, "customer_telegram_client_publish_service", publishing)
    monkeypatch.setattr(channels, "customer_telegram_governance_service", governance)
    service = CustomerChannelService(
        store=store,
        settings=SimpleNamespace(
            meta_oauth_public_ready=False,
            partizan_telegram_execution_fee_usd=0.001,
        ),
    )
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    assert project is not None

    assert service.autonomous_platforms(project) == [DistributionPlatform.TELEGRAM]

    publishing.connected = False
    assert service.autonomous_platforms(project) == []
    rows = service.list(project_id, "customer-token")
    telegram = next(item for item in rows if item.platform == DistributionPlatform.TELEGRAM)
    assert telegram.execution_ready is False
    assert telegram.execution_blocker == "connect an authorised Telegram account first"

    publishing.connected = True
    governance.readiness_ok = False
    governance.blockers = ["Telegram session requires reconnection"]
    assert service.autonomous_platforms(project) == []
    rows = service.list(project_id, "customer-token")
    telegram = next(item for item in rows if item.platform == DistributionPlatform.TELEGRAM)
    assert telegram.execution_ready is False
    assert "requires reconnection" in str(telegram.execution_blocker)
