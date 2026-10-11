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


def _snapshot(*, title: str, about: str) -> TelegramCommunitySnapshot:
    now = datetime.now(UTC)
    return TelegramCommunitySnapshot(
        entity_id=42,
        username="example",
        title=title,
        kind=TelegramSurfaceKind.CHANNEL,
        url="https://t.me/example",
        about=about,
        member_count=1000,
        source_checked_at=now,
        last_activity_at=now - timedelta(hours=1),
        comment_surface=SurfaceAvailability.AVAILABLE,
        reply_surface=SurfaceAvailability.UNKNOWN,
        standalone_post_surface=SurfaceAvailability.UNAVAILABLE,
        linked_discussion_id=99,
        action_target_url=None,
        recent_context=(
            TelegramRecentContext(
                message_id=123,
                text="Сегодня встречаемся вечером. Подробности внутри.",
                published_at=now - timedelta(hours=1),
                url="https://t.me/example/123",
                matched_terms=(),
            ),
        ),
    )


def test_relevant_community_can_resolve_fresh_post_even_when_post_text_has_no_query_terms() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(
            title="FemDom community",
            about="BDSM знакомства, события и безопасные практики",
        ),
    )

    assert metadata["action_target_specific"] is True
    assert metadata["action_target_url"] == "https://t.me/example/123"
    assert metadata["action_target_resolution"] == "COMMUNITY_RELEVANCE_FRESH_POST"
    assert set(metadata["community_match_terms"]) & {"femdom", "bdsm", "знакомства"}


def test_unrelated_community_does_not_get_fallback_action_target() -> None:
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
