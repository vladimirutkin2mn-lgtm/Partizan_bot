from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.distribution_policy import (
    DistributionExecutionPolicy,
    DistributionIdentitySelector,
)
from app.distribution_schemas import (
    CampaignSlotView,
    CommunityPolicyView,
    DistributionIdentityView,
    DistributionOpportunitySeed,
)
from app.distribution_types import (
    CampaignSlotStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
)


def _opportunity(
    platform: DistributionPlatform,
    kind: OpportunityKind,
    title: str = "Relationship advice",
) -> DistributionOpportunitySeed:
    return DistributionOpportunitySeed(
        icp_id=uuid4(),
        platform=platform,
        kind=kind,
        canonical_key=f"test:{uuid4()}",
        title=title,
        relevance_score=90,
        rationale="Relationship advice audience fit",
    )


def _identity(
    platform: DistributionPlatform,
    *,
    theme: str = "Relationships & Lifestyle",
    language: str = "English",
    status: DistributionIdentityStatus = DistributionIdentityStatus.ACTIVE,
) -> DistributionIdentityView:
    return DistributionIdentityView(
        id=uuid4(),
        platform=platform,
        theme=theme,
        language=language,
        public_positioning=f"Partizan {theme} Scout",
        status=status,
    )


def test_reddit_community_action_requires_policy() -> None:
    opportunity = _opportunity(DistributionPlatform.REDDIT, OpportunityKind.SUBREDDIT)
    identity = _identity(DistributionPlatform.REDDIT)

    decision = DistributionExecutionPolicy().evaluate(
        opportunity,
        DistributionActionType.COMMENT,
        identity=identity,
    )

    assert decision.allowed is False
    assert any("CommunityPolicy" in reason for reason in decision.reasons)


def test_reddit_policy_gates_links_and_mentions_separately() -> None:
    opportunity = _opportunity(DistributionPlatform.REDDIT, OpportunityKind.SUBREDDIT)
    identity = _identity(DistributionPlatform.REDDIT)
    policy = CommunityPolicyView(
        id=uuid4(),
        opportunity_id=uuid4(),
        commercial_participation_allowed=True,
        comments_allowed=True,
        links_allowed=False,
        product_mentions_allowed=True,
        disclosure_required=True,
    )

    direct_link = DistributionExecutionPolicy().evaluate(
        opportunity,
        DistributionActionType.COMMENT,
        identity=identity,
        community_policy=policy,
        has_direct_product_link=True,
        has_product_mention=True,
    )
    no_link = DistributionExecutionPolicy().evaluate(
        opportunity,
        DistributionActionType.COMMENT,
        identity=identity,
        community_policy=policy,
        has_direct_product_link=False,
        has_product_mention=True,
    )

    assert direct_link.allowed is False
    assert any("direct links" in reason for reason in direct_link.reasons)
    assert no_link.allowed is True
    assert no_link.disclosure_required is True


def test_paid_campaign_does_not_require_distribution_identity() -> None:
    opportunity = _opportunity(
        DistributionPlatform.INSTAGRAM,
        OpportunityKind.CREATOR_ACCOUNT,
    )

    decision = DistributionExecutionPolicy().evaluate(
        opportunity,
        DistributionActionType.PAID_CAMPAIGN,
    )

    assert decision.allowed is True


def test_identity_platform_and_status_are_execution_gates() -> None:
    opportunity = _opportunity(
        DistributionPlatform.INSTAGRAM,
        OpportunityKind.CREATOR_ACCOUNT,
    )
    wrong_platform = _identity(DistributionPlatform.TIKTOK)
    inactive = _identity(
        DistributionPlatform.INSTAGRAM,
        status=DistributionIdentityStatus.PAUSED,
    )

    wrong_platform_decision = DistributionExecutionPolicy().evaluate(
        opportunity,
        DistributionActionType.COMMENT,
        identity=wrong_platform,
    )
    inactive_decision = DistributionExecutionPolicy().evaluate(
        opportunity,
        DistributionActionType.COMMENT,
        identity=inactive,
    )

    assert wrong_platform_decision.allowed is False
    assert inactive_decision.allowed is False


def test_identity_selector_prefers_theme_and_language_match() -> None:
    product_id = uuid4()
    opportunity = _opportunity(
        DistributionPlatform.INSTAGRAM,
        OpportunityKind.CREATOR_ACCOUNT,
        title="Relationship breakup advice",
    )
    relationship_identity = _identity(
        DistributionPlatform.INSTAGRAM,
        theme="Relationship advice",
    )
    tech_identity = _identity(
        DistributionPlatform.INSTAGRAM,
        theme="AI and Tech",
    )

    selection = DistributionIdentitySelector().select(
        product_id=product_id,
        opportunity=opportunity,
        identities=[tech_identity, relationship_identity],
        desired_language="English",
    )

    assert selection is not None
    assert selection.identity.id == relationship_identity.id
    assert selection.score > 60


def test_identity_selector_excludes_identity_owned_by_another_active_campaign() -> None:
    product_id = uuid4()
    other_product_id = uuid4()
    opportunity = _opportunity(
        DistributionPlatform.INSTAGRAM,
        OpportunityKind.CREATOR_ACCOUNT,
    )
    best_identity = _identity(DistributionPlatform.INSTAGRAM, theme="Relationship advice")
    fallback_identity = _identity(DistributionPlatform.INSTAGRAM, theme="Lifestyle")
    now = datetime.now(UTC)
    occupied_slot = CampaignSlotView(
        id=uuid4(),
        product_id=other_product_id,
        distribution_identity_id=best_identity.id,
        platform=DistributionPlatform.INSTAGRAM,
        status=CampaignSlotStatus.ACTIVE,
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )

    selection = DistributionIdentitySelector().select(
        product_id=product_id,
        opportunity=opportunity,
        identities=[best_identity, fallback_identity],
        campaign_slots=[occupied_slot],
        desired_language="English",
    )

    assert selection is not None
    assert selection.identity.id == fallback_identity.id
