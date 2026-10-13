from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.discovery_intelligence import propagate_surface_corroboration
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind


def _scores(audience: float, confidence: float, *, actionability: float = 10.0) -> dict:
    return {
        "version": "v2",
        "audience_fit": audience,
        "context_fit": 30.0,
        "actionability": actionability,
        "confidence": confidence,
        "ranking_score": 50.0,
        "exploration_candidate": False,
        "execution_permission_inferred": False,
    }


def _community(*, icp_id, parent="telegram:42", checked_at=None, audience=62, confidence=65):
    checked_at = checked_at or datetime.now(UTC)
    return DistributionOpportunitySeed(
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key=f"{parent}:community",
        title="Audience community",
        url="https://t.me/example",
        relevance_score=70,
        rationale="Community evidence",
        metadata={
            "surface_parent_key": parent,
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "community_only_reason": "NO_FRESH_ACTION_TARGET",
            "source_checked_at": checked_at.isoformat(),
            "intelligence_scores": _scores(audience, confidence, actionability=20.0),
        },
        evidence=[],
    )


def _post(
    *,
    icp_id,
    message_id: int,
    parent="telegram:42",
    published_at=None,
    audience=70,
    confidence=70,
):
    published_at = published_at or datetime.now(UTC) - timedelta(hours=2)
    target = f"https://t.me/example/{message_id}"
    return DistributionOpportunitySeed(
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key=f"{parent}:post:{message_id}",
        title=f"Post {message_id}",
        url="https://t.me/example",
        relevance_score=75,
        rationale="Post evidence",
        metadata={
            "surface_parent_key": parent,
            "opportunity_scope": "POST",
            "actionability": "NEEDS_PREFLIGHT",
            "action_target_url": target,
            "action_target_specific": True,
            "recent_context": [
                {
                    "url": target,
                    "published_at": published_at.isoformat(),
                    "text": "Relevant audience discussion",
                }
            ],
            "intelligence_scores": _scores(audience, confidence, actionability=70.0),
        },
        evidence=[],
    )


def _by_scope(items, scope):
    return [
        item
        for item in items
        if str(item.metadata.get("opportunity_scope") or "").upper() == scope
    ]


def test_two_recent_strong_posts_raise_parent_community_research_prior_only() -> None:
    icp_id = uuid4()
    community = _community(icp_id=icp_id, audience=48, confidence=50)
    first = _post(icp_id=icp_id, message_id=101, audience=75, confidence=70)
    second = _post(icp_id=icp_id, message_id=102, audience=80, confidence=75)
    before = dict(community.metadata["intelligence_scores"])

    propagated = propagate_surface_corroboration([community, first, second])
    result = _by_scope(propagated, "COMMUNITY")[0]
    scores = result.metadata["intelligence_scores"]
    graph = result.metadata["graph_signal_propagation"]

    assert scores["audience_fit"] > before["audience_fit"]
    assert scores["confidence"] > before["confidence"]
    assert scores["actionability"] == before["actionability"]
    assert scores["context_fit"] == before["context_fit"]
    assert graph["direction"] == "POSTS_TO_COMMUNITY"
    assert graph["source_surface_count"] == 2
    assert graph["actionability_propagated"] is False
    assert graph["execution_permission_inferred"] is False


def test_single_post_cannot_raise_parent_community() -> None:
    icp_id = uuid4()
    community = _community(icp_id=icp_id, audience=48, confidence=50)
    post = _post(icp_id=icp_id, message_id=101, audience=90, confidence=90)
    before = dict(community.metadata["intelligence_scores"])

    propagated = propagate_surface_corroboration([community, post])
    result = _by_scope(propagated, "COMMUNITY")[0]

    assert result.metadata["intelligence_scores"]["audience_fit"] == before["audience_fit"]
    assert result.metadata["intelligence_scores"]["confidence"] == before["confidence"]
    assert result.metadata["graph_signal_propagation"]["direction"] == "NONE"


def test_stale_posts_do_not_propagate_into_community() -> None:
    icp_id = uuid4()
    stale = datetime.now(UTC) - timedelta(days=15)
    community = _community(icp_id=icp_id, audience=48, confidence=50)
    first = _post(icp_id=icp_id, message_id=101, published_at=stale, audience=90, confidence=90)
    second = _post(icp_id=icp_id, message_id=102, published_at=stale, audience=90, confidence=90)

    propagated = propagate_surface_corroboration([community, first, second])
    result = _by_scope(propagated, "COMMUNITY")[0]

    assert result.metadata["intelligence_scores"]["audience_fit"] == 48
    assert result.metadata["intelligence_scores"]["confidence"] == 50
    assert result.metadata["graph_signal_propagation"]["source_surface_count"] == 0


def test_strong_recent_community_gives_small_prior_to_child_post_without_actionability_change() -> None:
    icp_id = uuid4()
    community = _community(icp_id=icp_id, audience=85, confidence=80)
    post = _post(icp_id=icp_id, message_id=101, audience=45, confidence=45)
    before = dict(post.metadata["intelligence_scores"])

    propagated = propagate_surface_corroboration([community, post])
    result = _by_scope(propagated, "POST")[0]
    scores = result.metadata["intelligence_scores"]
    graph = result.metadata["graph_signal_propagation"]

    assert before["audience_fit"] < scores["audience_fit"] <= before["audience_fit"] + 4
    assert before["confidence"] < scores["confidence"] <= before["confidence"] + 4
    assert scores["actionability"] == before["actionability"]
    assert scores["context_fit"] == before["context_fit"]
    assert graph["direction"] == "COMMUNITY_TO_POST"
    assert graph["actionability_propagated"] is False


def test_parent_child_signals_never_cross_icp_boundary() -> None:
    community_icp = uuid4()
    post_icp = uuid4()
    community = _community(icp_id=community_icp, audience=90, confidence=90)
    post = _post(icp_id=post_icp, message_id=101, audience=45, confidence=45)
    before = dict(post.metadata["intelligence_scores"])

    propagated = propagate_surface_corroboration([community, post])
    result = _by_scope(propagated, "POST")[0]

    assert result.metadata["intelligence_scores"]["audience_fit"] == before["audience_fit"]
    assert result.metadata["intelligence_scores"]["confidence"] == before["confidence"]
    assert result.metadata["graph_signal_propagation"]["direction"] == "NONE"


def test_old_community_prior_expires_instead_of_boosting_new_posts() -> None:
    icp_id = uuid4()
    community = _community(
        icp_id=icp_id,
        checked_at=datetime.now(UTC) - timedelta(days=31),
        audience=95,
        confidence=95,
    )
    post = _post(icp_id=icp_id, message_id=101, audience=45, confidence=45)

    propagated = propagate_surface_corroboration([community, post])
    result = _by_scope(propagated, "POST")[0]

    assert result.metadata["intelligence_scores"]["audience_fit"] == 45
    assert result.metadata["intelligence_scores"]["confidence"] == 45
    assert result.metadata["graph_signal_propagation"]["decay_weight"] == 0.0
