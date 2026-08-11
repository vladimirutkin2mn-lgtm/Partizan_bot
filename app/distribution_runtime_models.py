import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.distribution_types import AttributionLevel, DistributionPlatform


class DistributionPlayRecord(Base):
    __tablename__ = "distribution_plays"
    __table_args__ = (
        Index("ix_distribution_plays_product_status", "product_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    icp_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("icps.id", ondelete="CASCADE"),
        index=True,
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
        index=True,
    )
    platform: Mapped[DistributionPlatform] = mapped_column(String(30), index=True)
    tactic_id: Mapped[str] = mapped_column(String(120), index=True)
    tactic_class: Mapped[str] = mapped_column(String(40))
    action_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), index=True)
    selected_identity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_identities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    priority_score: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class DistributionExperimentRecord(Base):
    __tablename__ = "distribution_experiments"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_distribution_experiment_action"),
        Index("ix_distribution_experiments_product_status", "product_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    distribution_play_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_plays.id", ondelete="CASCADE"),
        index=True,
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
        index=True,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_actions.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    attribution_level: Mapped[AttributionLevel] = mapped_column(String(30))
    tracking_url: Mapped[str] = mapped_column(Text)
    referral_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class DistributionAnalyticsEventRecord(Base):
    __tablename__ = "distribution_analytics_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    revenue: Mapped[float] = mapped_column(Float, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    attributed_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DistributionExperimentSpendRecord(Base):
    __tablename__ = "distribution_experiment_spend"

    spend_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
        index=True,
    )
    amount: Mapped[float] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DistributionGrowthDecisionRecord(Base):
    __tablename__ = "distribution_growth_decisions"
    __table_args__ = (
        Index("ix_distribution_growth_decisions_product_created", "product_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(30), index=True)
    platform: Mapped[DistributionPlatform] = mapped_column(String(30), index=True)
    tactic_id: Mapped[str] = mapped_column(String(120), index=True)
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
    fingerprint: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DistributionLearningEntryRecord(Base):
    __tablename__ = "distribution_learning_entries"
    __table_args__ = (
        Index("ix_distribution_learning_product_tactic", "product_id", "platform", "tactic_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
        index=True,
    )
    platform: Mapped[DistributionPlatform] = mapped_column(String(30), index=True)
    tactic_id: Mapped[str] = mapped_column(String(120), index=True)
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
    action: Mapped[str] = mapped_column(String(30), index=True)
    observed_cac: Mapped[float | None] = mapped_column(Float, nullable=True)
    paid_users: Mapped[int] = mapped_column(default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0)
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
