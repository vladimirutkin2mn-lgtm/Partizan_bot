from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.distribution_types import (
    CampaignSlotStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
    is_valid_action_type,
    is_valid_opportunity_kind,
)


class DistributionIdentityCreateRequest(BaseModel):
    platform: DistributionPlatform
    theme: str = Field(min_length=2, max_length=160)
    language: str | None = Field(default=None, max_length=50)
    geography_hints: list[str] = Field(default_factory=list)
    public_positioning: str = Field(min_length=5)
    profile_url: HttpUrl | None = None
    profile_config: dict = Field(default_factory=dict)
    allowed_opportunity_kinds: list[OpportunityKind] = Field(default_factory=list)
    allowed_actions: list[DistributionActionType] = Field(default_factory=list)
    attribution_route: str | None = None

    @model_validator(mode="after")
    def validate_platform_eligibility(self) -> "DistributionIdentityCreateRequest":
        for kind in self.allowed_opportunity_kinds:
            if not is_valid_opportunity_kind(self.platform, kind):
                raise ValueError(
                    f"Opportunity kind {kind.value} is not valid for {self.platform.value}"
                )
        for action in self.allowed_actions:
            if action == DistributionActionType.PAID_CAMPAIGN:
                raise ValueError("Partizan Distribution Identities are not required for paid campaigns")
            if not is_valid_action_type(self.platform, action):
                raise ValueError(
                    f"Action type {action.value} is not valid for {self.platform.value}"
                )
        return self


class DistributionIdentityStatusRequest(BaseModel):
    status: DistributionIdentityStatus


class CommunityPolicyUpsertRequest(BaseModel):
    commercial_participation_allowed: bool = False
    self_promotion_allowed: bool = False
    links_allowed: bool = False
    product_mentions_allowed: bool = False
    standalone_posts_allowed: bool = False
    comments_allowed: bool = False
    disclosure_required: bool = False
    special_promotion_windows: list[dict] = Field(default_factory=list)
    ai_content_constraints: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    last_checked_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=100)


class CampaignSlotCreateRequest(BaseModel):
    distribution_identity_id: UUID
    status: CampaignSlotStatus = CampaignSlotStatus.PLANNED
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    attribution_route: str | None = None
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> "CampaignSlotCreateRequest":
        if self.starts_at is not None and self.ends_at is not None:
            if self.ends_at <= self.starts_at:
                raise ValueError("Campaign slot ends_at must be after starts_at")
        return self


class CampaignSlotStatusRequest(BaseModel):
    status: CampaignSlotStatus
