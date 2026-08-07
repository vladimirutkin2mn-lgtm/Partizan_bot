import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DecisionContextRecord(Base):
    __tablename__ = "decision_contexts"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(String(80))
    rationale: Mapped[list[str]] = mapped_column(JSONB, default=list)
    metrics_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    budget_remaining: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    recommended_budget_increment: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    next_hypothesis: Mapped[dict] = mapped_column(JSONB, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class GrowthLearningMemoryRecord(Base):
    __tablename__ = "growth_learning_memory"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        index=True,
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        index=True,
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
    )
    source_type: Mapped[str] = mapped_column(String(80))
    template_id: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(20))
    observed_cac: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    paid_users: Mapped[int] = mapped_column(default=0)
    revenue: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
