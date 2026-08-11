"""execution assistant schema

Revision ID: 20260807_0006
Revises: 20260807_0005
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260807_0006"
down_revision: str | None = "20260807_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


execution_package_status = postgresql.ENUM(
    "PREPARED",
    "APPROVED",
    "REJECTED",
    "SENT",
    "FAILED",
    name="execution_package_status",
    create_type=False,
)


def upgrade() -> None:
    execution_package_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "execution_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column(
            "growth_play_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("growth_plays.id"),
            nullable=False,
        ),
        sa.Column(
            "contact",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tracking_url", sa.Text(), nullable=False),
        sa.Column("referral_token", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            execution_package_status,
            nullable=False,
            server_default="PREPARED",
        ),
        sa.Column("delivery_id", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_execution_packages_product_play",
        "execution_packages",
        ["product_id", "growth_play_id"],
    )

    op.add_column(
        "experiments",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_experiments_product_id",
        "experiments",
        "products",
        ["product_id"],
        ["id"],
    )
    op.add_column(
        "experiments",
        sa.Column("execution_package_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_experiments_execution_package_id",
        "experiments",
        "execution_packages",
        ["execution_package_id"],
        ["id"],
    )
    op.add_column("experiments", sa.Column("tracking_url", sa.Text(), nullable=True))
    op.add_column(
        "experiments",
        sa.Column("delivery_id", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("experiments", "delivery_id")
    op.drop_column("experiments", "tracking_url")
    op.drop_constraint(
        "fk_experiments_execution_package_id",
        "experiments",
        type_="foreignkey",
    )
    op.drop_column("experiments", "execution_package_id")
    op.drop_constraint("fk_experiments_product_id", "experiments", type_="foreignkey")
    op.drop_column("experiments", "product_id")
    op.drop_index("ix_execution_packages_product_play", table_name="execution_packages")
    op.drop_table("execution_packages")
    execution_package_status.drop(op.get_bind(), checkfirst=True)
