"""restart-safe runtime snapshots

Revision ID: 20260810_0010
Revises: 20260810_0009
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0010"
down_revision: str | None = "20260810_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_snapshots",
        sa.Column("namespace", sa.String(length=80), primary_key=True),
        sa.Column("entity_key", sa.String(length=200), primary_key=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_runtime_snapshots_namespace",
        "runtime_snapshots",
        ["namespace"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_snapshots_namespace", table_name="runtime_snapshots")
    op.drop_table("runtime_snapshots")
