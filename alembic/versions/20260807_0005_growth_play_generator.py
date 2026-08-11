"""growth play generator fields

Revision ID: 20260807_0005
Revises: 20260807_0004
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260807_0005"
down_revision: str | None = "20260807_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


growth_play_status = postgresql.ENUM(
    "PROPOSED",
    "APPROVED",
    "REJECTED",
    name="growth_play_status",
    create_type=False,
)


def upgrade() -> None:
    growth_play_status.create(op.get_bind(), checkfirst=True)
    op.add_column("growth_plays", sa.Column("rank", sa.Integer(), nullable=True))
    op.add_column(
        "growth_plays",
        sa.Column("template_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "growth_plays",
        sa.Column(
            "status",
            growth_play_status,
            nullable=False,
            server_default="PROPOSED",
        ),
    )
    op.add_column("growth_plays", sa.Column("offer", sa.Text(), nullable=True))
    op.add_column("growth_plays", sa.Column("success_metric", sa.Text(), nullable=True))
    op.add_column("growth_plays", sa.Column("estimated_cost_min", sa.Float(), nullable=True))
    op.add_column("growth_plays", sa.Column("estimated_cost_max", sa.Float(), nullable=True))
    op.add_column("growth_plays", sa.Column("effort_hours", sa.Float(), nullable=True))
    op.add_column("growth_plays", sa.Column("time_to_signal_days", sa.Integer(), nullable=True))
    op.add_column("growth_plays", sa.Column("kill_criteria", sa.Text(), nullable=True))
    op.add_column("growth_plays", sa.Column("scale_criteria", sa.Text(), nullable=True))
    op.add_column(
        "growth_plays",
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("growth_plays", sa.Column("score_explanation", sa.Text(), nullable=True))
    op.add_column(
        "growth_plays",
        sa.Column(
            "rationale",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "growth_plays",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_growth_plays_icp_priority", "growth_plays", ["icp_id", "priority"])


def downgrade() -> None:
    op.drop_index("ix_growth_plays_icp_priority", table_name="growth_plays")
    op.drop_column("growth_plays", "created_at")
    op.drop_column("growth_plays", "rationale")
    op.drop_column("growth_plays", "score_explanation")
    op.drop_column("growth_plays", "score_breakdown")
    op.drop_column("growth_plays", "scale_criteria")
    op.drop_column("growth_plays", "kill_criteria")
    op.drop_column("growth_plays", "time_to_signal_days")
    op.drop_column("growth_plays", "effort_hours")
    op.drop_column("growth_plays", "estimated_cost_max")
    op.drop_column("growth_plays", "estimated_cost_min")
    op.drop_column("growth_plays", "success_metric")
    op.drop_column("growth_plays", "offer")
    op.drop_column("growth_plays", "status")
    op.drop_column("growth_plays", "template_id")
    op.drop_column("growth_plays", "rank")
    growth_play_status.drop(op.get_bind(), checkfirst=True)
