"""normalized channel-first runtime tables

Revision ID: 20260810_0011
Revises: 20260810_0010
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0011"
down_revision: str | None = "20260810_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "distribution_plays",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "icp_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("icps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("tactic_id", sa.String(length=120), nullable=False),
        sa.Column("tactic_class", sa.String(length=40), nullable=False),
        sa.Column("action_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "selected_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_object(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_distribution_plays_product_id", "distribution_plays", ["product_id"])
    op.create_index("ix_distribution_plays_icp_id", "distribution_plays", ["icp_id"])
    op.create_index(
        "ix_distribution_plays_opportunity_id",
        "distribution_plays",
        ["opportunity_id"],
    )
    op.create_index("ix_distribution_plays_platform", "distribution_plays", ["platform"])
    op.create_index("ix_distribution_plays_tactic_id", "distribution_plays", ["tactic_id"])
    op.create_index("ix_distribution_plays_status", "distribution_plays", ["status"])
    op.create_index(
        "ix_distribution_plays_selected_identity_id",
        "distribution_plays",
        ["selected_identity_id"],
    )
    op.create_index(
        "ix_distribution_plays_product_status",
        "distribution_plays",
        ["product_id", "status"],
    )

    op.create_table(
        "distribution_experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "distribution_play_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_plays.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_actions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("attribution_level", sa.String(length=30), nullable=False),
        sa.Column("tracking_url", sa.Text(), nullable=False),
        sa.Column("referral_token", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_object(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("action_id", name="uq_distribution_experiment_action"),
        sa.UniqueConstraint("referral_token", name="uq_distribution_experiment_referral_token"),
    )
    op.create_index(
        "ix_distribution_experiments_product_id",
        "distribution_experiments",
        ["product_id"],
    )
    op.create_index(
        "ix_distribution_experiments_distribution_play_id",
        "distribution_experiments",
        ["distribution_play_id"],
    )
    op.create_index(
        "ix_distribution_experiments_opportunity_id",
        "distribution_experiments",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_distribution_experiments_action_id",
        "distribution_experiments",
        ["action_id"],
    )
    op.create_index(
        "ix_distribution_experiments_status",
        "distribution_experiments",
        ["status"],
    )
    op.create_index(
        "ix_distribution_experiments_referral_token",
        "distribution_experiments",
        ["referral_token"],
        unique=True,
    )
    op.create_index(
        "ix_distribution_experiments_product_status",
        "distribution_experiments",
        ["product_id", "status"],
    )

    op.create_table(
        "distribution_analytics_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("revenue", sa.Float(), nullable=False, server_default="0"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_object(),
        ),
        sa.Column("attributed_by", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_distribution_analytics_events_experiment_id",
        "distribution_analytics_events",
        ["experiment_id"],
    )
    op.create_index(
        "ix_distribution_analytics_events_event_type",
        "distribution_analytics_events",
        ["event_type"],
    )
    op.create_index(
        "ix_distribution_analytics_events_actor_id",
        "distribution_analytics_events",
        ["actor_id"],
    )
    op.create_index(
        "ix_distribution_analytics_events_occurred_at",
        "distribution_analytics_events",
        ["occurred_at"],
    )

    op.create_table(
        "distribution_experiment_spend",
        sa.Column("spend_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_object(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_distribution_experiment_spend_experiment_id",
        "distribution_experiment_spend",
        ["experiment_id"],
    )
    op.create_index(
        "ix_distribution_experiment_spend_occurred_at",
        "distribution_experiment_spend",
        ["occurred_at"],
    )

    op.create_table(
        "distribution_growth_decisions",
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
            sa.ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("tactic_id", sa.String(length=120), nullable=False),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "distribution_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fingerprint", sa.String(length=40), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_object(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for column in (
        "product_id",
        "experiment_id",
        "action",
        "platform",
        "tactic_id",
        "opportunity_id",
        "distribution_identity_id",
        "fingerprint",
    ):
        op.create_index(
            f"ix_distribution_growth_decisions_{column}",
            "distribution_growth_decisions",
            [column],
        )
    op.create_index(
        "ix_distribution_growth_decisions_product_created",
        "distribution_growth_decisions",
        ["product_id", "created_at"],
    )

    op.create_table(
        "distribution_learning_entries",
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
            sa.ForeignKey("distribution_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("tactic_id", sa.String(length=120), nullable=False),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "distribution_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_identities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("observed_cac", sa.Float(), nullable=True),
        sa.Column("paid_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue", sa.Float(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=_json_object(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    for column in (
        "product_id",
        "experiment_id",
        "platform",
        "tactic_id",
        "opportunity_id",
        "distribution_identity_id",
        "action",
    ):
        op.create_index(
            f"ix_distribution_learning_entries_{column}",
            "distribution_learning_entries",
            [column],
        )
    op.create_index(
        "ix_distribution_learning_product_tactic",
        "distribution_learning_entries",
        ["product_id", "platform", "tactic_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_distribution_learning_product_tactic", table_name="distribution_learning_entries")
    for column in (
        "action",
        "distribution_identity_id",
        "opportunity_id",
        "tactic_id",
        "platform",
        "experiment_id",
        "product_id",
    ):
        op.drop_index(
            f"ix_distribution_learning_entries_{column}",
            table_name="distribution_learning_entries",
        )
    op.drop_table("distribution_learning_entries")

    op.drop_index(
        "ix_distribution_growth_decisions_product_created",
        table_name="distribution_growth_decisions",
    )
    for column in (
        "fingerprint",
        "distribution_identity_id",
        "opportunity_id",
        "tactic_id",
        "platform",
        "action",
        "experiment_id",
        "product_id",
    ):
        op.drop_index(
            f"ix_distribution_growth_decisions_{column}",
            table_name="distribution_growth_decisions",
        )
    op.drop_table("distribution_growth_decisions")

    op.drop_index(
        "ix_distribution_experiment_spend_occurred_at",
        table_name="distribution_experiment_spend",
    )
    op.drop_index(
        "ix_distribution_experiment_spend_experiment_id",
        table_name="distribution_experiment_spend",
    )
    op.drop_table("distribution_experiment_spend")

    op.drop_index(
        "ix_distribution_analytics_events_occurred_at",
        table_name="distribution_analytics_events",
    )
    op.drop_index(
        "ix_distribution_analytics_events_actor_id",
        table_name="distribution_analytics_events",
    )
    op.drop_index(
        "ix_distribution_analytics_events_event_type",
        table_name="distribution_analytics_events",
    )
    op.drop_index(
        "ix_distribution_analytics_events_experiment_id",
        table_name="distribution_analytics_events",
    )
    op.drop_table("distribution_analytics_events")

    op.drop_index(
        "ix_distribution_experiments_product_status",
        table_name="distribution_experiments",
    )
    op.drop_index(
        "ix_distribution_experiments_referral_token",
        table_name="distribution_experiments",
    )
    op.drop_index("ix_distribution_experiments_status", table_name="distribution_experiments")
    op.drop_index("ix_distribution_experiments_action_id", table_name="distribution_experiments")
    op.drop_index(
        "ix_distribution_experiments_opportunity_id",
        table_name="distribution_experiments",
    )
    op.drop_index(
        "ix_distribution_experiments_distribution_play_id",
        table_name="distribution_experiments",
    )
    op.drop_index(
        "ix_distribution_experiments_product_id",
        table_name="distribution_experiments",
    )
    op.drop_table("distribution_experiments")

    op.drop_index("ix_distribution_plays_product_status", table_name="distribution_plays")
    op.drop_index(
        "ix_distribution_plays_selected_identity_id",
        table_name="distribution_plays",
    )
    op.drop_index("ix_distribution_plays_status", table_name="distribution_plays")
    op.drop_index("ix_distribution_plays_tactic_id", table_name="distribution_plays")
    op.drop_index("ix_distribution_plays_platform", table_name="distribution_plays")
    op.drop_index("ix_distribution_plays_opportunity_id", table_name="distribution_plays")
    op.drop_index("ix_distribution_plays_icp_id", table_name="distribution_plays")
    op.drop_index("ix_distribution_plays_product_id", table_name="distribution_plays")
    op.drop_table("distribution_plays")
