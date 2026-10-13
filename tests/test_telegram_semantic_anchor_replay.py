from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.discovery_relevance_learning import DiscoveryRelevanceLearningService
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="Promoted asset: FemDom",
        problem_or_desire=None,
        value_proposition=None,
        description=(
            "Telegram channel for people interested in FemDom. "
            "Business goal: get paying customers."
        ),
        known_audience=[],
    )


def _icp():
    return SimpleNamespace(
        title="Urgent problem solvers",
        description="A generic generated audience segment",
        pain="A moment makes the problem salient enough to seek a solution",
        trigger="Need to solve the problem now",
        desired_outcome="Resolve the problem",
        alternatives=["manual solution", "generic tool"],
    )


def _signals(matched_terms: list[str], *, confidence: str = "MEDIUM") -> dict:
    return {
        "fit_ratio": 0.5,
        "pain_ratio": 0.75,
        "trigger_ratio": 0.75,
        "alternative_ratio": 0.0,
        "demand_intent_hits": 1,
        "commercial_intent_hits": 0,
        "independent_evidence_count": 2,
        "confidence": confidence,
        "matched_terms": matched_terms,
    }


def _seed(
    matched_terms: list[str],
    *,
    platform: DistributionPlatform = DistributionPlatform.TELEGRAM,
    key: str | None = None,
) -> DistributionOpportunitySeed:
    if platform == DistributionPlatform.TELEGRAM:
        kind = OpportunityKind.CHANNEL
        url = "https://t.me/test_surface"
    elif platform == DistributionPlatform.INSTAGRAM:
        kind = OpportunityKind.CREATOR_ACCOUNT
        url = "https://instagram.com/test_surface"
    else:
        raise AssertionError("unsupported test platform")
    return DistributionOpportunitySeed(
        icp_id=uuid4(),
        platform=platform,
        kind=kind,
        canonical_key=key or f"{platform.value.lower()}:test",
        title="Observed surface",
        url=url,
        relevance_score=70,
        rationale="test",
        metadata={"research_signals": _signals(matched_terms)},
    )


def _filter(seed: DistributionOpportunitySeed):
    service = DiscoveryRelevanceLearningService(MemoryRuntimeStateStore())
    return service.filter_and_learn(
        product=_product(),
        icps=[_icp()],
        opportunities=[seed],
    )


def test_single_direct_product_anchor_can_pass_with_strong_supporting_signals() -> None:
    accepted, rejected = _filter(
        _seed(["channel", "femdom", "group", "https", "telegram", "test"])
    )

    assert rejected == []
    assert len(accepted) == 1
    guard = accepted[0].metadata["relevance_guard"]
    assert guard["status"] == "PASS"
    assert guard["direct_product_anchor_count"] == 1


@pytest.mark.parametrize(
    "matched_terms",
    [
        ["urgent", "problem", "solvers", "segment"],
        ["high", "frequency", "power", "users"],
        ["manual", "generic", "tool", "product"],
        ["business", "problem", "solvers"],
    ],
)
def test_generated_icp_boilerplate_cannot_satisfy_telegram_semantic_gate(
    matched_terms: list[str],
) -> None:
    accepted, rejected = _filter(_seed(matched_terms))

    assert accepted == []
    assert rejected[0]["reason"] == "INSUFFICIENT_SEMANTIC_EVIDENCE"


def test_anchorless_adjacent_context_still_passes_with_two_real_terms() -> None:
    accepted, rejected = _filter(_seed(["crypto", "founders"]))

    assert rejected == []
    assert len(accepted) == 1
    assert accepted[0].metadata["relevance_guard"]["direct_product_anchor_count"] == 0


def test_single_anchorless_context_term_does_not_get_direct_anchor_exception() -> None:
    accepted, rejected = _filter(_seed(["founders"]))

    assert accepted == []
    assert rejected[0]["reason"] == "INSUFFICIENT_SEMANTIC_EVIDENCE"


def test_non_telegram_semantic_contract_is_unchanged() -> None:
    accepted, rejected = _filter(
        _seed(
            ["manual", "generic", "tool", "product"],
            platform=DistributionPlatform.INSTAGRAM,
            key="instagram:test",
        )
    )

    assert rejected == []
    assert len(accepted) == 1
