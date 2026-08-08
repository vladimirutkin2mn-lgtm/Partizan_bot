"""icp engine fields

Revision ID: 20260807_0003
Revises: 20260807_0002
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260807_0003"
down_revision: str | None = "20260807_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("icps", sa.Column("rank", sa.Integer(), nullable=True))
    op.add_column("icps", sa.Column("desired_outcome", sa.Text(), nullable=True))
    op.add_column(
        "icps",
        sa.Column(
            "alternatives",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("icps", sa.Column("message_hook", sa.Text(), nullable=True))
    op.add_column(
        "icps",
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("icps", sa.Column("score_explanation", sa.Text(), nullable=True))
    op.add_column(
        "icps",
        sa.Column(
            "rationale",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("icps", sa.Column("duplicate_of", sa.String(length=200), nullable=True))
    op.add_column(
        "icps",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_icps_product_id_rank", "icps", ["product_id", "rank"])


def downgrade() -> None:
    op.drop_index("ix_icps_product_id_rank", table_name="icps")
    op.drop_column("icps", "created_at")
    op.drop_column("icps", "duplicate_of")
    op.drop_column("icps", "rationale")
    op.drop_column("icps", "score_explanation")
    op.drop_column("icps", "score_breakdown")
    op.drop_column("icps", "message_hook")
    op.drop_column("icps", "alternatives")
    op.drop_column("icps", "desired_outcome")
    op.drop_column("icps", "rank")
