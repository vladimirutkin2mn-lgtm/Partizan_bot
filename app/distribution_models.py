import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    CampaignSlotStatus,
    DistributionActionStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
)


class DistributionOpportunity(Base):
    __tablename__ = "distribution_opportunities"
    __table_args__ = (
        CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 100)",
            name="ck_distribution_opportunity_relevance_score",
        ),
        Index(
            "ux_distribution_opportunity_canonical",
            "icp_id",
            "platform",
            "canonical_key",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    icp_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("icps.id", ondelete="CASCADE"),
        index=True,
    )
    legacy_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channel_opportunities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    platform: Mapped[DistributionPlatform] = mapped_column(
        Enum(DistributionPlatform, name="distribution_platform")
    )
    kind: Mapped[OpportunityKind] = mapped_column(Enum(OpportunityKind, name="opportunity_kind"))
    canonical_key: Mapped[str] = mapped_column(String(500))
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    evidence: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DistributionIdentity(Base):
    __tablename__ = "distribution_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[DistributionPlatform] = mapped_column(
        Enum(DistributionPlatform, name="distribution_platform")
    )
    theme: Mapped[str] = mapped_column(String(160))
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    geography_hints: Mapped[list[str]] = mapped_column(JSONB, default=list)
    public_positioning: Mapped[str] = mapped_column(Text)
    profile_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    eligibility: Mapped[dict] = mapped_column(JSONB, default=dict)
    reputation_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    attribution_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[DistributionIdentityStatus] = mapped_column(
        Enum(DistributionIdentityStatus, name="distribution_identity_status"),
        default=DistributionIdentityStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CommunityPolicy(Base):
    __tablename__ = "community_policies"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="ck_community_policy_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    commercial_participation_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    self_promotion_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    links_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    product_mentions_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    standalone_posts_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    comments_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    disclosure_required: Mapped[bool] = mapped_column(Boolean, default=False)
    special_promotion_windows: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    ai_content_constraints: Mapped[list[str]] = mapped_column(JSONB, default=list)
    evidence: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CampaignSlot(Base):
    __tablename__ = "campaign_slots"
    __table_args__ = (
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_campaign_slot_time_window",
        ),
        Index(
            "ux_campaign_slot_active_identity",
            "distribution_identity_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    distribution_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_identities.id", ondelete="CASCADE"),
        index=True,
    )
    platform: Mapped[DistributionPlatform] = mapped_column(
        Enum(DistributionPlatform, name="distribution_platform")
    )
    status: Mapped[CampaignSlotStatus] = mapped_column(
        Enum(CampaignSlotStatus, name="campaign_slot_status"),
        default=CampaignSlotStatus.PLANNED,
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attribution_route: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DistributionAction(Base):
    __tablename__ = "distribution_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
        index=True,
    )
    distribution_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    campaign_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("campaign_slots.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action_type: Mapped[DistributionActionType] = mapped_column(
        Enum(DistributionActionType, name="distribution_action_type")
    )
    status: Mapped[DistributionActionStatus] = mapped_column(
        Enum(DistributionActionStatus, name="distribution_action_status"),
        default=DistributionActionStatus.PREPARED,
    )
    automation_level: Mapped[AutomationLevel] = mapped_column(
        Enum(AutomationLevel, name="automation_level")
    )
    attribution_level: Mapped[AttributionLevel] = mapped_column(
        Enum(AttributionLevel, name="attribution_level")
    )
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    tracking_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    operational_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
