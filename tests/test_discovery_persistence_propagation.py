from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.discovery_persistence_propagation import recompute_persisted_discovery_signals
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind


def _scores(audience, confidence, actionability):
    return {
        "version": "v2",
        "audience_fit": audience,
        "context_fit": 25.0,
        "actionability": actionability,
        "confidence": confidence,
        "ranking_score": 40.0,
        "exploration_candidate": False,
        "execution_permission_inferred": False,
    }


def _community(icp_id):
    now = datetime.now(UTC)
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:500:community",
        title="Community",
        url="https://t.me/example",
        relevance_score=45,
        rationale="community",
        metadata={
            "surface_parent_key": "telegram:500",
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "community_only_reason": "NO_FRESH_ACTION_TARGET",
            "source_checked_at": now.isoformat(),
            "intelligence_scores": _scores(48.0, 50.0, 20.0),
        },
        evidence=[],
    )


def _post(icp_id, message_id, audience=80.0, confidence=75.0):
    now = datetime.now(UTC)
    target = f"https://t.me/example/{message_id}"
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key=f"telegram:500:post:{message_id}",
        title=f"Post {message_id}",
        url="https://t.me/example",
        relevance_score=80,
        rationale="post",
        metadata={
            "surface_parent_key": "telegram:500",
            "opportunity_scope": "POST",
            "actionability": "NEEDS_PREFLIGHT",
            "action_target_url": target,
            "action_target_specific": True,
            "recent_context": [
                {
                    "url": target,
                    "published_at": (now - timedelta(hours=1)).isoformat(),
                    "text": "recent audience signal",
                }
            ],
            "intelligence_scores": _scores(audience, confidence, 70.0),
        },
        evidence=[],
    )


def _find(items, suffix):
    return next(item for item in items if item.canonical_key.endswith(suffix))


def test_recompute_connects_surfaces_accumulated_across_rounds() -> None:
    icp_id = uuid4()
    first_round = recompute_persisted_discovery_signals([_community(icp_id)])
    community_before = _find(first_round, ":community")
    assert community_before.metadata["intelligence_scores"]["audience_fit"] == 48.0

    merged = first_round + [_post(icp_id, 1), _post(icp_id, 2)]
    second_round = recompute_persisted_discovery_signals(merged)
    community_after = _find(second_round, ":community")

    assert community_after.metadata["intelligence_scores"]["audience_fit"] > 48.0
    assert community_after.metadata["intelligence_scores"]["confidence"] > 50.0
    assert community_after.metadata["graph_signal_propagation"]["direction"] == "POSTS_TO_COMMUNITY"


def test_recompute_is_idempotent_and_does_not_compound_prior_bonuses() -> None:
    icp_id = uuid4()
    original = [_community(icp_id), _post(icp_id, 1), _post(icp_id, 2)]

    first = recompute_persisted_discovery_signals(original)
    second = recompute_persisted_discovery_signals(first)
    third = recompute_persisted_discovery_signals(second)

    first_community = _find(first, ":community")
    second_community = _find(second, ":community")
    third_community = _find(third, ":community")

    first_scores = first_community.metadata["intelligence_scores"]
    second_scores = second_community.metadata["intelligence_scores"]
    third_scores = third_community.metadata["intelligence_scores"]

    assert second_scores["audience_fit"] == first_scores["audience_fit"]
    assert second_scores["confidence"] == first_scores["confidence"]
    assert third_scores["audience_fit"] == first_scores["audience_fit"]
    assert third_scores["confidence"] == first_scores["confidence"]


def test_recompute_reranks_accumulated_map_by_intelligence_not_legacy_relevance() -> None:
    icp_id = uuid4()
    community = _community(icp_id)
    weak = _post(icp_id, 1, audience=45.0, confidence=45.0)
    strong = _post(icp_id, 2, audience=95.0, confidence=95.0)
    weak.relevance_score = 99
    strong.relevance_score = 40

    ranked = recompute_persisted_discovery_signals([community, weak, strong])
    keys = [item.canonical_key for item in ranked]

    assert keys.index("telegram:500:post:2") < keys.index("telegram:500:post:1")
