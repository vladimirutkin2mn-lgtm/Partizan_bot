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
    CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE,
    CustomerTelegramGovernanceService,
    TelegramAutomationAuthorizationRequest,
    TelegramAutomationStatus,
    TelegramRemoteMessageState,
    TelegramRemoteObservationResult,
)
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
    CustomerTelegramClientPublishError,
    TelegramClientPublishOutcome,
    TelegramClientPublishReceipt,
    TelegramConnectionStatus,
    TelegramPublishRequest,
)


class FakeCustomerFunnel:
    def __init__(self, project: dict) -> None:
        self.project = project

    def get_project_payload(self, project_id, customer_token: str) -> dict:
        assert customer_token == "customer-token"
        assert str(project_id) == str(self.project["project_id"])
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
    def __init__(self, receipt: TelegramClientPublishReceipt) -> None:
        self.receipt = receipt
        self.connected = True
        self.blocker: str | None = None
        self.publish_calls: list[tuple] = []

    def readiness_blocker(self) -> str | None:
        return self.blocker

    def is_connected(self, project_id) -> bool:
        return self.connected

    def get_receipt(self, action_id):
        return self.receipt if action_id == self.receipt.action_id else None

    async def publish(self, project_id, customer_token, action_id, payload):
        self.publish_calls.append((project_id, customer_token, action_id, payload.retry))
        return self.receipt


class FakeSecretStore:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, reference: str) -> str | None:
        return self.values.get(reference)


class FakeObservationTransport:
    def __init__(self, results: list[TelegramRemoteObservationResult]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def observe(self, *, session: str, target_username: str, message_id: int):
        self.calls.append(
            {
                "session": session,
                "target_username": target_username,
                "message_id": message_id,
            }
        )
        return self.results.pop(0)


def _settings():
    return SimpleNamespace(
        telegram_client_publish_provider="telethon",
        telegram_client_publish_public_ready=True,
        telegram_client_publish_api_id=12345,
        telegram_client_publish_api_hash=SecretStr("api-hash-secret"),
        provider_secret_encryption_key=SecretStr("configured-encryption-key"),
    )


def _fixture(monkeypatch):
    store = MemoryRuntimeStateStore()
    project_id = uuid4()
    product_id = uuid4()
    experiment_id = uuid4()
    action_id = uuid4()
    project = {
        "project_id": str(project_id),
        "product_id": str(product_id),
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
        published_at=datetime(2026, 9, 8, 18, 0, tzinfo=UTC),
        metadata={
            "target_username": "relationship_group",
            "remote_peer_id": 123456,
            "remote_message_id": 88,
        },
        created_at=datetime(2026, 9, 8, 18, 0, tzinfo=UTC),
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
        CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "status": TelegramConnectionStatus.ACTIVE.value,
            "secret_reference": "SESSION_REF",
        },
    )
    return store, project, action, receipt, publish_service


def test_automation_requires_explicit_confirmation_and_live_readiness(monkeypatch) -> None:
    store, project, _, _, publish_service = _fixture(monkeypatch)
    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
        secret_store=FakeSecretStore({"SESSION_REF": "secret-session"}),
        observation_transport=FakeObservationTransport([]),
    )
    project_id = project["project_id"]

    with pytest.raises(CustomerTelegramClientPublishError, match="Explicit confirmation"):
        service.authorize_automation(
            project_id,
            "customer-token",
            TelegramAutomationAuthorizationRequest(
                confirm_client_owned_execution=False,
                max_publishes_per_day=2,
            ),
        )

    publish_service.blocker = "Telegram publishing paused"
    with pytest.raises(CustomerTelegramClientPublishError, match="paused"):
        service.authorize_automation(
            project_id,
            "customer-token",
            TelegramAutomationAuthorizationRequest(
                confirm_client_owned_execution=True,
                max_publishes_per_day=2,
            ),
        )

    publish_service.blocker = None
    authorized = service.authorize_automation(
        project_id,
        "customer-token",
        TelegramAutomationAuthorizationRequest(
            confirm_client_owned_execution=True,
            max_publishes_per_day=2,
        ),
    )
    assert authorized.status == TelegramAutomationStatus.ENABLED
    assert authorized.max_publishes_per_day == 2
    assert authorized.readiness_ok is True


def test_automated_publish_rechecks_authorization_pause_and_daily_limit(monkeypatch) -> None:
    store, project, action, receipt, publish_service = _fixture(monkeypatch)
    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
        secret_store=FakeSecretStore({"SESSION_REF": "secret-session"}),
        observation_transport=FakeObservationTransport([]),
    )
    project_id = project["project_id"]
    service.authorize_automation(
        project_id,
        "customer-token",
        TelegramAutomationAuthorizationRequest(
            confirm_client_owned_execution=True,
            max_publishes_per_day=1,
        ),
    )
    service.pause_automation(project_id, "customer-token")
    with pytest.raises(CustomerTelegramClientPublishError, match="not explicitly enabled"):
        pytest.run(asyncio=False)

    service.authorize_automation(
        project_id,
        "customer-token",
        TelegramAutomationAuthorizationRequest(
            confirm_client_owned_execution=True,
            max_publishes_per_day=1,
        ),
    )
    store.put(
        CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "history": [
                {
                    "fingerprint": "existing",
                    "published_at": datetime.now(UTC).isoformat(),
                }
            ],
        },
    )
    with pytest.raises(CustomerTelegramClientPublishError, match="limited to 1"):
        pytest.run(asyncio=False)

    store.clear_namespace(CUSTOMER_TELEGRAM_PUBLISH_GUARD_NAMESPACE)
    result = pytest.run(asyncio=False)
    assert result is None
    assert receipt.action_id == action.id
    assert publish_service.publish_calls == []


@pytest.mark.asyncio
async def test_automated_publish_delegates_only_after_authorization(monkeypatch) -> None:
    store, project, action, receipt, publish_service = _fixture(monkeypatch)
    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
        secret_store=FakeSecretStore({"SESSION_REF": "secret-session"}),
        observation_transport=FakeObservationTransport([]),
    )
    project_id = project["project_id"]

    with pytest.raises(CustomerTelegramClientPublishError, match="not explicitly enabled"):
        await service.automated_publish(
            project_id,
            "customer-token",
            action.id,
            TelegramPublishRequest(),
        )

    service.authorize_automation(
        project_id,
        "customer-token",
        TelegramAutomationAuthorizationRequest(
            confirm_client_owned_execution=True,
            max_publishes_per_day=2,
        ),
    )
    result = await service.automated_publish(
        project_id,
        "customer-token",
        action.id,
        TelegramPublishRequest(),
    )
    assert result == receipt
    assert len(publish_service.publish_calls) == 1

    service.revoke_automation(project_id, "customer-token")
    with pytest.raises(CustomerTelegramClientPublishError, match="not explicitly enabled"):
        await service.automated_publish(
            project_id,
            "customer-token",
            action.id,
            TelegramPublishRequest(),
        )


@pytest.mark.asyncio
async def test_observation_records_present_then_removed_without_secret_leak(monkeypatch) -> None:
    store, project, action, _, _ = _fixture(monkeypatch)
    transport = FakeObservationTransport(
        [
            TelegramRemoteObservationResult(state=TelegramRemoteMessageState.PRESENT),
            TelegramRemoteObservationResult(
                state=TelegramRemoteMessageState.REMOVED,
                provider_code="MESSAGE_NOT_FOUND",
                restriction_signal="MESSAGE_REMOVED_OR_UNAVAILABLE",
            ),
        ]
    )
    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
        secret_store=FakeSecretStore({"SESSION_REF": "secret-session"}),
        observation_transport=transport,
    )
    project_id = project["project_id"]

    first = await service.observe_publish(project_id, "customer-token", action.id)
    second = await service.observe_publish(project_id, "customer-token", action.id)

    assert first.latest.state == TelegramRemoteMessageState.PRESENT
    assert second.latest.state == TelegramRemoteMessageState.REMOVED
    assert second.latest.restriction_signal == "MESSAGE_REMOVED_OR_UNAVAILABLE"
    assert len(second.history) == 2
    assert transport.calls[0]["session"] == "secret-session"
    assert "secret-session" not in second.model_dump_json()
    persisted = store.get(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE, str(action.id))
    assert persisted is not None
    assert "secret-session" not in str(persisted)


@pytest.mark.asyncio
async def test_observation_records_restriction_and_denies_cross_project_access(monkeypatch) -> None:
    store, project, action, _, _ = _fixture(monkeypatch)
    transport = FakeObservationTransport(
        [
            TelegramRemoteObservationResult(
                state=TelegramRemoteMessageState.INACCESSIBLE,
                provider_code="COMMUNITY_NOT_ACCESSIBLE",
                restriction_signal="COMMUNITY_RESTRICTED",
            )
        ]
    )
    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
        secret_store=FakeSecretStore({"SESSION_REF": "secret-session"}),
        observation_transport=transport,
    )
    view = await service.observe_publish(project["project_id"], "customer-token", action.id)
    assert view.latest.state == TelegramRemoteMessageState.INACCESSIBLE
    assert view.latest.restriction_signal == "COMMUNITY_RESTRICTED"

    foreign = dict(project)
    foreign["product_id"] = str(uuid4())
    monkeypatch.setattr(governance, "customer_funnel_service", FakeCustomerFunnel(foreign))
    with pytest.raises(CustomerTelegramClientPublishError, match="does not belong"):
        service.get_observation(project["project_id"], "customer-token", action.id)


def test_reset_clears_governance_state(monkeypatch) -> None:
    store, project, action, _, _ = _fixture(monkeypatch)
    service = CustomerTelegramGovernanceService(
        store=store,
        settings=_settings(),
        secret_store=FakeSecretStore({"SESSION_REF": "secret-session"}),
        observation_transport=FakeObservationTransport([]),
    )
    store.put(
        CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE,
        str(project["project_id"]),
        {"status": TelegramAutomationStatus.ENABLED.value},
    )
    store.put(
        CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE,
        str(action.id),
        {"action_id": str(action.id)},
    )
    service.reset()
    assert store.list_namespace(CUSTOMER_TELEGRAM_AUTOMATION_NAMESPACE) == []
    assert store.list_namespace(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE) == []
