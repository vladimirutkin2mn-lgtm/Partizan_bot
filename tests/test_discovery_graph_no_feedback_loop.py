from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.discovery_intelligence import propagate_surface_corroboration
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind


def _item(*, icp_id, canonical_key, scope, audience, confidence, target_url=None):
    now = datetime.now(UTC)
    metadata = {
        "surface_parent_key": "telegram:77",
        "opportunity_scope": scope,
        "actionability": "RESEARCH_ONLY" if scope == "COMMUNITY" else "NEEDS_PREFLIGHT",
        "source_checked_at": now.isoformat(),
        "intelligence_scores": {
            "version": "v2",
            "audience_fit": audience,
            "context_fit": 25.0,
            "actionability": 20.0 if scope == "COMMUNITY" else 70.0,
            "confidence": confidence,
            "ranking_score": 50.0,
            "exploration_candidate": False,
            "execution_permission_inferred": False,
        },
    }
    if target_url:
        metadata["action_target_url"] = target_url
        metadata["action_target_specific"] = True
        metadata["recent_context"] = [
            {
                "url": target_url,
                "published_at": (now - timedelta(hours=1)).isoformat(),
                "text": "Strong recent audience evidence",
            }
        ]
    return DistributionOpportunitySeed(
        icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key=canonical_key,
        title=canonical_key,
        url="https://t.me/example",
        relevance_score=70,
        rationale="graph test",
        metadata=metadata,
        evidence=[],
    )


def test_posts_that_raise_community_do_not_receive_same_pass_boost_back() -> None:
    icp_id = uuid4()
    community = _item(
        icp_id=icp_id,
        canonical_key="telegram:77:community",
        scope="COMMUNITY",
        audience=54.0,
        confidence=49.0,
    )
    first = _item(
        icp_id=icp_id,
        canonical_key="telegram:77:post:1",
        scope="POST",
        audience=80.0,
        confidence=80.0,
        target_url="https://t.me/example/1",
    )
    second = _item(
        icp_id=icp_id,
        canonical_key="telegram:77:post:2",
        scope="POST",
        audience=82.0,
        confidence=82.0,
        target_url="https://t.me/example/2",
    )
    first_before = dict(first.metadata["intelligence_scores"])
    second_before = dict(second.metadata["intelligence_scores"])

    propagated = propagate_surface_corroboration([community, first, second])
    by_key = {item.canonical_key: item for item in propagated}
    community_after = by_key["telegram:77:community"]
    first_after = by_key["telegram:77:post:1"]
    second_after = by_key["telegram:77:post:2"]

    assert community_after.metadata["intelligence_scores"]["audience_fit"] > 54.0
    assert community_after.metadata["intelligence_scores"]["confidence"] > 49.0

    assert first_after.metadata["intelligence_scores"]["audience_fit"] == first_before["audience_fit"]
    assert first_after.metadata["intelligence_scores"]["confidence"] == first_before["confidence"]
    assert second_after.metadata["intelligence_scores"]["audience_fit"] == second_before["audience_fit"]
    assert second_after.metadata["intelligence_scores"]["confidence"] == second_before["confidence"]
    assert first_after.metadata["graph_signal_propagation"]["direction"] == "NONE"
    assert second_after.metadata["graph_signal_propagation"]["direction"] == "NONE"
