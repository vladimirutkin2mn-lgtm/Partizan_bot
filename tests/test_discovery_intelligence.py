from uuid import uuid4

from app.discovery_intelligence import (
    annotate_base_intelligence,
    exploration_slot_count,
    propagate_surface_corroboration,
)
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind


def _opportunity(
    *,
    icp_id=None,
    canonical_key="telegram:adjacent",
    actionability="RESEARCH_ONLY",
    community_only_reason="NO_INTERACTION_SURFACE",
    fit=0.9,
    pain=0.4,
    trigger=0.2,
    alternative=0.0,
    demand_hits=3,
    commercial_hits=0,
    confidence="MEDIUM",
    independent_evidence=2,
    evidence_url=None,
):
    return DistributionOpportunitySeed(
        icp_id=icp_id or uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key=canonical_key,
        title="Adjacent audience community",
        url="https://t.me/adjacent_audience",
        relevance_score=72.0,
        rationale="Audience evidence exists outside the obvious topical context.",
        metadata={
            "opportunity_scope": "COMMUNITY",
            "actionability": actionability,
            "community_only_reason": community_only_reason,
            "research_signals": {
                "fit_ratio": fit,
                "pain_ratio": pain,
                "trigger_ratio": trigger,
                "alternative_ratio": alternative,
                "demand_intent_hits": demand_hits,
                "commercial_intent_hits": commercial_hits,
                "independent_evidence_count": independent_evidence,
                "confidence": confidence,
                "matched_terms": ["audience", "problem", "need"],
            },
        },
        evidence=[{"url": evidence_url}] if evidence_url else [],
    )


def test_scores_separate_audience_context_actionability_and_confidence() -> None:
    item = annotate_base_intelligence([_opportunity()])[0]
    scores = item.metadata["intelligence_scores"]

    assert scores["audience_fit"] > scores["context_fit"]
    assert scores["actionability"] == 10.0
    assert scores["confidence"] == 65.0
    assert scores["exploration_candidate"] is True
    assert scores["execution_permission_inferred"] is False


def test_same_surface_corroboration_raises_audience_and_confidence_not_actionability() -> None:
    first, second = annotate_base_intelligence(
        [
            _opportunity(
                icp_id=uuid4(),
                canonical_key="telegram:same",
                evidence_url="https://evidence.example/one",
            ),
            _opportunity(
                icp_id=uuid4(),
                canonical_key="telegram:same",
                evidence_url="https://evidence.example/two",
            ),
        ]
    )
    before = dict(first.metadata["intelligence_scores"])

    propagated = propagate_surface_corroboration([first, second])
    after = propagated[0].metadata["intelligence_scores"]
    propagation = propagated[0].metadata["signal_propagation"]

    assert after["audience_fit"] > before["audience_fit"]
    assert after["confidence"] > before["confidence"]
    assert after["actionability"] == before["actionability"]
    assert propagation["distinct_icp_count"] == 2
    assert propagation["independent_evidence_urls"] == 2
    assert propagation["actionability_propagated"] is False


def test_repeated_same_evidence_does_not_create_corroboration_bonus() -> None:
    first, second = annotate_base_intelligence(
        [
            _opportunity(
                icp_id=uuid4(),
                canonical_key="telegram:same",
                evidence_url="https://evidence.example/shared",
            ),
            _opportunity(
                icp_id=uuid4(),
                canonical_key="telegram:same",
                evidence_url="https://evidence.example/shared",
            ),
        ]
    )
    before = dict(first.metadata["intelligence_scores"])

    propagated = propagate_surface_corroboration([first, second])
    after = propagated[0].metadata["intelligence_scores"]
    propagation = propagated[0].metadata["signal_propagation"]

    assert after["audience_fit"] == before["audience_fit"]
    assert after["confidence"] == before["confidence"]
    assert propagation["independent_evidence_urls"] == 1
    assert propagation["confidence_bonus"] == 0.0


def test_different_surfaces_do_not_share_corroboration() -> None:
    first, second = annotate_base_intelligence(
        [
            _opportunity(canonical_key="telegram:first", evidence_url="https://evidence.example/1"),
            _opportunity(canonical_key="telegram:second", evidence_url="https://evidence.example/2"),
        ]
    )
    propagated = propagate_surface_corroboration([first, second])

    assert propagated[0].metadata["signal_propagation"]["confidence_bonus"] == 0.0
    assert propagated[1].metadata["signal_propagation"]["confidence_bonus"] == 0.0


def test_exploration_budget_is_bounded_to_roughly_fifteen_percent() -> None:
    assert exploration_slot_count(5) == 0
    assert exploration_slot_count(6) == 1
    assert exploration_slot_count(10) == 2
    assert exploration_slot_count(80) == 12
