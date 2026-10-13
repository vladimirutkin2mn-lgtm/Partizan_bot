from types import SimpleNamespace
from uuid import uuid4

from app.distribution_types import DistributionPlatform
from app.telegram_discovery_strategy import ExpandedAudienceIntelligenceEngine


def _candidate(index: int, *, exploration: bool, score: float):
    return SimpleNamespace(
        icp_id=ICP_ID,
        platform=DistributionPlatform.TELEGRAM,
        canonical_key=f"telegram:{'explore' if exploration else 'direct'}:{index}",
        relevance_score=score,
        metadata={
            "intelligence_scores": {
                "ranking_score": score,
                "exploration_candidate": exploration,
            }
        },
    )


ICP_ID = uuid4()


def test_portfolio_reserves_exploration_slots_after_coverage() -> None:
    direct = [_candidate(index, exploration=False, score=100 - index) for index in range(12)]
    exploration = [
        _candidate(100, exploration=True, score=20),
        _candidate(101, exploration=True, score=19),
    ]

    limited = ExpandedAudienceIntelligenceEngine._coverage_first_limit(
        direct + exploration,
        10,
    )

    exploration_keys = [
        item.canonical_key
        for item in limited
        if item.metadata["intelligence_scores"]["exploration_candidate"]
    ]
    assert len(limited) == 10
    assert set(exploration_keys) == {"telegram:explore:100", "telegram:explore:101"}


def test_small_portfolio_does_not_force_exploration_reserve() -> None:
    direct = [_candidate(index, exploration=False, score=100 - index) for index in range(6)]
    exploration = [_candidate(100, exploration=True, score=5)]

    limited = ExpandedAudienceIntelligenceEngine._coverage_first_limit(
        direct + exploration,
        5,
    )

    assert all(
        not item.metadata["intelligence_scores"]["exploration_candidate"]
        for item in limited
    )
