from types import SimpleNamespace
from uuid import uuid4

from app.discovery_relevance_learning import DiscoveryRelevanceLearningService
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


def test_telegram_utility_terms_cannot_satisfy_semantic_relevance() -> None:
    service = DiscoveryRelevanceLearningService(MemoryRuntimeStateStore())
    product = SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        problem_or_desire="find relevant FemDom and BDSM communities",
        value_proposition="audience discovery",
        known_audience=["people interested in BDSM"],
    )
    icp = SimpleNamespace(
        title="People interested in FemDom",
        description="BDSM audience",
        pain="hard to find relevant communities",
        trigger="looking for a community",
        desired_outcome="find a relevant place",
        alternatives=[],
    )
    signals = {
        "fit_ratio": 0.5,
        "pain_ratio": 0.3,
        "trigger_ratio": 0.2,
        "alternative_ratio": 0.0,
        "demand_intent_hits": 1,
        "commercial_intent_hits": 0,
        "independent_evidence_count": 3,
        "confidence": "HIGH",
        "matched_terms": ["https", "telegram", "channel", "community", "around"],
    }
    opportunity = DistributionOpportunitySeed(
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key="telegram:unrelated",
        title="Unrelated community",
        url="https://t.me/unrelated",
        relevance_score=80,
        rationale="test",
        metadata={"research_signals": signals},
    )

    accepted, rejected = service.filter_and_learn(
        product=product,
        icps=[icp],
        opportunities=[opportunity],
    )

    assert accepted == []
    assert rejected[0]["reason"] == "INSUFFICIENT_SEMANTIC_EVIDENCE"
