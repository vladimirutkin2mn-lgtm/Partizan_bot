from types import SimpleNamespace
from uuid import uuid4

from app.distribution_play_planner import DistributionPlayPlanner
from app.distribution_play_schemas import DistributionPlayStatus
from app.distribution_schemas import (
    AudienceDistributionMapView,
    CommunityPolicyView,
    DistributionIdentityView,
    DistributionOpportunityView,
)
from app.distribution_types import (
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
)


def _product():
    return SimpleNamespace(
        id=uuid4(),
        goal="Acquire paid users",
        budget=200.0,
        max_cac=10.0,
        language="English",
    )


def _opportunity(
    *,
    icp_id,
    platform: DistributionPlatform,
    kind: OpportunityKind,
    title: str,
    relevance: float = 90,
) -> DistributionOpportunityView:
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=icp_id,
        platform=platform,
        kind=kind,
        canonical_key=f"test:{uuid4()}",
        title=title,
        relevance_score=relevance,
        rationale=f"Strong {title} audience fit",
    )


def _identity(platform: DistributionPlatform, theme: str) -> DistributionIdentityView:
    return DistributionIdentityView(
        id=uuid4(),
        platform=platform,
        theme=theme,
        language="English",
        public_positioning=f"Partizan {theme} Scout",
        status=DistributionIdentityStatus.ACTIVE,
    )


def test_planner_uses_only_four_mvp_platforms_and_keeps_setup_blockers_visible() -> None:
    product = _product()
    icp_id = uuid4()
    opportunities = [
        _opportunity(
            icp_id=icp_id,
            platform=DistributionPlatform.TELEGRAM,
            kind=OpportunityKind.CHANNEL,
            title="Relationship Telegram",
        ),
        _opportunity(
            icp_id=icp_id,
            platform=DistributionPlatform.INSTAGRAM,
            kind=OpportunityKind.CREATOR_ACCOUNT,
            title="Relationship Creator",
        ),
        _opportunity(
            icp_id=icp_id,
            platform=DistributionPlatform.REDDIT,
            kind=OpportunityKind.SUBREDDIT,
            title="r/relationships",
        ),
        _opportunity(
            icp_id=icp_id,
            platform=DistributionPlatform.TIKTOK,
            kind=OpportunityKind.CONTENT_CLUSTER,
            title="breakup advice",
        ),
    ]
    distribution_map = AudienceDistributionMapView(
        product_id=product.id,
        top_icp_count=1,
        opportunity_count=len(opportunities),
        opportunities=opportunities,
    )

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
    )

    assert {play.platform for play in plays} == {
        DistributionPlatform.TELEGRAM,
        DistributionPlatform.INSTAGRAM,
        DistributionPlatform.REDDIT,
        DistributionPlatform.TIKTOK,
    }
    assert any(play.status == DistributionPlayStatus.READY for play in plays)
    assert any(play.status == DistributionPlayStatus.BLOCKED for play in plays)
    assert all(
        play.status == DistributionPlayStatus.READY
        for play in plays
        if play.action_type == DistributionActionType.PAID_CAMPAIGN
    )
    assert all(
        play.blockers
        for play in plays
        if play.action_type != DistributionActionType.PAID_CAMPAIGN
    )


def test_instagram_community_play_becomes_ready_with_matching_identity() -> None:
    product = _product()
    icp_id = uuid4()
    opportunity = _opportunity(
        icp_id=icp_id,
        platform=DistributionPlatform.INSTAGRAM,
        kind=OpportunityKind.CREATOR_ACCOUNT,
        title="Relationship advice creator",
    )
    distribution_map = AudienceDistributionMapView(
        product_id=product.id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )
    identity = _identity(DistributionPlatform.INSTAGRAM, "Relationship advice")

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
        identities=[identity],
    )
    community = next(play for play in plays if play.tactic_id == "instagram_creator_comment")

    assert community.status == DistributionPlayStatus.READY
    assert community.selected_identity_id == identity.id
    assert community.blockers == []


def test_reddit_policy_can_allow_comment_while_blocking_link_post() -> None:
    product = _product()
    icp_id = uuid4()
    opportunity = _opportunity(
        icp_id=icp_id,
        platform=DistributionPlatform.REDDIT,
        kind=OpportunityKind.SUBREDDIT,
        title="r/relationships",
    )
    distribution_map = AudienceDistributionMapView(
        product_id=product.id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )
    identity = _identity(DistributionPlatform.REDDIT, "Relationship advice")
    policy = CommunityPolicyView(
        id=uuid4(),
        opportunity_id=opportunity.id,
        commercial_participation_allowed=True,
        comments_allowed=True,
        standalone_posts_allowed=True,
        links_allowed=False,
        product_mentions_allowed=True,
        confidence=90,
    )

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
        identities=[identity],
        community_policies=[policy],
    )
    comment = next(play for play in plays if play.tactic_id == "reddit_comment")
    post = next(play for play in plays if play.tactic_id == "reddit_value_post")

    assert comment.status == DistributionPlayStatus.READY
    assert post.status == DistributionPlayStatus.BLOCKED
    assert any("direct links" in blocker for blocker in post.blockers)


def test_tiktok_organic_video_is_first_class_ready_play_with_identity() -> None:
    product = _product()
    icp_id = uuid4()
    opportunity = _opportunity(
        icp_id=icp_id,
        platform=DistributionPlatform.TIKTOK,
        kind=OpportunityKind.CONTENT_CLUSTER,
        title="breakup advice",
    )
    distribution_map = AudienceDistributionMapView(
        product_id=product.id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )
    identity = _identity(DistributionPlatform.TIKTOK, "Breakup relationship advice")

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
        identities=[identity],
    )
    organic = next(
        play for play in plays if play.tactic_id == "tiktok_partizan_organic_video"
    )

    assert organic.status == DistributionPlayStatus.READY
    assert organic.action_type == DistributionActionType.ORGANIC_VIDEO
    assert organic.selected_identity_id == identity.id


def test_paid_tactic_is_deduplicated_per_icp_and_platform() -> None:
    product = _product()
    icp_id = uuid4()
    opportunities = [
        _opportunity(
            icp_id=icp_id,
            platform=DistributionPlatform.INSTAGRAM,
            kind=OpportunityKind.CREATOR_ACCOUNT,
            title="Creator A",
            relevance=95,
        ),
        _opportunity(
            icp_id=icp_id,
            platform=DistributionPlatform.INSTAGRAM,
            kind=OpportunityKind.CREATOR_ACCOUNT,
            title="Creator B",
            relevance=85,
        ),
    ]
    distribution_map = AudienceDistributionMapView(
        product_id=product.id,
        top_icp_count=1,
        opportunity_count=2,
        opportunities=opportunities,
    )

    plays = DistributionPlayPlanner().plan(
        product=product,
        distribution_map=distribution_map,
    )
    paid = [play for play in plays if play.tactic_id == "instagram_ads"]

    assert len(paid) == 1
    assert paid[0].opportunity_id == opportunities[0].id
