from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.distribution_schemas import (
    CampaignSlotView,
    CommunityPolicyView,
    DistributionActionView,
    DistributionOpportunityView,
)
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    CampaignSlotStatus,
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
    OpportunityKind,
    is_valid_action_type,
    is_valid_opportunity_kind,
)


def test_mvp_platform_opportunity_mapping() -> None:
    assert is_valid_opportunity_kind(DistributionPlatform.TELEGRAM, OpportunityKind.CHANNEL)
    assert is_valid_opportunity_kind(DistributionPlatform.TELEGRAM, OpportunityKind.GROUP)
    assert is_valid_opportunity_kind(
        DistributionPlatform.INSTAGRAM,
        OpportunityKind.CREATOR_ACCOUNT,
    )
    assert is_valid_opportunity_kind(DistributionPlatform.REDDIT, OpportunityKind.SUBREDDIT)
    assert is_valid_opportunity_kind(
        DistributionPlatform.TIKTOK,
        OpportunityKind.CONTENT_CLUSTER,
    )
    assert not is_valid_opportunity_kind(
        DistributionPlatform.INSTAGRAM,
        OpportunityKind.SUBREDDIT,
    )


def test_opportunity_schema_rejects_cross_platform_kind() -> None:
    with pytest.raises(ValidationError):
        DistributionOpportunityView(
            id=uuid4(),
            icp_id=uuid4(),
            platform=DistributionPlatform.REDDIT,
            kind=OpportunityKind.CONTENT_CLUSTER,
            canonical_key="r/relationships",
            title="Relationships",
            relevance_score=90,
        )


def test_action_mapping_keeps_tiktok_organic_distinct() -> None:
    assert is_valid_action_type(
        DistributionPlatform.TIKTOK,
        DistributionActionType.ORGANIC_VIDEO,
    )
    assert not is_valid_action_type(
        DistributionPlatform.INSTAGRAM,
        DistributionActionType.ORGANIC_VIDEO,
    )


def test_action_schema_rejects_invalid_platform_action() -> None:
    with pytest.raises(ValidationError):
        DistributionActionView(
            id=uuid4(),
            platform=DistributionPlatform.INSTAGRAM,
            opportunity_id=uuid4(),
            action_type=DistributionActionType.STANDALONE_POST,
            status=DistributionActionStatus.PREPARED,
            automation_level=AutomationLevel.ASSISTED,
            attribution_level=AttributionLevel.CAMPAIGN,
        )


def test_campaign_slot_requires_positive_time_window() -> None:
    start = datetime.now(UTC)
    with pytest.raises(ValidationError):
        CampaignSlotView(
            id=uuid4(),
            product_id=uuid4(),
            distribution_identity_id=uuid4(),
            platform=DistributionPlatform.INSTAGRAM,
            status=CampaignSlotStatus.ACTIVE,
            starts_at=start,
            ends_at=start - timedelta(minutes=1),
        )


def test_reddit_policy_gate_is_explicit() -> None:
    policy = CommunityPolicyView(
        id=uuid4(),
        opportunity_id=uuid4(),
        commercial_participation_allowed=True,
        comments_allowed=True,
        links_allowed=False,
        confidence=88,
    )
    assert policy.allows_commercial_action is True
    assert policy.links_allowed is False


def test_policy_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        CommunityPolicyView(
            id=uuid4(),
            opportunity_id=uuid4(),
            confidence=101,
        )
