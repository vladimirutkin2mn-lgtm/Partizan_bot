"""analytics loop schema

Revision ID: 20260807_0007
Revises: 20260807_0006
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260807_0007"
down_revision: str | None = "20260807_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("attribution_method", sa.String(length=120), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_analytics_events_experiment_type",
        "analytics_events",
        ["experiment_id", "event_type"],
    )
    op.create_index(
        "ix_analytics_events_experiment_occurred",
        "analytics_events",
        ["experiment_id", "occurred_at"],
    )

    op.create_table(
        "experiment_spend",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_experiment_spend_experiment_occurred",
        "experiment_spend",
        ["experiment_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_spend_experiment_occurred",
        table_name="experiment_spend",
    )
    op.drop_table("experiment_spend")
    op.drop_index(
        "ix_analytics_events_experiment_occurred",
        table_name="analytics_events",
    )
    op.drop_index(
        "ix_analytics_events_experiment_type",
        table_name="analytics_events",
    )
    op.drop_table("analytics_events")
