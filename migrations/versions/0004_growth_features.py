"""Add curated campaigns, trust signals, sharing analytics, and reports."""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0004_growth_features"
down_revision = "0003_oauth_identities"
branch_labels = None
depends_on = None


def upgrade():
    now = datetime.now(timezone.utc)
    with op.batch_alter_table("shop") as batch:
        batch.add_column(sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_index("ix_shop_is_verified", ["is_verified"])

    with op.batch_alter_table("product") as batch:
        batch.add_column(sa.Column("why_sulit", sa.String(240), nullable=False, server_default=""))
        batch.add_column(sa.Column("best_for", sa.String(160), nullable=False, server_default=""))
        batch.add_column(sa.Column("price_checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
        batch.add_column(sa.Column("is_sponsored", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_index("ix_product_is_sponsored", ["is_sponsored"])

    op.create_table(
        "click_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marketplace", sa.String(20), nullable=False),
        sa.Column("source", sa.String(24), nullable=False, server_default="direct"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("product_id", "shop_id", "marketplace", "source", "occurred_at"):
        op.create_index(f"ix_click_event_{column}", "click_event", [column])

    op.create_table(
        "campaign",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("eyebrow", sa.String(50), nullable=False, server_default="CURATED COLLECTION"),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_campaign_slug", "campaign", ["slug"], unique=True)
    op.create_index("ix_campaign_is_active", "campaign", ["is_active"])
    op.create_table(
        "campaign_product",
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaign.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "product_report",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("details", sa.String(300), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(254)),
    )
    op.create_index("ix_product_report_product_id", "product_report", ["product_id"])
    op.create_index("ix_product_report_reason", "product_report", ["reason"])
    op.create_index("ix_product_report_status", "product_report", ["status"])

    # Give a fresh installation useful collection shells without inventing product data.
    campaign = sa.table(
        "campaign",
        sa.column("title", sa.String),
        sa.column("slug", sa.String),
        sa.column("eyebrow", sa.String),
        sa.column("description", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        campaign,
        [
            {"title": "Student Essentials Under ₱500", "slug": "student-essentials-under-500", "eyebrow": "SULIT FOR STUDENTS", "description": "Affordable study, desk, and everyday tech finds selected for Filipino students.", "is_active": True, "sort_order": 10, "created_at": now, "updated_at": now},
            {"title": "Brownout Survival Kit", "slug": "brownout-survival-kit", "eyebrow": "READY WHEN POWER IS OUT", "description": "Rechargeable lights, fans, power banks, and practical emergency essentials.", "is_active": True, "sort_order": 20, "created_at": now, "updated_at": now},
            {"title": "Budget Desk Setup", "slug": "budget-desk-setup", "eyebrow": "WORK AND STUDY BETTER", "description": "Useful accessories for a clean, comfortable setup without the premium price.", "is_active": True, "sort_order": 30, "created_at": now, "updated_at": now},
        ],
    )


def downgrade():
    op.drop_table("product_report")
    op.drop_table("campaign_product")
    op.drop_table("campaign")
    op.drop_table("click_event")
    with op.batch_alter_table("product") as batch:
        batch.drop_index("ix_product_is_sponsored")
        batch.drop_column("is_sponsored")
        batch.drop_column("price_checked_at")
        batch.drop_column("best_for")
        batch.drop_column("why_sulit")
    with op.batch_alter_table("shop") as batch:
        batch.drop_index("ix_shop_is_verified")
        batch.drop_column("is_verified")
