from datetime import UTC, datetime, timedelta

from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import PlatformDiscoveryRequest
from app.search import DiscoveryQuery, SourceClass
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


def _request() -> PlatformDiscoveryRequest:
    return PlatformDiscoveryRequest(
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        topic="FemDom discussion comments questions",
        discovery_query=DiscoveryQuery(
            SourceClass.COMMUNITY,
            "site:t.me FemDom discussion comments questions",
        ),
    )


def _snapshot_contexts(
    contexts: tuple[TelegramRecentContext, ...],
    *,
    action_target_url: str | None,
) -> TelegramCommunitySnapshot:
    return TelegramCommunitySnapshot(
        entity_id=12345,
        username="femdom_example",
        title="FemDom community",
        kind=TelegramSurfaceKind.CHANNEL,
        url="https://t.me/femdom_example",
        about="FemDom discussion community",
        member_count=100,
        source_checked_at=datetime.now(UTC),
        last_activity_at=max(
            (item.published_at for item in contexts if item.published_at is not None),
            default=None,
        ),
        comment_surface=SurfaceAvailability.AVAILABLE,
        reply_surface=SurfaceAvailability.UNKNOWN,
        standalone_post_surface=SurfaceAvailability.UNAVAILABLE,
        linked_discussion_id=98765,
        action_target_url=action_target_url,
        recent_context=contexts,
    )


def _snapshot(
    context: TelegramRecentContext,
    *,
    action_target_url: str | None,
) -> TelegramCommunitySnapshot:
    return _snapshot_contexts((context,), action_target_url=action_target_url)


def _context(*, age: timedelta, text: str, message_id: int = 101) -> TelegramRecentContext:
    url = f"https://t.me/femdom_example/{message_id}"
    return TelegramRecentContext(
        message_id=message_id,
        text=text,
        published_at=datetime.now(UTC) - age,
        url=url,
        matched_terms=("femdom",),
    )


def test_fresh_conversational_direct_match_remains_specific() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    context = _context(
        age=timedelta(days=2),
        text="Can anyone share advice about FemDom community etiquette?",
    )

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(context, action_target_url=context.url),
    )

    assert metadata["action_target_url"] == context.url
    assert metadata["action_target_specific"] is True
    assert metadata["action_target_resolution"] == "DIRECT_MESSAGE_MATCH"
    assert metadata["action_target_quality"] == "FRESH_CONVERSATION"
    assert metadata["conversation_signals"] == ["QUESTION", "REQUEST"]
    assert metadata["target_match_terms"] == ["femdom"]
    assert metadata["opportunity_scope"] == "POST"
    assert metadata["actionability"] == "NEEDS_PREFLIGHT"


def test_stale_direct_match_is_not_actionable_even_if_native_connector_selected_it() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    context = _context(age=timedelta(days=10), text="Any FemDom advice from last week?")

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(context, action_target_url=context.url),
    )

    assert metadata["action_target_url"] is None
    assert metadata["action_target_specific"] is False
    assert metadata["community_only_reason"] == "DIRECT_TARGET_STALE"
    assert metadata["opportunity_scope"] == "COMMUNITY"
    assert metadata["actionability"] == "RESEARCH_ONLY"


def test_media_only_direct_match_is_not_actionable() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    context = _context(age=timedelta(days=1), text="")

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(context, action_target_url=context.url),
    )

    assert metadata["action_target_url"] is None
    assert metadata["community_only_reason"] == "DIRECT_TARGET_EMPTY_TEXT"
    assert metadata["actionability"] == "RESEARCH_ONLY"


def test_fresh_broadcast_without_conversation_cue_is_not_actionable() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    context = _context(
        age=timedelta(hours=1),
        text="FemDom daily roundup and community news",
    )

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(context, action_target_url=context.url),
    )

    assert metadata["action_target_url"] is None
    assert metadata["community_only_reason"] == "DIRECT_TARGET_NO_CONVERSATION_CUE"
    assert metadata["actionability"] == "RESEARCH_ONLY"


def test_promotional_question_is_not_treated_as_conversation_opportunity() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    context = _context(
        age=timedelta(hours=1),
        text="Want a FemDom special offer? Join now and DM me: https://example.com",
    )

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(context, action_target_url=context.url),
    )

    assert metadata["action_target_url"] is None
    assert metadata["community_only_reason"] == "DIRECT_TARGET_PROMOTIONAL"
    assert metadata["actionability"] == "RESEARCH_ONLY"


def test_direct_broadcast_can_recover_nearby_real_conversation() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    direct = _context(
        age=timedelta(hours=1),
        text="FemDom daily roundup and community news",
        message_id=101,
    )
    conversation = _context(
        age=timedelta(hours=2),
        text="Can anyone recommend a FemDom-friendly meetup?",
        message_id=102,
    )

    metadata = adapter._native_metadata(
        _request(),
        _snapshot_contexts(
            (direct, conversation),
            action_target_url=direct.url,
        ),
    )

    assert metadata["action_target_url"] == conversation.url
    assert metadata["action_target_specific"] is True
    assert metadata["action_target_resolution"] == "RECOVERED_FRESH_CONVERSATION"
    assert metadata["action_target_quality"] == "FRESH_CONVERSATION"
    assert "QUESTION" in metadata["conversation_signals"]
    assert metadata["actionability"] == "NEEDS_PREFLIGHT"


def test_media_only_fallback_is_not_promoted_to_specific_post() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    context = _context(age=timedelta(hours=3), text="")

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(context, action_target_url=None),
    )

    assert metadata["action_target_url"] is None
    assert metadata["action_target_specific"] is False
    assert metadata["community_only_reason"] == "NO_FRESH_CONVERSATIONAL_ACTION_TARGET"
    assert metadata["actionability"] == "RESEARCH_ONLY"


def test_fallback_can_select_fresh_semantic_conversation() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    context = _context(
        age=timedelta(hours=3),
        text="I need advice about FemDom community etiquette",
    )

    metadata = adapter._native_metadata(
        _request(),
        _snapshot(context, action_target_url=None),
    )

    assert metadata["action_target_url"] == context.url
    assert metadata["action_target_specific"] is True
    assert metadata["action_target_resolution"] == "COMMUNITY_RELEVANCE_FRESH_CONVERSATION"
    assert metadata["action_target_quality"] == "FRESH_CONVERSATION"
    assert metadata["conversation_signals"] == ["REQUEST"]
    assert metadata["opportunity_scope"] == "POST"
    assert metadata["actionability"] == "NEEDS_PREFLIGHT"
