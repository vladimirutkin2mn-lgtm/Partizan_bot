from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    CampaignSlotStatus,
    DistributionActionStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
    is_valid_action_type,
    is_valid_opportunity_kind,
)


class DistributionOpportunitySeed(BaseModel):
    icp_id: UUID
    platform: DistributionPlatform
    kind: OpportunityKind
    canonical_key: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl | None = None
    relevance_score: float | None = Field(default=None, ge=0, le=100)
    rationale: str | None = None
    metadata: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_platform_kind(self) -> "DistributionOpportunitySeed":
        if not is_valid_opportunity_kind(self.platform, self.kind):
            raise ValueError(
                f"Opportunity kind {self.kind.value} is not valid for {self.platform.value}"
            )
        if self.platform == DistributionPlatform.TELEGRAM:
            self.metadata = _normalize_legacy_telegram_action_mode(
                self.kind,
                self.metadata,
            )
        return self


class DistributionOpportunityView(DistributionOpportunitySeed):
    id: UUID
    legacy_channel_id: UUID | None = None


class AudienceDistributionMapView(BaseModel):
    product_id: UUID
    top_icp_count: int = Field(ge=1)
    opportunity_count: int = Field(ge=0)
    opportunities: list[DistributionOpportunityView] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)


class DistributionIdentityView(BaseModel):
    id: UUID
    platform: DistributionPlatform
    theme: str = Field(min_length=1, max_length=160)
    language: str | None = Field(default=None, max_length=50)
    geography_hints: list[str] = Field(default_factory=list)
    public_positioning: str = Field(min_length=1)
    profile_url: HttpUrl | None = None
    profile_config: dict = Field(default_factory=dict)
    eligibility: dict = Field(default_factory=dict)
    reputation_metadata: dict = Field(default_factory=dict)
    attribution_route: str | None = None
    status: DistributionIdentityStatus


class CommunityPolicyView(BaseModel):
    id: UUID
    opportunity_id: UUID
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
    source: str = "manual_review"
    research_status: str = "MANUAL"
    last_checked_at: datetime | None = None
    fresh_until: datetime | None = None
    confidence: float | None = Field(default=None, ge=0, le=100)

    @property
    def allows_commercial_action(self) -> bool:
        return self.commercial_participation_allowed and (
            self.standalone_posts_allowed or self.comments_allowed
        )


class CampaignSlotView(BaseModel):
    id: UUID
    product_id: UUID
    distribution_identity_id: UUID
    platform: DistributionPlatform
    status: CampaignSlotStatus
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    attribution_route: str | None = None
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> "CampaignSlotView":
        if self.starts_at is not None and self.ends_at is not None:
            if self.ends_at <= self.starts_at:
                raise ValueError("Campaign slot ends_at must be after starts_at")
        return self


class DistributionActionView(BaseModel):
    id: UUID
    platform: DistributionPlatform
    opportunity_id: UUID
    distribution_identity_id: UUID | None = None
    campaign_slot_id: UUID | None = None
    experiment_id: UUID | None = None
    action_type: DistributionActionType
    status: DistributionActionStatus
    automation_level: AutomationLevel
    attribution_level: AttributionLevel
    target_url: HttpUrl | None = None
    content_text: str | None = None
    content_payload: dict = Field(default_factory=dict)
    tracking_url: HttpUrl | None = None
    operational_metadata: dict = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    executed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_platform_action(self) -> "DistributionActionView":
        if not is_valid_action_type(self.platform, self.action_type):
            raise ValueError(
                f"Action type {self.action_type.value} is not valid for {self.platform.value}"
            )
        return self


def _normalize_legacy_telegram_action_mode(
    kind: OpportunityKind,
    metadata: dict,
) -> dict:
    """Backfill action mode for persisted Telegram opportunities created before #498."""
    normalized = dict(metadata or {})
    if str(normalized.get("action_target_mode") or "").strip():
        return normalized
    target_url = str(normalized.get("action_target_url") or "").strip()
    if not target_url:
        return normalized
    capabilities = (
        dict(normalized.get("surface_capabilities"))
        if isinstance(normalized.get("surface_capabilities"), dict)
        else {}
    )
    specific = normalized.get("action_target_specific") is True
    if (
        kind == OpportunityKind.CHANNEL
        and specific
        and str(capabilities.get("comment") or "").upper() == "AVAILABLE"
    ):
        normalized["action_target_mode"] = "CHANNEL_COMMENT"
        return normalized
    if kind != OpportunityKind.GROUP:
        return normalized
    if specific and str(capabilities.get("reply") or "").upper() == "AVAILABLE":
        normalized["action_target_mode"] = "GROUP_REPLY"
        return normalized
    if str(capabilities.get("standalone_post") or "").upper() == "AVAILABLE":
        normalized["action_target_mode"] = "STANDALONE_POST"
        normalized.setdefault("action_target_specific", False)
    return normalized
