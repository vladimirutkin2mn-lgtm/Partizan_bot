from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import TelegramChannelInviteSnapshot
from app.telegram_native_attribution import (
    TelegramNativeAttributionError,
    TelegramNativeAttributionService,
    TelegramNativeAttributionStatus,
)
from app.telegram_profile_conversion_pack import (
    TelegramProfileCTAType,
    TelegramProfilePackStatus,
    TelegramProfilePackView,
)


def _bot_pack() -> TelegramProfilePackView:
    now = datetime.now(UTC)
    return TelegramProfilePackView(
        id=uuid4(),
        project_id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        campaign_id=uuid4(),
        action_fingerprint="a" * 64,
        mode="CUSTOMER_OWNED",
        name="Bot profile",
        display_name="Product Bot",
        bio="Try it ↓\nhttps://t.me/examplebot",
        cta_type=TelegramProfileCTAType.TELEGRAM_PUBLIC_LINK,
        cta_value="https://t.me/examplebot",
        avatar=None,
        story_enabled=False,
        status=TelegramProfilePackStatus.DRAFT,
        fingerprint="b" * 64,
        created_at=now,
        updated_at=now,
    )


class FakePackService:
    def __init__(self, pack: TelegramProfilePackView) -> None:
        self.pack = pack

    def get(self, project_id, customer_token, pack_id):
        assert project_id == self.pack.project_id
        assert customer_token == "customer-token"
        assert pack_id == self.pack.id
        return self.pack

    def update_draft(self, project_id, customer_token, pack_id, payload):
        self.pack = self.pack.model_copy(
            update={
                "bio": payload.bio,
                "cta_type": payload.cta_type,
                "cta_value": payload.cta_value,
                "fingerprint": "c" * 64,
                "updated_at": datetime.now(UTC),
            }
        )
        return self.pack


class FakeExecutionService:
    def __init__(self, pack: TelegramProfilePackView, referral_token: str = "abc123def4567890"):
        self.experiment = SimpleNamespace(
            id=pack.experiment_id,
            product_id=pack.product_id,
            action_id=pack.action_id,
            referral_token=referral_token,
        )

    def get_experiment(self, experiment_id):
        assert experiment_id == self.experiment.id
        return self.experiment


class FakeAnalyticsService:
    def __init__(self) -> None:
        self.bot_starts = 0
        self.analytics_error = None

    def experiment_analytics(self, experiment_id):
        if self.analytics_error is not None:
            raise self.analytics_error
        return SimpleNamespace(
            experiment=SimpleNamespace(id=experiment_id),
            metrics=SimpleNamespace(bot_starts=self.bot_starts),
        )


class FakeInvitePublishService:
    async def create_channel_invite_internal(self, project_id, channel_username, *, title):
        return TelegramChannelInviteSnapshot(
            link="https://t.me/+invite",
            usage=0,
            requested=0,
            revoked=False,
        )

    async def channel_invite_internal(self, project_id, channel_username, link):
        return TelegramChannelInviteSnapshot(
            link=link,
            usage=0,
            requested=0,
            revoked=False,
        )


@pytest.mark.asyncio
async def test_provision_bot_start_rewrites_public_bot_link_with_experiment_token() -> None:
    pack = _bot_pack()
    packs = FakePackService(pack)
    analytics = FakeAnalyticsService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=FakeInvitePublishService(),
        analytics_service=analytics,
        execution_service=FakeExecutionService(pack, referral_token="botStart123"),
    )

    attribution = await service.provision_bot_start(
        pack.project_id,
        "customer-token",
        pack.id,
    )

    assert attribution.status == TelegramNativeAttributionStatus.READY
    assert attribution.kind.value == "BOT_START"
    assert attribution.channel_username is None
    assert attribution.bot_username == "examplebot"
    assert attribution.bot_start_token == "botStart123"
    assert attribution.source_url == "https://t.me/examplebot"
    assert attribution.native_url == "https://t.me/examplebot?start=botStart123"
    assert packs.pack.cta_type == TelegramProfileCTAType.TELEGRAM_BOT_START
    assert packs.pack.cta_value == "https://t.me/examplebot?start=botStart123"
    assert packs.pack.bio == "Try it ↓\nhttps://t.me/examplebot?start=botStart123"


@pytest.mark.asyncio
async def test_bot_start_sync_reads_first_class_experiment_metric() -> None:
    pack = _bot_pack()
    packs = FakePackService(pack)
    analytics = FakeAnalyticsService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=FakeInvitePublishService(),
        analytics_service=analytics,
        execution_service=FakeExecutionService(pack),
    )
    await service.provision_bot_start(pack.project_id, "customer-token", pack.id)

    analytics.bot_starts = 3
    first = await service.sync(pack.project_id, "customer-token", pack.id)
    second = await service.sync(pack.project_id, "customer-token", pack.id)

    assert first.bot_start_count == 3
    assert first.last_delta_bot_starts == 3
    assert first.last_error is None
    assert second.bot_start_count == 3
    assert second.last_delta_bot_starts == 0


@pytest.mark.asyncio
async def test_bot_start_sync_preserves_last_count_when_analytics_is_unavailable() -> None:
    pack = _bot_pack()
    packs = FakePackService(pack)
    analytics = FakeAnalyticsService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=FakeInvitePublishService(),
        analytics_service=analytics,
        execution_service=FakeExecutionService(pack),
    )
    await service.provision_bot_start(pack.project_id, "customer-token", pack.id)

    analytics.bot_starts = 2
    observed = await service.sync(pack.project_id, "customer-token", pack.id)
    analytics.analytics_error = ValueError("experiment not measurable")
    pending = await service.sync(pack.project_id, "customer-token", pack.id)

    assert observed.bot_start_count == 2
    assert pending.bot_start_count == 2
    assert pending.last_delta_bot_starts == 0
    assert "Analytics pending" in (pending.last_error or "")


@pytest.mark.asyncio
async def test_bot_start_provisioning_requires_bot_username() -> None:
    pack = _bot_pack().model_copy(
        update={
            "bio": "Try it ↓\nhttps://t.me/productchannel",
            "cta_value": "https://t.me/productchannel",
        }
    )
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        publish_service=FakeInvitePublishService(),
        analytics_service=FakeAnalyticsService(),
        execution_service=FakeExecutionService(pack),
    )

    with pytest.raises(TelegramNativeAttributionError, match="ending in 'bot'"):
        await service.provision_bot_start(pack.project_id, "customer-token", pack.id)
