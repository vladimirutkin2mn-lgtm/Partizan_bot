from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import telegram_client_publishing as module
from app.distribution_types import DistributionActionType, DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import (
    CustomerTelegramClientPublishError,
    CustomerTelegramClientPublishService,
    TelegramPublishRequest,
)
from app.telegram_community_restrictions import TelegramCommunityRestrictionMemory


class NeverPublishTransport:
    def __init__(self) -> None:
        self.publish_calls = 0

    async def publish(self, **kwargs):
        self.publish_calls += 1
        raise AssertionError("known restriction must block before Telegram transport")


@pytest.mark.asyncio
async def test_known_customer_community_restriction_blocks_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryRuntimeStateStore()
    restrictions = TelegramCommunityRestrictionMemory(store)
    transport = NeverPublishTransport()
    service = CustomerTelegramClientPublishService(
        store=store,
        transport=transport,
        community_restrictions=restrictions,
    )

    project_id = uuid4()
    product_id = uuid4()
    experiment_id = uuid4()
    action_id = uuid4()
    project = {"id": str(project_id), "product_id": str(product_id)}
    action = SimpleNamespace(
        id=action_id,
        platform=DistributionPlatform.TELEGRAM,
        action_type=DistributionActionType.STANDALONE_POST,
        experiment_id=experiment_id,
        target_url="https://t.me/relationship_group",
        operational_metadata={},
    )
    experiment = SimpleNamespace(product_id=product_id)

    monkeypatch.setattr(
        module._impl.distribution_execution_service,
        "get_action",
        lambda candidate_action_id: action,
    )
    monkeypatch.setattr(
        module._impl.distribution_execution_service,
        "get_experiment",
        lambda candidate_experiment_id: experiment,
    )
    restrictions.remember(
        project_id,
        "relationship_group",
        reason="ACCOUNT_BANNED_IN_COMMUNITY",
        restriction_signal="ACCOUNT_RESTRICTED",
    )

    with pytest.raises(
        CustomerTelegramClientPublishError,
        match="known account/community restriction",
    ):
        await service.publish_internal(
            project_id,
            project,
            action_id,
            TelegramPublishRequest(retry=False),
        )

    assert transport.publish_calls == 0
