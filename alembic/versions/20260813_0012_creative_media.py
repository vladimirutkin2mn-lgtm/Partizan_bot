"""durable creative media blobs

Revision ID: 20260813_0012
Revises: 20260810_0011
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260813_0012"
down_revision: str | None = "20260810_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "creative_media_blobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("sha256", name="uq_creative_media_blobs_sha256"),
    )
    op.create_index(
        "ix_creative_media_blobs_sha256",
        "creative_media_blobs",
        ["sha256"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_creative_media_blobs_sha256", table_name="creative_media_blobs")
    op.drop_table("creative_media_blobs")
