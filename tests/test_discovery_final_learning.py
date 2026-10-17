from types import SimpleNamespace
from uuid import uuid4

from app.discovery_relevance_learning import (
    DISCOVERY_NEGATIVE_LEARNING_NAMESPACE,
    DiscoveryRelevanceLearningService,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="FemDom",
        problem_or_desire="find femdom relationships",
        value_proposition="dating for compatible people",
        description="",
        known_audience=["people interested in femdom"],
    )


def _opportunity(*, community_fit_pass: bool = True):
    return SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        canonical_key="telegram:123:community",
        title="FemDom community",
        metadata={
            "community_fit_pass": community_fit_pass,
            "research_signals": {},
        },
    )


def _rejection() -> dict:
    return {
        "platform": DistributionPlatform.TELEGRAM.value,
        "canonical_key": "telegram:123:community",
        "title": "FemDom community",
        "reason": "INSUFFICIENT_SEMANTIC_EVIDENCE",
        "signal_families": [],
    }


def test_pure_filter_does_not_write_negative_memory() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store=store)
    product = _product()

    for _ in range(3):
        accepted, rejected = service.filter(
            product=product,
            opportunities=[_opportunity()],
        )
        assert accepted == []
        assert len(rejected) == 1

    assert service.snapshot(product.id)["blocked_keys"] == set()
    assert store.list_namespace(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE) == []


def test_finally_accepted_community_never_learns_its_provisional_rejection() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store=store)
    product = _product()
    final_accepted = [_opportunity()]

    result_counts = []
    for _ in range(3):
        final_rejections = service.learn_final_rejections(
            product=product,
            icps=[],
            accepted=final_accepted,
            rejections=[_rejection()],
        )
        result_counts.append(1 if final_accepted and not final_rejections else 0)

    assert result_counts == [1, 1, 1]
    assert service.snapshot(product.id)["blocked_keys"] == set()
    assert store.list_namespace(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE) == []


def test_true_final_rejection_still_learns_and_blocks_after_repetition() -> None:
    store = MemoryRuntimeStateStore()
    service = DiscoveryRelevanceLearningService(store=store)
    product = _product()

    for _ in range(2):
        final_rejections = service.learn_final_rejections(
            product=product,
            icps=[],
            accepted=[],
            rejections=[_rejection()],
        )
        assert len(final_rejections) == 1

    assert service.snapshot(product.id)["blocked_keys"] == {
        "telegram|telegram:123:community"
    }
