from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

import app.telegram_client_governance as governance
from app.channel_execution import PublisherMode
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_governance import (
    CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE,
    CustomerTelegramGovernanceService,
    TelethonClientObservationTransport,
    TelegramAutomationStatus,
    TelegramRemoteMessageState,
)
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
    CustomerTelegramClientPublishError,
    TelegramClientPublishOutcome,
    TelegramClientPublishReceipt,
    TelegramPublishRequest,
)


def _settings(*, public_ready: bool = True):
    return SimpleNamespace(
        telegram_client_publish_provider="telethon",
        telegram_client_publish_public_ready=public_ready,
        telegram_client_publish_api_id=12345,
        telegram_client_publish_api_hash=SecretStr("api-hash-secret"),
        provider_secret_encryption_key=SecretStr("configured-encryption-key"),
    )


class FakeCustomerFunnel:
    def __init__(self, project: dict) -> None:
        self.project = project

    def get_project_payload(self, project_id, customer_token: str) -> dict:
        assert str(project_id) == str(self.project["project_id"])
        assert customer_token == "customer-token"
        return dict(self.project)


class FakeDistributionExecution:
    def __init__(self, action, experiment) -> None:
        self.action = action
        self.experiment = experiment

    def get_action(self, action_id):
        assert action_id == self.action.id
        return self.action

    def get_experiment(self, experiment_id):
        assert experiment_id == self.experiment.id
        return self.experiment


class FakePublishService:
    def __init__(self, receipt: TelegramClientPublishReceipt | None = None) -> None:
        self.receipt = receipt
        self.publish_calls = 0

    def readiness_blocker(self) -> str | None:
        return None

    def is_connected(self, project_id) -> bool:
        return True

    def get_receipt(self, action_id):
        if self.receipt is None:
            return None
        return self.receipt if action_id == self.receipt.action_id else None

    async def publish(self, project_id, customer_token, action_id, payload):
        self.publish_calls += 1
        assert self.receipt is not None
        return self.receipt


def test_observation_is_blocked_when_public_readiness_is_disabled() -> None:
    service = CustomerTelegramGovernanceService(
        store=MemoryRuntimeStateStore(),
        settings=_settings(public_ready=False),
    )
    assert service.observation_blocker() == (
        "Telegram client-owned observation is not enabled for customers yet"
    )


@pytest.mark.asyncio
async def test_observation_transport_sanitizes_session_construction_failure(monkeypatch) -> None:
    transport = TelethonClientObservationTransport(_settings())

    def fail_client(session: str):
        raise ValueError(f"invalid session {session}")

    monkeypatch.setattr(transport, "_client", fail_client)
    result = await transport.observe(
        session="super-secret-session",
        target_username="relationship_group",
        message_id=88,
    )

    assert result.state == TelegramRemoteMessageState.UNKNOWN
    assert result.provider_code == "OBSERVE_FAILED"
    assert "super-secret-session" not in result.model_dump_json()


def test_pause_does_not_downgrade_revoked_authorization(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    project = {
        "project_id": project_id,
        "product_id": uuid4(),
        "channel_publisher_modes": {
            DistributionPlatform.TELEGRAM.value: PublisherMode.CLIENT_OWNED.value,
        },
    }
    revoked_at = datetime.now(UTC).isoformat()
    store.put(
        CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "status": TelegramAutomationStatus.REVOKED.value,
            "max_publishes_per_day": 1,
            "authorized_at": None,
            "paused_at": None,
            "revoked_at": revoked_at,
        },
    )
    monkeypatch.setattr(governance, "customer_funnel_service", FakeCustomerFunnel(project))
    monkeypatch.setattr(governance, "customer_telegram_client_publish_service", FakePublishService())

    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
    )
    result = service.pause_automation(project_id, "customer-token")

    assert result.status == TelegramAutomationStatus.REVOKED
    assert result.revoked_at == datetime.fromisoformat(revoked_at)
    persisted = store.get(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE, str(project_id))
    assert persisted is not None
    assert persisted["status"] == TelegramAutomationStatus.REVOKED.value
    assert persisted["paused_at"] is None


@pytest.mark.asyncio
async def test_automated_publish_preserves_idempotent_receipt_before_daily_limit(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    experiment_id = uuid4()
    action_id = uuid4()
    project = {
        "project_id": project_id,
        "product_id": product_id,
        "channel_publisher_modes": {
            DistributionPlatform.TELEGRAM.value: PublisherMode.CLIENT_OWNED.value,
        },
    }
    action = SimpleNamespace(
        id=action_id,
        platform=DistributionPlatform.TELEGRAM,
        experiment_id=experiment_id,
    )
    experiment = SimpleNamespace(id=experiment_id, product_id=product_id)
    receipt = TelegramClientPublishReceipt(
        action_id=action_id,
        outcome=TelegramClientPublishOutcome.EXECUTED,
        message="confirmed",
        external_reference="telegram-client:123456:88",
        executed_url="https://t.me/relationship_group/88",
        published_at=datetime.now(UTC),
        metadata={
            "target_username": "relationship_group",
            "remote_message_id": 88,
        },
        created_at=datetime.now(UTC),
    )
    publish_service = FakePublishService(receipt)
    monkeypatch.setattr(governance, "customer_funnel_service", FakeCustomerFunnel(project))
    monkeypatch.setattr(
        governance,
        "distribution_execution_service",
        FakeDistributionExecution(action, experiment),
    )
    monkeypatch.setattr(governance, "customer_telegram_client_publish_service", publish_service)

    store.put(
        CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "status": TelegramAutomationStatus.ENABLED.value,
            "max_publishes_per_day": 1,
            "authorized_at": datetime.now(UTC).isoformat(),
            "paused_at": None,
            "revoked_at": None,
        },
    )
    store.put(
        CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "history": [
                {
                    "fingerprint": "already-counted",
                    "published_at": datetime.now(UTC).isoformat(),
                }
            ],
        },
    )
    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
    )

    result = await service.automated_publish(
        project_id,
        "customer-token",
        action_id,
        TelegramPublishRequest(),
    )

    assert result == receipt
    assert publish_service.publish_calls == 0

    foreign = dict(project)
    foreign["product_id"] = uuid4()
    monkeypatch.setattr(governance, "customer_funnel_service", FakeCustomerFunnel(foreign))
    with pytest.raises(CustomerTelegramClientPublishError, match="does not belong"):
        await service.automated_publish(
            project_id,
            "customer-token",
            action_id,
            TelegramPublishRequest(),
        )
