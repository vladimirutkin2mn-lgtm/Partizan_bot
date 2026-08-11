from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.execution_adapters import (
    AdapterExecutionOutcome,
    AssistedCommunityExecutionAdapter,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    ExecutionAdapterRegistry,
    ExecutionAdapterReceipt,
    TelegramBotExecutionAdapter,
    distribution_execution_adapter_service,
)
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class FakeSecretResolver:
    def __init__(self, value: str | None = "bot-secret-token") -> None:
        self.value = value
        self.requested: list[str] = []

    def resolve(self, environment_variable: str) -> str:
        self.requested.append(environment_variable)
        if self.value is None:
            raise ValueError(
                f"Telegram bot token secret is not configured in {environment_variable}"
            )
        return self.value


class FakeTelegramClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def send_message(self, *, token: str, chat_id: str, text: str) -> dict:
        self.calls.append({"token": token, "chat_id": chat_id, "text": text})
        if self.fail:
            raise RuntimeError("simulated network failure")
        return {
            "message_id": 321,
            "chat": {"id": -100123, "username": chat_id.removeprefix("@")},
        }


class InterruptingAdapter:
    name = "interrupting-provider"
    provider = "test"

    def supports(self, action: DistributionActionView) -> bool:
        return True

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        raise KeyboardInterrupt("simulated process interruption")


class NeverRunAdapter:
    name = "never-run"
    provider = "test"

    def supports(self, action: DistributionActionView) -> bool:
        return True

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        raise AssertionError("adapter must not run while IN_PROGRESS receipt exists")


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()
    audience_intelligence_service.reset()
    growth_play_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_execution_adapter_service.reset()


def _product() -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 200\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _telegram_identity(
    *,
    allowed_target: str = "@relationship_group",
    token_env: str = "PARTIZAN_TELEGRAM_RELATIONSHIPS_BOT_TOKEN",
) -> dict:
    response = client.post(
        "/v1/distribution-identities",
        json={
            "platform": "TELEGRAM",
            "theme": "Relationship advice",
            "language": "English",
            "public_positioning": "Partizan-operated relationship tools bot",
            "profile_config": {
                "execution_provider": "telegram_bot",
                "bot_token_env": token_env,
                "allowed_execution_targets": [allowed_target],
            },
            "allowed_opportunity_kinds": ["GROUP"],
            "allowed_actions": ["STANDALONE_POST"],
        },
    )
    assert response.status_code == 201
    return response.json()


def _approved_telegram_group_post(
    product_id: str,
    identity: dict,
    *,
    target_url: str = "https://t.me/relationship_group",
) -> str:
    slot = client.post(
        f"/v1/products/{product_id}/campaign-slots",
        json={
            "distribution_identity_id": identity["id"],
            "status": "ACTIVE",
            "attribution_route": "https://partizan.example/relationships",
        },
    )
    assert slot.status_code == 201

    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    group_post = next(
        play
        for play in plays.json()["plays"]
        if play["tactic_id"] == "telegram_group_post" and play["status"] == "READY"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{group_post['id']}/actions/prepare",
        json={
            "destination_url": "https://example.com/oracle",
            "target_url": target_url,
            "content_text": "A useful relationship reflection for the group.",
        },
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200
    return action_id


def _telegram_service(
    telegram_client: FakeTelegramClient,
    secret_resolver: FakeSecretResolver,
) -> DistributionExecutionAdapterService:
    adapter = TelegramBotExecutionAdapter(
        client=telegram_client,
        secret_resolver=secret_resolver,
    )
    return DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([adapter, AssistedCommunityExecutionAdapter()]),
        store=get_runtime_store(),
    )


def test_allowlisted_telegram_target_executes_after_provider_confirmation() -> None:
    product_id = _product()
    identity = _telegram_identity()
    action_id = _approved_telegram_group_post(product_id, identity)
    telegram_client = FakeTelegramClient()
    secret_resolver = FakeSecretResolver()
    service = _telegram_service(telegram_client, secret_resolver)

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.EXECUTED
    assert result.receipt.provider == "telegram-bot-api"
    assert result.receipt.external_reference == "telegram:@relationship_group:321"
    assert str(result.receipt.executed_url) == "https://t.me/relationship_group/321"
    assert result.plan.action.status.value == "EXECUTED"
    assert result.plan.experiment.status.value == "RUNNING"
    assert telegram_client.calls == [
        {
            "token": "bot-secret-token",
            "chat_id": "@relationship_group",
            "text": "A useful relationship reflection for the group.",
        }
    ]
    assert secret_resolver.requested == ["PARTIZAN_TELEGRAM_RELATIONSHIPS_BOT_TOKEN"]
    stored = service.get_receipt(UUID(action_id))
    assert stored is not None
    assert "bot-secret-token" not in stored.model_dump_json()


def test_target_outside_identity_allowlist_never_calls_telegram() -> None:
    product_id = _product()
    identity = _telegram_identity(allowed_target="@approved_group")
    action_id = _approved_telegram_group_post(
        product_id,
        identity,
        target_url="https://t.me/relationship_group",
    )
    telegram_client = FakeTelegramClient()
    service = _telegram_service(telegram_client, FakeSecretResolver())

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert "allowlisted" in result.receipt.message
    assert telegram_client.calls == []
    assert result.plan.action.status.value == "APPROVED"


def test_missing_secret_does_not_leak_token_and_leaves_action_approved() -> None:
    product_id = _product()
    identity = _telegram_identity()
    action_id = _approved_telegram_group_post(product_id, identity)
    telegram_client = FakeTelegramClient()
    service = _telegram_service(telegram_client, FakeSecretResolver(value=None))

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert "PARTIZAN_TELEGRAM_RELATIONSHIPS_BOT_TOKEN" in result.receipt.message
    assert "bot-secret-token" not in result.receipt.model_dump_json()
    assert telegram_client.calls == []
    assert result.plan.action.status.value == "APPROVED"


def test_telegram_network_failure_keeps_action_approved_for_explicit_retry() -> None:
    product_id = _product()
    identity = _telegram_identity()
    action_id = _approved_telegram_group_post(product_id, identity)
    service = _telegram_service(FakeTelegramClient(fail=True), FakeSecretResolver())

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.FAILED
    assert result.plan.action.status.value == "APPROVED"
    assert result.plan.experiment.status.value == "APPROVED"


def test_telegram_comment_is_not_routed_to_bot_send_message_adapter() -> None:
    action = DistributionActionView(
        id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        opportunity_id=uuid4(),
        distribution_identity_id=uuid4(),
        action_type=DistributionActionType.COMMENT,
        status=DistributionActionStatus.APPROVED,
        automation_level=AutomationLevel.ASSISTED,
        attribution_level=AttributionLevel.PROFILE,
        target_url="https://t.me/relationship_channel/10",
        content_text="Relevant comment",
    )
    registry = ExecutionAdapterRegistry(
        [
            TelegramBotExecutionAdapter(
                client=FakeTelegramClient(),
                secret_resolver=FakeSecretResolver(),
            ),
            AssistedCommunityExecutionAdapter(),
        ]
    )

    selected = registry.resolve(action)

    assert selected.name == "assisted-community"


def test_interrupted_network_attempt_requires_explicit_retry() -> None:
    product_id = _product()
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate").json()[
        "plays"
    ]
    paid = next(play for play in plays if play["tactic_id"] == "telegram_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{paid['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    ).json()
    action_id = prepared["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200

    interrupted = DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([InterruptingAdapter()]),
        store=get_runtime_store(),
    )
    with pytest.raises(KeyboardInterrupt):
        interrupted.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    stored = interrupted.get_receipt(UUID(action_id))
    assert stored is not None
    assert stored.outcome == AdapterExecutionOutcome.IN_PROGRESS

    recreated = DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([NeverRunAdapter()]),
        store=get_runtime_store(),
    )
    result = recreated.execute(UUID(action_id), DistributionAdapterExecuteRequest())
    assert result.receipt.outcome == AdapterExecutionOutcome.IN_PROGRESS
    assert result.plan.action.status.value == "APPROVED"
