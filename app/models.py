import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProductProfileStatus(StrEnum):
    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    CONFIRMED = "CONFIRMED"


class ClarificationStatus(StrEnum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"


class GrowthPlayStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExperimentStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


class DecisionAction(StrEnum):
    SCALE = "SCALE"
    CONTINUE = "CONTINUE"
    MODIFY = "MODIFY"
    STOP = "STOP"


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    input_brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    problem_or_desire: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_proposition: Mapped[str | None] = mapped_column(Text, nullable=True)
    usp: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_cases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    market: Mapped[str | None] = mapped_column(String(120), nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pricing_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_cac: Mapped[float | None] = mapped_column(Float, nullable=True)
    allowed_channels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    constraints: Mapped[list[str]] = mapped_column(JSONB, default=list)
    known_audience: Mapped[list[str]] = mapped_column(JSONB, default=list)
    known_competitors: Mapped[list[str]] = mapped_column(JSONB, default=list)
    reference_links: Mapped[list[str]] = mapped_column(JSONB, default=list)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    contradictions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[ProductProfileStatus] = mapped_column(
        Enum(ProductProfileStatus, name="product_profile_status"),
        default=ProductProfileStatus.DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    clarifications = relationship(
        "ClarificationQuestion",
        back_populates="product",
        cascade="all, delete-orphan",
    )


class ClarificationQuestion(Base):
    __tablename__ = "clarification_questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(100))
    question: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[ClarificationStatus] = mapped_column(
        Enum(ClarificationStatus, name="clarification_status"),
        default=ClarificationStatus.OPEN,
    )
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    product = relationship("Product", back_populates="clarifications")


class ICP(Base):
    __tablename__ = "icps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    pain: Mapped[str | None] = mapped_column(Text, nullable=True)
    desired_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger: Mapped[str | None] = mapped_column(Text, nullable=True)
    willingness_to_pay: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternatives: Mapped[list[str]] = mapped_column(JSONB, default=list)
    message_hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)
    score_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[list[str]] = mapped_column(JSONB, default=list)
    duplicate_of: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChannelOpportunity(Base):
    __tablename__ = "channel_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    icp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("icps.id"))
    platform: Mapped[str] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(100))
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    acquisition_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GrowthPlay(Base):
    __tablename__ = "growth_plays"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    icp_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("icps.id"))
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("channel_opportunities.id")
    )
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[GrowthPlayStatus] = mapped_column(
        Enum(GrowthPlayStatus, name="growth_play_status"),
        default=GrowthPlayStatus.PROPOSED,
    )
    hypothesis: Mapped[str] = mapped_column(Text)
    offer: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    success_metric: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_cost_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    effort_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_signal_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    kill_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    scale_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)
    score_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    growth_play_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("growth_plays.id")
    )
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus, name="experiment_status"),
        default=ExperimentStatus.DRAFT,
    )
    budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id")
    )
    action: Mapped[DecisionAction] = mapped_column(
        Enum(DecisionAction, name="decision_action")
    )
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
