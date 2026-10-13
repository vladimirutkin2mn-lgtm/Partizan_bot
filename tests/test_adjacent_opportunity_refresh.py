from types import SimpleNamespace
from uuid import uuid4

from app.adjacent_opportunity_refresh import (
    AdjacentAutonomousOpportunityRefreshService,
    adjacent_autonomous_opportunity_refresh_service,
)
from app.autonomous_growth_worker import AutonomousGrowthWorker
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore


class FakeAudienceService:
    def __init__(self, distribution):
        self.distribution = distribution

    def get(self, product_id):
        assert product_id == self.distribution.product_id
        return self.distribution

    def find_opportunity(self, opportunity_id):
        raise KeyError(opportunity_id)


def _product(product_id):
    return SimpleNamespace(
        id=product_id,
        name="CareerGuide",
        problem_or_desire="find better career opportunities",
        value_proposition="practical career coaching",
        description="Career support for job seekers",
        known_audience=["job seekers"],
    )


def _icps():
    return SimpleNamespace(
        icps=[
            SimpleNamespace(
                id=uuid4(),
                title="Career changers",
                description="People looking for a new role",
                pain="job search is confusing",
                trigger="starting interviews",
                desired_outcome="find a better role",
                alternatives=["career coach", "job board", "peer mentoring"],
            )
        ]
    )


def _distribution(*, strong=True):
    product_id = uuid4()
    opportunity = DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:crypto-women:community",
        title="Crypto Women Amsterdam",
        url="https://t.me/cryptowomen",
        relevance_score=78,
        rationale="Observed audience overlap in an adjacent context.",
        metadata={
            "about": "Female founders discuss web3 investing and local startup meetups",
            "opportunity_scope": "COMMUNITY",
            "actionability": "RESEARCH_ONLY",
            "relevance_guard": {"status": "PASS"},
            "intelligence_scores": {
                "audience_fit": 72.0 if strong else 35.0,
                "context_fit": 18.0,
                "actionability": 10.0,
                "confidence": 70.0 if strong else 40.0,
                "ranking_score": 55.0,
                "exploration_candidate": True,
            },
            "participant_evidence": {
                "matched_participant_count": 3,
                "identity_persisted": False,
            },
        },
        evidence=[
            {
                "title": "Web3 founder networking night",
                "snippet": "Startup investing meetup for female founders and operators",
                "url": "https://example.com/observed",
                "query": "CareerGuide career changers Telegram community",
            }
        ],
    )
    return AudienceDistributionMapView(
        product_id=product_id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )


def test_adaptive_round_reserves_one_of_first_four_slots_for_observed_adjacent_context() -> None:
    distribution = _distribution(strong=True)
    audience = FakeAudienceService(distribution)
    service = AdjacentAutonomousOpportunityRefreshService(
        store=MemoryRuntimeStateStore(),
        audience_service=audience,
    )

    hints = service._adaptive_discovery_hints(
        product=_product(distribution.product_id),
        icps=_icps(),
        product_id=distribution.product_id,
        preflight_by_url={},
        round_number=2,
    )

    first_four = " | ".join(hints[:4]).casefold()
    assert len(hints) >= 4
    assert "crypto" in first_four or "web3" in first_four
    assert hints[0] == "career coach"
    assert any(term in first_four for term in ("crypto", "web3", "female founders"))


def test_weak_existing_surface_does_not_consume_adjacent_slot() -> None:
    distribution = _distribution(strong=False)
    service = AdjacentAutonomousOpportunityRefreshService(
        store=MemoryRuntimeStateStore(),
        audience_service=FakeAudienceService(distribution),
    )

    hints = service._adaptive_discovery_hints(
        product=_product(distribution.product_id),
        icps=_icps(),
        product_id=distribution.product_id,
        preflight_by_url={},
        round_number=2,
    )

    assert hints[:4] == [
        "career coach",
        "job board",
        "peer mentoring",
        "starting interviews",
    ]
    assert "crypto" not in " ".join(hints).casefold()
    assert "web3" not in " ".join(hints).casefold()


def test_adjacent_hint_generation_does_not_mutate_source_opportunity() -> None:
    distribution = _distribution(strong=True)
    original = distribution.opportunities[0].model_dump(mode="json")
    service = AdjacentAutonomousOpportunityRefreshService(
        store=MemoryRuntimeStateStore(),
        audience_service=FakeAudienceService(distribution),
    )

    service._adaptive_discovery_hints(
        product=_product(distribution.product_id),
        icps=_icps(),
        product_id=distribution.product_id,
        preflight_by_url={},
        round_number=3,
    )

    assert distribution.opportunities[0].model_dump(mode="json") == original


def test_missing_persisted_distribution_falls_back_to_existing_adaptive_hints() -> None:
    class MissingAudienceService:
        def get(self, product_id):
            raise KeyError(product_id)

        def find_opportunity(self, opportunity_id):
            raise KeyError(opportunity_id)

    product_id = uuid4()
    service = AdjacentAutonomousOpportunityRefreshService(
        store=MemoryRuntimeStateStore(),
        audience_service=MissingAudienceService(),
    )

    hints = service._adaptive_discovery_hints(
        product=_product(product_id),
        icps=_icps(),
        product_id=product_id,
        preflight_by_url={},
        round_number=2,
    )

    assert hints
    assert hints[0] == "career coach"


def test_growth_worker_uses_adjacent_refresh_by_default_but_keeps_dependency_override() -> None:
    worker = AutonomousGrowthWorker()
    assert worker._opportunity_refresh_service is adjacent_autonomous_opportunity_refresh_service

    custom = SimpleNamespace(run_once=lambda **kwargs: kwargs)
    overridden = AutonomousGrowthWorker(opportunity_refresh_service=custom)
    assert overridden._opportunity_refresh_service is custom
