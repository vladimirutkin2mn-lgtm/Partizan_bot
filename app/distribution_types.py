from enum import StrEnum


class DistributionPlatform(StrEnum):
    TELEGRAM = "TELEGRAM"
    INSTAGRAM = "INSTAGRAM"
    REDDIT = "REDDIT"
    TIKTOK = "TIKTOK"


class OpportunityKind(StrEnum):
    CHANNEL = "CHANNEL"
    GROUP = "GROUP"
    CREATOR_ACCOUNT = "CREATOR_ACCOUNT"
    SUBREDDIT = "SUBREDDIT"
    CONTENT_CLUSTER = "CONTENT_CLUSTER"


class DistributionActionType(StrEnum):
    COMMENT = "COMMENT"
    REPLY = "REPLY"
    STANDALONE_POST = "STANDALONE_POST"
    ORGANIC_VIDEO = "ORGANIC_VIDEO"
    PAID_CAMPAIGN = "PAID_CAMPAIGN"


class AutomationLevel(StrEnum):
    FULL = "FULL"
    APPROVAL_GATED = "APPROVAL_GATED"
    ASSISTED = "ASSISTED"
    MANUAL = "MANUAL"


class DistributionIdentityStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"


class CampaignSlotStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class DistributionActionStatus(StrEnum):
    PREPARED = "PREPARED"
    APPROVED = "APPROVED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AttributionLevel(StrEnum):
    ACTION = "ACTION"
    CAMPAIGN = "CAMPAIGN"
    PROFILE = "PROFILE"
    PAID = "PAID"


OPPORTUNITY_KINDS_BY_PLATFORM: dict[DistributionPlatform, frozenset[OpportunityKind]] = {
    DistributionPlatform.TELEGRAM: frozenset({OpportunityKind.CHANNEL, OpportunityKind.GROUP}),
    DistributionPlatform.INSTAGRAM: frozenset({OpportunityKind.CREATOR_ACCOUNT}),
    DistributionPlatform.REDDIT: frozenset({OpportunityKind.SUBREDDIT}),
    DistributionPlatform.TIKTOK: frozenset({OpportunityKind.CONTENT_CLUSTER}),
}


ACTION_TYPES_BY_PLATFORM: dict[
    DistributionPlatform, frozenset[DistributionActionType]
] = {
    DistributionPlatform.TELEGRAM: frozenset(
        {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
            DistributionActionType.STANDALONE_POST,
            DistributionActionType.PAID_CAMPAIGN,
        }
    ),
    DistributionPlatform.INSTAGRAM: frozenset(
        {
            DistributionActionType.COMMENT,
            DistributionActionType.PAID_CAMPAIGN,
        }
    ),
    DistributionPlatform.REDDIT: frozenset(
        {
            DistributionActionType.COMMENT,
            DistributionActionType.REPLY,
            DistributionActionType.STANDALONE_POST,
            DistributionActionType.PAID_CAMPAIGN,
        }
    ),
    DistributionPlatform.TIKTOK: frozenset(
        {
            DistributionActionType.COMMENT,
            DistributionActionType.ORGANIC_VIDEO,
            DistributionActionType.PAID_CAMPAIGN,
        }
    ),
}


def is_valid_opportunity_kind(
    platform: DistributionPlatform,
    kind: OpportunityKind,
) -> bool:
    return kind in OPPORTUNITY_KINDS_BY_PLATFORM[platform]


def is_valid_action_type(
    platform: DistributionPlatform,
    action_type: DistributionActionType,
) -> bool:
    return action_type in ACTION_TYPES_BY_PLATFORM[platform]
