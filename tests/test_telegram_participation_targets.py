from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.community_first_live_opportunities import sync_community_first_live_opportunities
from app.customer_live_opportunities import (
    CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE,
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerLiveOpportunityService,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightService
from app.telegram_participation_target import (
    TelegramParticipationMode,
    select_telegram_participation_target,
)
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


def _context(message_id: int, age_hours: int = 1) -> TelegramRecentContext:
    published = datetime.now(UTC) - timedelta(hours=age_hours)
    return TelegramRecentContext(
        message_id=message_id,
        text=f"message {message_id}",
        published_at=published,
        url=f"https://t.me/community/{message_id}",
    )


def _snapshot(
    *,
    kind: TelegramSurfaceKind,
    recent_context: tuple[TelegramRecentContext, ...] = (),
    comment: SurfaceAvailability = SurfaceAvailability.UNKNOWN,
    reply: SurfaceAvailability = SurfaceAvailability.UNKNOWN,
    standalone: SurfaceAvailability = SurfaceAvailability.UNKNOWN,
) -> TelegramCommunitySnapshot:
    return TelegramCommunitySnapshot(
        entity_id=42,
        username="community",
        title="Community",
        kind=kind,
        url="https://t.me/community",
        comment_surface=comment,
        reply_surface=reply,
        standalone_post_surface=standalone,
        recent_context=recent_context,
    )


def test_channel_does_not_fall_back_to_older_post_when_latest_is_blocked() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.CHANNEL,
        comment=SurfaceAvailability.AVAILABLE,
        recent_context=(_context(147, 1), _context(111, 2)),
    )

    target = select_telegram_participation_target(
        snapshot,
        blocked_urls={"https://t.me/community/147"},
    )

    assert target.mode is None
    assert target.target_url is None
    assert target.reason == "LATEST_POST_BLOCKED"
    assert target.actionability == "RESEARCH_ONLY"


def test_channel_uses_highest_native_message_id_not_dates_or_search_relevance() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.CHANNEL,
        comment=SurfaceAvailability.AVAILABLE,
        recent_context=(
            TelegramRecentContext(
                message_id=147,
                text="",  # Native media posts can lack a text caption.
                published_at=None,
                url="https://t.me/community/147",
            ),
            _context(111, 1),
        ),
    )
    target = select_telegram_participation_target(snapshot)
    assert target.mode == TelegramParticipationMode.CHANNEL_COMMENT
    assert target.target_url == "https://t.me/community/147"
    assert target.target_published_at is None


def test_channel_does_not_fall_back_if_latest_post_has_no_url() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.CHANNEL,
        comment=SurfaceAvailability.AVAILABLE,
        recent_context=(
            TelegramRecentContext(
                message_id=147, text="video", published_at=None, url=None,
            ),
            _context(111, 2),
        ),
    )
    target = select_telegram_participation_target(snapshot)
    assert target.target_url is None
    assert target.reason == "LATEST_POST_URL_UNKNOWN"


def test_group_prefers_standalone_message_even_with_recent_messages() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.GROUP,
        reply=SurfaceAvailability.AVAILABLE,
        standalone=SurfaceAvailability.AVAILABLE,
        recent_context=(_context(25),),
    )

    target = select_telegram_participation_target(snapshot)

    assert target.mode == TelegramParticipationMode.STANDALONE_POST
    assert target.target_url == "https://t.me/community"
    assert target.target_specific is False
    assert target.actionability == "NEEDS_PREFLIGHT"


def test_group_can_explicitly_select_contextual_reply_when_requested() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.GROUP,
        reply=SurfaceAvailability.AVAILABLE,
        standalone=SurfaceAvailability.AVAILABLE,
        recent_context=(_context(25),),
    )
    target = select_telegram_participation_target(
        snapshot, prefer_contextual_reply=True,
    )
    assert target.mode == TelegramParticipationMode.GROUP_REPLY
    assert target.target_url == "https://t.me/community/25"
    assert target.target_specific is True


def test_group_falls_back_to_standalone_post_when_message_target_is_blocked() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.GROUP,
        reply=SurfaceAvailability.AVAILABLE,
        standalone=SurfaceAvailability.AVAILABLE,
        recent_context=(_context(25),),
    )

    target = select_telegram_participation_target(
        snapshot,
        blocked_urls={"https://t.me/community/25"},
    )

    assert target.mode == TelegramParticipationMode.STANDALONE_POST
    assert target.target_url == "https://t.me/community"
    assert target.target_specific is False
    assert target.reason is None


def test_group_can_be_actionable_without_any_recent_message() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.GROUP,
        reply=SurfaceAvailability.AVAILABLE,
        standalone=SurfaceAvailability.AVAILABLE,
        recent_context=(),
    )

    target = select_telegram_participation_target(snapshot)

    assert target.mode == TelegramParticipationMode.STANDALONE_POST
    assert target.target_url == "https://t.me/community"
    assert target.resolution == "COMMUNITY_STANDALONE_POST"


def test_group_reply_does_not_fall_back_to_older_message_when_latest_is_blocked() -> None:
    snapshot = _snapshot(
        kind=TelegramSurfaceKind.GROUP,
        reply=SurfaceAvailability.AVAILABLE,
        standalone=SurfaceAvailability.UNAVAILABLE,
        recent_context=(_context(147), _context(111, 2)),
    )
    target = select_telegram_participation_target(
        snapshot, blocked_urls={"https://t.me/community/147"}
    )
    assert target.target_url is None
    assert target.reason == "LATEST_POST_BLOCKED"


class FakeGroupClient:
    def __init__(self, *, message_id: int | None = None) -> None:
        self.message_id = message_id
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_user_authorized(self) -> bool:
        return True

    async def get_entity(self, handle: str):
        return SimpleNamespace(
            id=42,
            username=handle.lstrip("@"),
            megagroup=True,
            default_banned_rights=None,
        )

    async def get_messages(self, entity, *, ids: int):
        del entity
        if self.message_id is None or ids != self.message_id:
            return None
        return SimpleNamespace(id=ids)

    async def __call__(self, request):
        del request
        return SimpleNamespace(participant=SimpleNamespace(banned_rights=None))


@pytest.mark.asyncio
async def test_group_standalone_preflight_does_not_require_message_url() -> None:
    client = FakeGroupClient()
    service = TelegramOpportunityPreflightService(
        session_provider=lambda project_id: "session",
        client_factory=lambda session: client,
        restriction_checker=lambda project_id, handle: None,
    )

    result = await service.check_target(
        uuid4(),
        "https://t.me/community",
        TelegramParticipationMode.STANDALONE_POST.value,
    )

    assert result.status == "READY"
    assert result.post_id is None
    assert result.action_target_mode == "STANDALONE_POST"
    assert client.connected is False


@pytest.mark.asyncio
async def test_group_reply_preflight_requires_exact_existing_message() -> None:
    client = FakeGroupClient(message_id=25)
    service = TelegramOpportunityPreflightService(
        session_provider=lambda project_id: "session",
        client_factory=lambda session: client,
        restriction_checker=lambda project_id, handle: None,
    )

    result = await service.check_target(
        uuid4(),
        "https://t.me/community/25",
        TelegramParticipationMode.GROUP_REPLY.value,
    )

    assert result.status == "READY"
    assert result.post_id == 25
    assert result.action_target_mode == "GROUP_REPLY"


def test_live_sync_preserves_standalone_post_action_kind(monkeypatch) -> None:
    store = MemoryRuntimeStateStore()
    live_service = CustomerLiveOpportunityService(store)
    monkeypatch.setattr(
        "app.community_first_live_opportunities.customer_live_opportunity_service",
        live_service,
    )
    product_id = uuid4()
    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
            "deleted_at": None,
        },
    )
    opportunity = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        title="Community",
        relevance_score=88.0,
        rationale="Audience fit",
        metadata={
            "source_checked_at": datetime.now(UTC).isoformat(),
            "telegram_entity_id": 42,
            "handle": "community",
            "action_target_url": "https://t.me/community",
            "action_target_specific": False,
            "action_target_mode": "STANDALONE_POST",
        },
    )

    synced = sync_community_first_live_opportunities(
        product_id,
        [opportunity],
        store=store,
    )

    assert synced == 1
    rows = store.list_namespace(CUSTOMER_LIVE_OPPORTUNITY_NAMESPACE)
    assert len(rows) == 1
    assert rows[0]["kind"] == "STANDALONE_POST"
    assert rows[0]["url"] == "https://t.me/community"
    assert rows[0]["metadata"]["action_target_mode"] == "STANDALONE_POST"
