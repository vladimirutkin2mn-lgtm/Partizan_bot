from types import SimpleNamespace

from app.distribution_types import DistributionPlatform
from app.manual_discovery_trace import (
    _failure_rows,
    _filters,
    _selected_metadata,
    _telegram_normalization_diagnostic,
    _trace_decision,
)


def _product():
    return SimpleNamespace(
        name="Promoted asset: FemDom",
        problem_or_desire=None,
        value_proposition=None,
        description=(
            "Telegram channel for people interested in FemDom. "
            "Business goal: get paying customers."
        ),
        known_audience=[],
    )


def _opportunity():
    return SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        canonical_key="telegram:2076691209:community",
        title="FEMDOM Знакомства🌶",
        metadata={
            "research_signals": {
                "confidence": "MEDIUM",
                "independent_evidence_count": 2,
                "matched_terms": [
                    "channel",
                    "femdom",
                    "goal",
                    "group",
                    "https",
                    "product",
                    "telegram",
                ],
                "fit_ratio": 0.583,
                "pain_ratio": 0.875,
                "trigger_ratio": 0.875,
                "alternative_ratio": 0.0,
                "commercial_intent_hits": 0,
                "demand_intent_hits": 1,
            }
        },
    )


def test_trace_uses_direct_product_anchor_path_like_production_gate() -> None:
    decision = _trace_decision(
        _opportunity(),
        {"blocked_keys": set(), "negative_terms": set()},
        _product(),
    )

    assert decision["accepted"] is True
    assert decision["reason"] == "MULTI_SIGNAL_RELEVANCE"
    assert decision["direct_product_anchor_count"] == 1


def test_trace_filter_description_documents_direct_product_anchor_exception() -> None:
    semantic_gate = _filters()["semantic_gate"]

    assert semantic_gate["minimum_matched_terms"] == 2
    assert semantic_gate["direct_product_anchor_minimum_matched_terms"] == 1
    assert "DIRECT_PRODUCT_ANCHOR" in semantic_gate["pass_paths"]


def test_trace_explains_non_telegram_search_hit_rejection() -> None:
    result = _telegram_normalization_diagnostic(
        "https://example.com/article-about-femdom"
    )

    assert result == {
        "normalized": False,
        "normalization_reason": "NON_TELEGRAM_HOST",
        "host": "example.com",
    }


def test_trace_explains_unsupported_telegram_invite_link() -> None:
    result = _telegram_normalization_diagnostic("https://t.me/+InviteToken")

    assert result["normalized"] is False
    assert result["normalization_reason"] == "INVITE_LINK_UNSUPPORTED"


def test_trace_normalization_matches_public_post_to_channel_slug() -> None:
    result = _telegram_normalization_diagnostic("https://t.me/s/a_sfera/2106")

    assert result["normalized"] is True
    assert result["normalization_reason"] == "TELEGRAM_SLUG_ACCEPTED"
    assert result["handle"] == "a_sfera"
    assert result["canonical_url"] == "https://t.me/a_sfera"


def test_trace_keeps_actionability_diagnostics_and_recent_context() -> None:
    recent_context = [
        {
            "message_id": 455,
            "text": "recent message",
            "published_at": "2026-10-13T12:00:00+00:00",
            "url": "https://t.me/thisisxtasia/455",
            "matched_terms": [],
        }
    ]
    result = _selected_metadata(
        {
            "handle": "thisisxtasia",
            "action_target_url": "https://t.me/thisisxtasia/455",
            "action_target_specific": True,
            "action_target_resolution": "DIRECT_MESSAGE_MATCH",
            "action_target_quality": "FRESH_CONVERSATION",
            "conversation_signals": ["QUESTION", "REQUEST"],
            "target_match_terms": ["femdom"],
            "opportunity_scope": "POST",
            "actionability": "NEEDS_PREFLIGHT",
            "community_match_terms": ["femdom"],
            "linked_discussion_id": 2862464487,
            "surface_capabilities": {
                "comment": "AVAILABLE",
                "reply": "UNKNOWN",
                "standalone_post": "UNAVAILABLE",
                "publisher_permission_verified": False,
            },
            "recent_context": recent_context,
            "unrelated_internal_field": "do not expose",
        }
    )

    assert result["action_target_quality"] == "FRESH_CONVERSATION"
    assert result["conversation_signals"] == ["QUESTION", "REQUEST"]
    assert result["target_match_terms"] == ["femdom"]
    assert result["community_match_terms"] == ["femdom"]
    assert result["surface_capabilities"]["comment"] == "AVAILABLE"
    assert result["recent_context"] == recent_context
    assert "unrelated_internal_field" not in result


def test_trace_serializes_bounded_search_failure_message() -> None:
    failure = SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        query="site:t.me FemDom",
        error_type="RateLimitError",
        message="search: Error code: 429 - requests per minute exceeded" + "x" * 700,
    )

    rows = _failure_rows([failure])

    assert rows[0]["platform"] == "TELEGRAM"
    assert rows[0]["query"] == "site:t.me FemDom"
    assert rows[0]["error_type"] == "RateLimitError"
    assert rows[0]["message"].startswith("search: Error code: 429")
    assert len(rows[0]["message"]) == 480
