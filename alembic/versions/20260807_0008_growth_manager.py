"""growth manager schema

Revision ID: 20260807_0008
Revises: 20260807_0007
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260807_0008"
down_revision: str | None = "20260807_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_contexts",
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decisions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column(
            "rationale",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "metrics_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("budget_remaining", sa.Numeric(18, 2), nullable=True),
        sa.Column(
            "recommended_budget_increment",
            sa.Numeric(18, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "next_hypothesis",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("fingerprint", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_decision_contexts_product_id", "decision_contexts", ["product_id"])
    op.create_index("ix_decision_contexts_fingerprint", "decision_contexts", ["fingerprint"])

    op.create_table(
        "growth_learning_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("template_id", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("observed_cac", sa.Numeric(18, 2), nullable=True),
        sa.Column("paid_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_growth_learning_memory_product_id",
        "growth_learning_memory",
        ["product_id"],
    )
    op.create_index(
        "ix_growth_learning_memory_experiment_id",
        "growth_learning_memory",
        ["experiment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_growth_learning_memory_experiment_id",
        table_name="growth_learning_memory",
    )
    op.drop_index(
        "ix_growth_learning_memory_product_id",
        table_name="growth_learning_memory",
    )
    op.drop_table("growth_learning_memory")
    op.drop_index("ix_decision_contexts_fingerprint", table_name="decision_contexts")
    op.drop_index("ix_decision_contexts_product_id", table_name="decision_contexts")
    op.drop_table("decision_contexts")
