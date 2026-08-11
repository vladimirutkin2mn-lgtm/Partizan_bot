"""channel hunter fields

Revision ID: 20260807_0004
Revises: 20260807_0003
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260807_0004"
down_revision: str | None = "20260807_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "channel_opportunities",
        sa.Column("title", sa.String(length=300), nullable=True),
    )
    op.add_column(
        "channel_opportunities",
        sa.Column("rationale", sa.Text(), nullable=True),
    )
    op.add_column(
        "channel_opportunities",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_channel_opportunities_icp_relevance",
        "channel_opportunities",
        ["icp_id", "relevance_score"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_channel_opportunities_icp_relevance",
        table_name="channel_opportunities",
    )
    op.drop_column("channel_opportunities", "created_at")
    op.drop_column("channel_opportunities", "rationale")
    op.drop_column("channel_opportunities", "title")
