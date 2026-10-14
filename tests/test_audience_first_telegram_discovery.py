from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.distribution_types import OpportunityKind
from app.platform_discovery import PlatformDiscoveryRequest
from app.search import DiscoveryQuery, SourceClass
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        problem_or_desire="знакомство с femdom и BDSM",
        value_proposition="безопасно найти близкое по интересам сообщество",
        description="FemDom product for people exploring BDSM dynamics",
        known_audience=["новички в BDSM", "люди с интересом к femdom"],
        market="Russia",
        language="Russian",
    )


def _generic_icp():
    return SimpleNamespace(
        id=uuid4(),
        title="Urgent problem solvers",
        description="People actively looking for an answer",
        pain="hard to find the right place",
        trigger="ready to act now",
        alternatives=["manual solution", "generic tool"],
    )


def test_every_expanded_query_keeps_product_anchor_even_with_generic_icp() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    requests = adapter.build_requests(_product(), _generic_icp())

    assert len(requests) == 6
    for request in requests:
        query = request.discovery_query.query.casefold()
        assert "femdom" in query
        assert "bdsm" in query


def _request() -> PlatformDiscoveryRequest:
    return PlatformDiscoveryRequest(
        platform=SimpleNamespace(value="TELEGRAM"),
        kind=OpportunityKind.CHANNEL,
        topic="FemDom BDSM знакомства безопасное сообщество",
        discovery_query=DiscoveryQuery(
            SourceClass.COMMUNITY,
            "site:t.me FemDom BDSM знакомства безопасное сообщество Telegram channel",
        ),
    )


def _snapshot(
    *,
    title: str,
    about: str,
    kind: TelegramSurfaceKind = TelegramSurfaceKind.CHANNEL,
    linked_discussion_id: int | None = 99,
    message_text: str = "Сегодня встречаемся вечером. Подробности внутри.",
    matched_terms: tuple[str, ...] = (),
) -> TelegramCommunitySnapshot:
    now = datetime.now(UTC)
    is_group = kind == TelegramSurfaceKind.GROUP
    has_discussion = linked_discussion_id is not None
    return TelegramCommunitySnapshot(
        entity_id=42,
        username="example",
        title=title,
        kind=kind,
        url="https://t.me/example",
        about=about,
        member_count=1000,
        source_checked_at=now,
        last_activity_at=now - timedelta(hours=1),
        comment_surface=(
            SurfaceAvailability.AVAILABLE
            if kind == TelegramSurfaceKind.CHANNEL and has_discussion
            else SurfaceAvailability.UNAVAILABLE
            if kind == TelegramSurfaceKind.CHANNEL
            else SurfaceAvailability.UNKNOWN
        ),
        reply_surface=(
            SurfaceAvailability.AVAILABLE if is_group else SurfaceAvailability.UNKNOWN
        ),
        standalone_post_surface=(
            SurfaceAvailability.AVAILABLE if is_group else SurfaceAvailability.UNAVAILABLE
        ),
        linked_discussion_id=linked_discussion_id,
        action_target_url=None,
        recent_context=(
            TelegramRecentContext(
                message_id=123,
                text=message_text,
                published_at=now - timedelta(hours=1),
                url="https://t.me/example/123",
                matched_terms=matched_terms,
            ),
        ),
    )


def test_relevant_community_keeps_non_conversational_fresh_post_as_research_only() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(
            title="FemDom community",
            about="BDSM знакомства, события и безопасные практики",
        ),
    )

    assert metadata["action_target_specific"] is False
    assert metadata["action_target_url"] is None
    assert metadata["action_target_resolution"] == "COMMUNITY_ONLY"
    assert metadata["opportunity_scope"] == "COMMUNITY"
    assert metadata["actionability"] == "RESEARCH_ONLY"
    assert metadata["community_only_reason"] == "NO_FRESH_CONVERSATIONAL_ACTION_TARGET"
    assert set(metadata["community_match_terms"]) & {"femdom", "bdsm", "знакомства"}


def test_unrelated_community_stays_as_research_only_community() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(
            title="Python jobs",
            about="Backend vacancies, coding interviews and engineering news",
        ),
    )

    assert metadata["action_target_specific"] is False
    assert metadata["action_target_url"] is None
    assert metadata["action_target_resolution"] == "COMMUNITY_ONLY"
    assert metadata["opportunity_scope"] == "COMMUNITY"
    assert metadata["actionability"] == "RESEARCH_ONLY"
    assert metadata["community_only_reason"] == "NO_COMMUNITY_OVERLAP"


def test_relevant_broadcast_without_discussion_never_gets_fallback_action_target() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(
            title="FemDom community",
            about="BDSM знакомства, события и безопасные практики",
            kind=TelegramSurfaceKind.CHANNEL,
            linked_discussion_id=None,
        ),
    )

    assert metadata["action_target_specific"] is False
    assert metadata["action_target_url"] is None
    assert metadata["action_target_resolution"] == "COMMUNITY_ONLY"
    assert metadata["opportunity_scope"] == "COMMUNITY"
    assert metadata["actionability"] == "RESEARCH_ONLY"
    assert metadata["community_only_reason"] == "NO_INTERACTION_SURFACE"
    assert set(metadata["community_match_terms"]) & {"femdom", "bdsm", "знакомства"}


def test_relevant_group_can_use_conversation_ready_post_without_linked_discussion() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(
            title="FemDom group",
            about="BDSM знакомства, события и безопасные практики",
            kind=TelegramSurfaceKind.GROUP,
            linked_discussion_id=None,
            message_text="Кто-нибудь может посоветовать FemDom-сообщество для новичка?",
            matched_terms=("femdom",),
        ),
    )

    assert metadata["action_target_specific"] is True
    assert metadata["action_target_url"] == "https://t.me/example/123"
    assert metadata["action_target_resolution"] == "COMMUNITY_RELEVANCE_FRESH_CONVERSATION"
    assert metadata["action_target_quality"] == "FRESH_CONVERSATION"
    assert metadata["opportunity_scope"] == "POST"
    assert metadata["actionability"] == "NEEDS_PREFLIGHT"
