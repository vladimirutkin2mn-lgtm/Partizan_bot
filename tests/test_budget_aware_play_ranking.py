from types import SimpleNamespace
from uuid import uuid4

from app.distribution_play_planner import DistributionPlayPlanner
from app.distribution_schemas import (
    AudienceDistributionMapView,
    DistributionIdentityView,
    DistributionOpportunityView,
)
from app.distribution_types import (
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
)


def _product(budget: float):
    return SimpleNamespace(
        id=uuid4(),
        goal="Acquire paid users",
        budget=budget,
        max_cac=10.0,
        language="English",
    )


def _fixture(budget: float):
    product = _product(budget)
    icp_id = uuid4()
    opportunity = DistributionOpportunityView(
        id=uuid4(),
        icp_id=icp_id,
        platform=DistributionPlatform.INSTAGRAM,
        kind=OpportunityKind.CREATOR_ACCOUNT,
        canonical_key=f"creator:{uuid4()}",
        title="High-fit creator audience",
        relevance_score=90,
        rationale="Strong audience fit",
    )
    distribution_map = AudienceDistributionMapView(
        product_id=product.id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )
    identity = DistributionIdentityView(
        id=uuid4(),
        platform=DistributionPlatform.INSTAGRAM,
        theme="High-fit creator audience",
        language="English",
        public_positioning="Partizan audience scout",
        status=DistributionIdentityStatus.ACTIVE,
    )
    return product, distribution_map, identity


def test_small_budget_prefers_zero_minimum_path_without_hiding_paid_tactic() -> None:
    product, distribution_map, identity = _fixture(20.0)

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
        identities=[identity],
    )
    comment = next(play for play in plays if play.tactic_id == "instagram_creator_comment")
    paid = next(play for play in plays if play.tactic_id == "instagram_ads")

    assert comment.priority_score > paid.priority_score
    assert paid.action_type == DistributionActionType.PAID_CAMPAIGN
    assert paid.estimated_cost_min == 20.0
    assert paid.estimated_cost_max == 20.0
    assert any("nominal minimum of 100.00" in reason for reason in paid.rationale)
    assert any("lower-cash paths are tried first" in reason for reason in paid.rationale)


def test_small_budget_top_play_is_not_artificially_capped_paid_tactic() -> None:
    product, distribution_map, identity = _fixture(20.0)

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
        identities=[identity],
        max_plays=1,
    )

    assert len(plays) == 1
    assert plays[0].tactic_id == "instagram_creator_comment"


def test_budget_covering_nominal_paid_minimum_allows_paid_tactic_to_compete_normally() -> None:
    product, distribution_map, identity = _fixture(200.0)

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
        identities=[identity],
    )
    paid = next(play for play in plays if play.tactic_id == "instagram_ads")
    comment = next(play for play in plays if play.tactic_id == "instagram_creator_comment")

    assert paid.priority_score > comment.priority_score
    assert any("covers the tactic's nominal minimum" in reason for reason in paid.rationale)
