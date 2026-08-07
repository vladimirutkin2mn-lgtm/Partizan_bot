"""foundation schema

Revision ID: 20260807_0001
Revises:
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260807_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


product_profile_status = postgresql.ENUM(
    "DRAFT", "NEEDS_CLARIFICATION", "CONFIRMED", name="product_profile_status", create_type=False
)
clarification_status = postgresql.ENUM(
    "OPEN", "ANSWERED", name="clarification_status", create_type=False
)
experiment_status = postgresql.ENUM(
    "DRAFT",
    "APPROVED",
    "RUNNING",
    "FINISHED",
    "CANCELLED",
    name="experiment_status",
    create_type=False,
)
decision_action = postgresql.ENUM(
    "SCALE", "CONTINUE", "MODIFY", "STOP", name="decision_action", create_type=False
)


def upgrade() -> None:
    product_profile_status.create(op.get_bind(), checkfirst=True)
    clarification_status.create(op.get_bind(), checkfirst=True)
    experiment_status.create(op.get_bind(), checkfirst=True)
    decision_action.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("value_proposition", sa.Text(), nullable=True),
        sa.Column("use_cases", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("market", sa.String(length=120), nullable=True),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("pricing_model", sa.String(length=80), nullable=True),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("budget", sa.Float(), nullable=True),
        sa.Column("max_cac", sa.Float(), nullable=True),
        sa.Column("allowed_channels", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("constraints", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("reference_links", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("assumptions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", product_profile_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "clarification_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(length=100), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", clarification_status, nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_clarification_questions_product_id",
        "clarification_questions",
        ["product_id"],
    )

    op.create_table(
        "icps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("products.id")),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("pain", sa.Text(), nullable=True),
        sa.Column("trigger", sa.Text(), nullable=True),
        sa.Column("willingness_to_pay", sa.Text(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
    )

    op.create_table(
        "channel_opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("icp_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("icps.id")),
        sa.Column("platform", sa.String(length=100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("type", sa.String(length=100), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("contact_data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("acquisition_method", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "growth_plays",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("icp_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("icps.id")),
        sa.Column(
            "channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_opportunities.id"),
        ),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("execution_plan", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("expected_cost", sa.Float(), nullable=True),
        sa.Column("expected_result", sa.Text(), nullable=True),
        sa.Column("priority", sa.Float(), nullable=True),
    )

    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "growth_play_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("growth_plays.id"),
        ),
        sa.Column("status", experiment_status, nullable=False),
        sa.Column("budget", sa.Float(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id"),
        ),
        sa.Column("action", decision_action, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("decisions")
    op.drop_table("experiments")
    op.drop_table("growth_plays")
    op.drop_table("channel_opportunities")
    op.drop_table("icps")
    op.drop_index(
        "ix_clarification_questions_product_id",
        table_name="clarification_questions",
    )
    op.drop_table("clarification_questions")
    op.drop_table("products")

    decision_action.drop(op.get_bind(), checkfirst=True)
    experiment_status.drop(op.get_bind(), checkfirst=True)
    clarification_status.drop(op.get_bind(), checkfirst=True)
    product_profile_status.drop(op.get_bind(), checkfirst=True)
