"""product intake fields

Revision ID: 20260807_0002
Revises: 20260807_0001
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260807_0002"
down_revision: str | None = "20260807_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("input_brief", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("problem_or_desire", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("usp", sa.Text(), nullable=True))
    op.add_column(
        "products",
        sa.Column(
            "known_audience",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "known_competitors",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "contradictions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "clarification_questions",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
    )


def downgrade() -> None:
    op.drop_column("clarification_questions", "priority")
    op.drop_column("products", "contradictions")
    op.drop_column("products", "known_competitors")
    op.drop_column("products", "known_audience")
    op.drop_column("products", "usp")
    op.drop_column("products", "problem_or_desire")
    op.drop_column("products", "input_brief")
