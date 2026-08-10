"""channel-first distribution domain

Revision ID: 20260810_0009
Revises: 20260807_0008
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0009"
down_revision: str | None = "20260807_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


distribution_platform = postgresql.ENUM(
    "TELEGRAM",
    "INSTAGRAM",
    "REDDIT",
    "TIKTOK",
    name="distribution_platform",
)
opportunity_kind = postgresql.ENUM(
    "CHANNEL",
    "GROUP",
    "CREATOR_ACCOUNT",
    "SUBREDDIT",
    "CONTENT_CLUSTER",
    name="opportunity_kind",
)
distribution_identity_status = postgresql.ENUM(
    "ACTIVE",
    "PAUSED",
    "RETIRED",
    name="distribution_identity_status",
)
campaign_slot_status = postgresql.ENUM(
    "PLANNED",
    "ACTIVE",
    "COMPLETED",
    "CANCELLED",
    name="campaign_slot_status",
)
distribution_action_type = postgresql.ENUM(
    "COMMENT",
    "REPLY",
    "STANDALONE_POST",
    "ORGANIC_VIDEO",
    "PAID_CAMPAIGN",
    name="distribution_action_type",
)
distribution_action_status = postgresql.ENUM(
    "PREPARED",
    "APPROVED",
    "EXECUTED",
    "FAILED",
    "SKIPPED",
    name="distribution_action_status",
)
automation_level = postgresql.ENUM(
    "FULL",
    "APPROVAL_GATED",
    "ASSISTED",
    "MANUAL",
    name="automation_level",
)
attribution_level = postgresql.ENUM(
    "ACTION",
    "CAMPAIGN",
    "PROFILE",
    "PAID",
    name="attribution_level",
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        distribution_platform,
        opportunity_kind,
        distribution_identity_status,
        campaign_slot_status,
        distribution_action_type,
        distribution_action_status,
        automation_level,
        attribution_level,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "distribution_opportunities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "icp_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("icps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "legacy_channel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("channel_opportunities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "platform",
            postgresql.ENUM(name="distribution_platform", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "kind",
            postgresql.ENUM(name="opportunity_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("canonical_key", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "relevance_score IS NULL OR (relevance_score >= 0 AND relevance_score <= 100)",
            name="ck_distribution_opportunity_relevance_score",
        ),
    )
    op.create_index(
        "ix_distribution_opportunities_icp_id",
        "distribution_opportunities",
        ["icp_id"],
    )
    op.create_index(
        "ix_distribution_opportunities_legacy_channel_id",
        "distribution_opportunities",
        ["legacy_channel_id"],
    )
    op.create_index(
        "ux_distribution_opportunity_canonical",
        "distribution_opportunities",
        ["icp_id", "platform", "canonical_key"],
        unique=True,
    )

    op.create_table(
        "distribution_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "platform",
            postgresql.ENUM(name="distribution_platform", create_type=False),
            nullable=False,
        ),
        sa.Column("theme", sa.String(length=160), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=True),
        sa.Column(
            "geography_hints",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("public_positioning", sa.Text(), nullable=False),
        sa.Column("profile_url", sa.Text(), nullable=True),
        sa.Column(
            "profile_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "eligibility",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "reputation_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attribution_route", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="distribution_identity_status", create_type=False),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "community_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_opportunities.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("commercial_participation_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("self_promotion_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("links_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("product_mentions_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("standalone_posts_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("comments_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disclosure_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "special_promotion_windows",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "ai_content_constraints",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 100)",
            name="ck_community_policy_confidence",
        ),
    )
    op.create_index(
        "ix_community_policies_opportunity_id",
        "community_policies",
        ["opportunity_id"],
        unique=True,
    )

    op.create_table(
        "campaign_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "distribution_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("distribution_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "platform",
            postgresql.ENUM(name="distribution_platform", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="campaign_slot_status", create_type=False),
            nullable=False,
            server_default="PLANNED",
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attribution_route", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="ck_campaign_slot_time_window",
        ),
    )
    op.create_index("ix_campaign_slots_product_id", "campaign_slots", ["product_id"])
    op.create_index(
        "ix_campaign_slots_distribution_identity_id",
        "campaign_slots",
        ["distribution_identity_id"],
    )
    op.create_index(
        "ux_campaign_slot_active_identity",
        "campaign_slots",
        ["distribution_identity_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    op.create_table(
        "distribution_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "campaign_slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("campaign_slots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "experiment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "action_type",
            postgresql.ENUM(name="distribution_action_type", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="distribution_action_status", create_type=False),
            nullable=False,
            server_default="PREPARED",
        ),
        sa.Column(
            "automation_level",
            postgresql.ENUM(name="automation_level", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "attribution_level",
            postgresql.ENUM(name="attribution_level", create_type=False),
            nullable=False,
        ),
        sa.Column("target_url", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column(
            "content_payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("tracking_url", sa.Text(), nullable=True),
        sa.Column(
            "operational_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_distribution_actions_opportunity_id", "distribution_actions", ["opportunity_id"])
    op.create_index(
        "ix_distribution_actions_distribution_identity_id",
        "distribution_actions",
        ["distribution_identity_id"],
    )
    op.create_index("ix_distribution_actions_campaign_slot_id", "distribution_actions", ["campaign_slot_id"])
    op.create_index("ix_distribution_actions_experiment_id", "distribution_actions", ["experiment_id"])


def downgrade() -> None:
    op.drop_index("ix_distribution_actions_experiment_id", table_name="distribution_actions")
    op.drop_index("ix_distribution_actions_campaign_slot_id", table_name="distribution_actions")
    op.drop_index(
        "ix_distribution_actions_distribution_identity_id",
        table_name="distribution_actions",
    )
    op.drop_index("ix_distribution_actions_opportunity_id", table_name="distribution_actions")
    op.drop_table("distribution_actions")

    op.drop_index("ux_campaign_slot_active_identity", table_name="campaign_slots")
    op.drop_index("ix_campaign_slots_distribution_identity_id", table_name="campaign_slots")
    op.drop_index("ix_campaign_slots_product_id", table_name="campaign_slots")
    op.drop_table("campaign_slots")

    op.drop_index("ix_community_policies_opportunity_id", table_name="community_policies")
    op.drop_table("community_policies")

    op.drop_table("distribution_identities")

    op.drop_index("ux_distribution_opportunity_canonical", table_name="distribution_opportunities")
    op.drop_index("ix_distribution_opportunities_legacy_channel_id", table_name="distribution_opportunities")
    op.drop_index("ix_distribution_opportunities_icp_id", table_name="distribution_opportunities")
    op.drop_table("distribution_opportunities")

    bind = op.get_bind()
    for enum_type in (
        attribution_level,
        automation_level,
        distribution_action_status,
        distribution_action_type,
        campaign_slot_status,
        distribution_identity_status,
        opportunity_kind,
        distribution_platform,
    ):
        enum_type.drop(bind, checkfirst=True)
