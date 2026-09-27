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


def _pack() -> TelegramProfilePackView:
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
        name="FemDom profile",
        display_name="Nikolay | FemDom",
        bio="FemDom ↓\nhttps://t.me/femdom",
        cta_type=TelegramProfileCTAType.TELEGRAM_PUBLIC_LINK,
        cta_value="https://t.me/femdom",
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
        self.update_calls = []

    def get(self, project_id, customer_token, pack_id):
        assert project_id == self.pack.project_id
        assert customer_token == "customer-token"
        assert pack_id == self.pack.id
        return self.pack

    def update_draft(self, project_id, customer_token, pack_id, payload):
        self.update_calls.append(payload)
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


class FakeInvitePublishService:
    def __init__(self) -> None:
        self.create_calls = []
        self.read_calls = []
        self.usage = 0
        self.requested = 0
        self.revoked = False

    async def create_channel_invite_internal(
        self,
        project_id,
        channel_username,
        *,
        title,
    ):
        self.create_calls.append(
            {
                "project_id": project_id,
                "channel_username": channel_username,
                "title": title,
            }
        )
        return TelegramChannelInviteSnapshot(
            link="https://t.me/+experimentInvite",
            usage=self.usage,
            requested=self.requested,
            revoked=self.revoked,
        )

    async def channel_invite_internal(
        self,
        project_id,
        channel_username,
        link,
    ):
        self.read_calls.append(
            {
                "project_id": project_id,
                "channel_username": channel_username,
                "link": link,
            }
        )
        return TelegramChannelInviteSnapshot(
            link=link,
            usage=self.usage,
            requested=self.requested,
            revoked=self.revoked,
        )


class FakeAnalyticsService:
    def __init__(self) -> None:
        self.events = []
        self.error = None

    def ingest_event(self, payload):
        if self.error is not None:
            raise self.error
        self.events.append(payload)
        return SimpleNamespace(event_id=payload.event_id)


@pytest.mark.asyncio
async def test_provision_replaces_public_channel_cta_with_native_invite() -> None:
    pack = _pack()
    packs = FakePackService(pack)
    publish = FakeInvitePublishService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=publish,
        analytics_service=FakeAnalyticsService(),
    )

    attribution = await service.provision_channel_invite(
        pack.project_id,
        "customer-token",
        pack.id,
    )

    assert attribution.status == TelegramNativeAttributionStatus.READY
    assert attribution.channel_username == "femdom"
    assert attribution.source_url == "https://t.me/femdom"
    assert attribution.native_url == "https://t.me/+experimentInvite"
    assert publish.create_calls[0]["channel_username"] == "femdom"
    assert publish.create_calls[0]["title"].startswith("Partizan ")
    assert packs.pack.cta_type == TelegramProfileCTAType.TELEGRAM_CHANNEL_INVITE
    assert packs.pack.cta_value == "https://t.me/+experimentInvite"
    assert packs.pack.bio == "FemDom ↓\nhttps://t.me/+experimentInvite"


@pytest.mark.asyncio
async def test_sync_ingests_cumulative_join_count_for_exact_experiment() -> None:
    pack = _pack()
    packs = FakePackService(pack)
    publish = FakeInvitePublishService()
    analytics = FakeAnalyticsService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=publish,
        analytics_service=analytics,
    )
    await service.provision_channel_invite(
        pack.project_id,
        "customer-token",
        pack.id,
    )

    publish.usage = 3
    synced = await service.sync(pack.project_id, "customer-token", pack.id)

    assert synced.join_count == 3
    assert synced.attributed_join_count == 3
    assert synced.last_delta_joins == 3
    assert synced.analytics_pending is False
    assert len(analytics.events) == 1
    event = analytics.events[0]
    assert event.event_type == "JOIN"
    assert event.experiment_id == pack.experiment_id
    assert event.action_id == pack.action_id
    assert event.properties["count"] == 3
    assert event.properties["delta"] == 3
    assert event.properties["profile_pack_id"] == str(pack.id)


@pytest.mark.asyncio
async def test_internal_action_sync_reuses_bound_native_attribution() -> None:
    pack = _pack()
    packs = FakePackService(pack)
    publish = FakeInvitePublishService()
    analytics = FakeAnalyticsService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=publish,
        analytics_service=analytics,
    )
    await service.provision_channel_invite(
        pack.project_id,
        "customer-token",
        pack.id,
    )

    publish.usage = 2
    synced = await service.sync_for_action_internal(
        pack.project_id,
        pack.action_id,
    )

    assert synced is not None
    assert synced.join_count == 2
    assert synced.attributed_join_count == 2
    assert analytics.events[0].event_type == "JOIN"


@pytest.mark.asyncio
async def test_repeated_sync_with_same_usage_does_not_create_duplicate_join_event() -> None:
    pack = _pack()
    packs = FakePackService(pack)
    publish = FakeInvitePublishService()
    analytics = FakeAnalyticsService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=publish,
        analytics_service=analytics,
    )
    await service.provision_channel_invite(
        pack.project_id,
        "customer-token",
        pack.id,
    )

    publish.usage = 2
    await service.sync(pack.project_id, "customer-token", pack.id)
    second = await service.sync(pack.project_id, "customer-token", pack.id)

    assert second.join_count == 2
    assert second.last_delta_joins == 0
    assert len(analytics.events) == 1


@pytest.mark.asyncio
async def test_join_measurement_stays_pending_until_experiment_is_measurable() -> None:
    pack = _pack()
    packs = FakePackService(pack)
    publish = FakeInvitePublishService()
    analytics = FakeAnalyticsService()
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=packs,
        publish_service=publish,
        analytics_service=analytics,
    )
    await service.provision_channel_invite(
        pack.project_id,
        "customer-token",
        pack.id,
    )

    publish.usage = 4
    analytics.error = ValueError("Distribution analytics require a RUNNING experiment")
    pending = await service.sync(pack.project_id, "customer-token", pack.id)

    assert pending.join_count == 4
    assert pending.attributed_join_count == 0
    assert pending.analytics_pending is True
    assert "Analytics pending" in (pending.last_error or "")

    analytics.error = None
    recovered = await service.sync(pack.project_id, "customer-token", pack.id)

    assert recovered.join_count == 4
    assert recovered.attributed_join_count == 4
    assert recovered.analytics_pending is False
    assert len(analytics.events) == 1
    assert analytics.events[0].properties["count"] == 4


@pytest.mark.asyncio
async def test_provision_requires_plain_public_channel_link() -> None:
    pack = _pack().model_copy(
        update={
            "bio": "FemDom ↓\nhttps://t.me/+existingInvite",
            "cta_type": TelegramProfileCTAType.TELEGRAM_PUBLIC_LINK,
            "cta_value": "https://t.me/+existingInvite",
        }
    )
    service = TelegramNativeAttributionService(
        store=MemoryRuntimeStateStore(),
        pack_service=FakePackService(pack),
        publish_service=FakeInvitePublishService(),
        analytics_service=FakeAnalyticsService(),
    )

    with pytest.raises(TelegramNativeAttributionError, match="public @username"):
        await service.provision_channel_invite(
            pack.project_id,
            "customer-token",
            pack.id,
        )
