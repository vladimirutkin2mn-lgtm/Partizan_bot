import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config import Settings
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.provider_secret_store import ProviderSecretStore
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
    CustomerTelegramClientPublishError,
    CustomerTelegramClientPublishService,
    TelegramClientPublishOutcome,
    TelegramClientPublishReceipt,
    TelegramPublishRequest,
)


class NeverPublishTransport:
    async def begin_login(self, phone_number: str):
        raise AssertionError("login transport must not run")

    async def complete_login(self, **kwargs):
        raise AssertionError("login transport must not run")

    async def publish(self, **kwargs):
        raise AssertionError("publish transport must not run for a foreign project")


def test_existing_receipt_is_not_exposed_to_foreign_project(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    settings = Settings(
        _env_file=None,
        telegram_client_publish_provider="telethon",
        telegram_client_publish_public_ready=True,
        telegram_client_publish_api_id=12345,
        telegram_client_publish_api_hash=SecretStr("publish-api-hash"),
        provider_secret_encryption_key=SecretStr(Fernet.generate_key().decode("ascii")),
    )
    service = CustomerTelegramClientPublishService(
        store=store,
        settings=settings,
        secret_store=ProviderSecretStore(store=store, settings=settings),
        transport=NeverPublishTransport(),
    )

    project_id = uuid4()
    project_product_id = uuid4()
    foreign_product_id = uuid4()
    experiment_id = uuid4()
    action_id = uuid4()

    project = {"id": str(project_id), "product_id": str(project_product_id)}
    action = SimpleNamespace(
        id=action_id,
        platform=DistributionPlatform.TELEGRAM,
        action_type=DistributionActionType.STANDALONE_POST,
        experiment_id=experiment_id,
    )
    experiment = SimpleNamespace(product_id=foreign_product_id)

    from app import telegram_client_publishing as module

    monkeypatch.setattr(
        module.customer_funnel_service,
        "get_project_payload",
        lambda candidate_project_id, customer_token: project,
    )
    monkeypatch.setattr(
        module.distribution_execution_service,
        "get_action",
        lambda candidate_action_id: action,
    )
    monkeypatch.setattr(
        module.distribution_execution_service,
        "get_experiment",
        lambda candidate_experiment_id: experiment,
    )

    receipt = TelegramClientPublishReceipt(
        action_id=action_id,
        outcome=TelegramClientPublishOutcome.EXECUTED,
        message="Previously executed receipt that belongs to another product.",
        external_reference="telegram-client:123456:88",
        executed_url="https://t.me/relationship_group/88",
        published_at=datetime(2026, 9, 8, 16, 0, tzinfo=UTC),
        metadata={"remote_message_id": 88},
        created_at=datetime(2026, 9, 8, 16, 0, tzinfo=UTC),
    )
    store.put(
        CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
        str(action_id),
        receipt.model_dump(mode="json"),
    )

    with pytest.raises(
        CustomerTelegramClientPublishError,
        match="does not belong to this customer project",
    ):
        asyncio.run(
            service.publish(
                project_id,
                "customer-token",
                action_id,
                TelegramPublishRequest(retry=False),
            )
        )
