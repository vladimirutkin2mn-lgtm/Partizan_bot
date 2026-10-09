from types import SimpleNamespace
from uuid import uuid4

from app.discovery_relevance_learning import DiscoveryRelevanceLearningService
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="LedgerFlow",
        problem_or_desire="automate bookkeeping for small businesses",
        value_proposition="simpler accounting and tax workflows",
        known_audience=["small business owners", "bookkeepers"],
    )


def _icp():
    return SimpleNamespace(
        title="Small business owners doing their own accounting",
        description="Founders who want simpler bookkeeping",
        pain="manual bookkeeping and tax mistakes",
        trigger="tax deadline or growing transaction volume",
        desired_outcome="reliable accounting with less manual work",
        alternatives=["accountant", "spreadsheet bookkeeping"],
    )


def _seed(key: str, title: str, signals: dict) -> DistributionOpportunitySeed:
    return DistributionOpportunitySeed(
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL,
        canonical_key=key,
        title=title,
        url=f"https://t.me/{key.replace(':', '_')}",
        relevance_score=60,
        rationale="test",
        metadata={"research_signals": signals},
    )


def _strong_signals() -> dict:
    return {
        "fit_ratio": 0.4,
        "pain_ratio": 0.35,
        "trigger_ratio": 0.2,
        "alternative_ratio": 0.0,
        "demand_intent_hits": 1,
        "commercial_intent_hits": 0,
        "independent_evidence_count": 2,
        "confidence": "MEDIUM",
        "matched_terms": ["accounting", "bookkeeping", "tax"],
    }


def _weak_signals() -> dict:
    return {
        "fit_ratio": 0.1,
        "pain_ratio": 0.0,
        "trigger_ratio": 0.0,
        "alternative_ratio": 0.0,
        "demand_intent_hits": 0,
        "commercial_intent_hits": 0,
        "independent_evidence_count": 1,
        "confidence": "LOW",
        "matched_terms": ["owners"],
    }


def test_multi_signal_relevance_passes_and_is_annotated() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store)
    product = _product()

    accepted, rejected = service.filter_and_learn(
        product=product,
        icps=[_icp()],
        opportunities=[
            _seed(
                "telegram:small_business_accounting",
                "Small business accounting help",
                _strong_signals(),
            )
        ],
    )

    assert rejected == []
    assert len(accepted) == 1
    assert accepted[0].metadata["relevance_guard"]["status"] == "PASS"
    assert accepted[0].metadata["relevance_guard"]["reason"] == "MULTI_SIGNAL_RELEVANCE"


def test_irrelevant_community_is_remembered_and_blocked_even_if_it_later_scores_high() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store)
    product = _product()
    key = "telegram:random_crypto_channel"

    accepted, rejected = service.filter_and_learn(
        product=product,
        icps=[_icp()],
        opportunities=[_seed(key, "Random crypto news", _weak_signals())],
    )

    assert accepted == []
    assert rejected[0]["reason"] == "INSUFFICIENT_SEMANTIC_EVIDENCE"

    accepted_again, rejected_again = service.filter_and_learn(
        product=product,
        icps=[_icp()],
        opportunities=[_seed(key, "Random crypto news", _strong_signals())],
    )

    assert accepted_again == []
    assert rejected_again[0]["reason"] == "NEGATIVE_COMMUNITY_MEMORY"


def test_candidate_that_matches_one_icp_is_not_learned_away_by_another_icp() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store)
    product = _product()
    key = "telegram:mixed_fit_community"

    accepted, rejected = service.filter_and_learn(
        product=product,
        icps=[_icp()],
        opportunities=[
            _seed(key, "Mixed community", _weak_signals()),
            _seed(key, "Small business accounting community", _strong_signals()),
        ],
    )

    assert len(accepted) == 1
    assert len(rejected) == 1

    accepted_again, rejected_again = service.filter_and_learn(
        product=product,
        icps=[_icp()],
        opportunities=[
            _seed(key, "Small business accounting community", _strong_signals())
        ],
    )

    assert len(accepted_again) == 1
    assert rejected_again == []


def test_repeated_irrelevant_theme_becomes_negative_signal_for_new_communities() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store)
    product = _product()
    icp = _icp()

    accepted, rejected = service.filter_and_learn(
        product=product,
        icps=[icp],
        opportunities=[
            _seed(
                "telegram:crypto_one",
                "Crypto Airdrop Signals",
                _weak_signals(),
            ),
            _seed(
                "telegram:crypto_two",
                "Crypto Airdrop Trading",
                _weak_signals(),
            ),
        ],
    )

    assert accepted == []
    assert len(rejected) == 2
    snapshot = service.snapshot(product.id)
    assert {"crypto", "airdrop"}.issubset(snapshot["negative_terms"])

    accepted_next, rejected_next = service.filter_and_learn(
        product=product,
        icps=[icp],
        opportunities=[
            _seed(
                "telegram:crypto_three",
                "Crypto Airdrop Community",
                _strong_signals(),
            )
        ],
    )

    assert accepted_next == []
    assert rejected_next[0]["reason"] == "NEGATIVE_THEME_MEMORY"


def test_negative_theme_memory_removes_bad_adaptive_hints_before_search() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store)
    product = _product()
    icp = _icp()

    service.filter_and_learn(
        product=product,
        icps=[icp],
        opportunities=[
            _seed("telegram:crypto_one", "Crypto Airdrop Signals", _weak_signals()),
            _seed("telegram:crypto_two", "Crypto Airdrop Trading", _weak_signals()),
        ],
    )

    filtered = service.filter_hints(
        product.id,
        [
            "crypto founders looking for tools",
            "tax deadline accounting questions",
            "airdrop growth community",
            "tax deadline accounting questions",
        ],
    )

    assert filtered == ["tax deadline accounting questions"]
