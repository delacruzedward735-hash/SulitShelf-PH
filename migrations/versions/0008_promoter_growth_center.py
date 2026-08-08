"""Add promoter growth analytics, commissions, health, and Pro branding."""

from alembic import op
import sqlalchemy as sa


revision = "0008_promoter_growth_center"
down_revision = "0007_password_recovery"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("shop") as batch:
        batch.add_column(sa.Column("branding_theme", sa.String(20), nullable=False, server_default="orange"))
        batch.add_column(sa.Column("branding_tagline", sa.String(120), nullable=False, server_default=""))
        batch.add_column(sa.Column("branding_logo_name", sa.String(160)))
        batch.add_column(sa.Column("branding_banner_name", sa.String(160)))
        batch.add_column(sa.Column("hide_platform_branding", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("product") as batch:
        batch.add_column(sa.Column("health_status", sa.String(24), nullable=False, server_default="unchecked"))
        batch.add_column(sa.Column("health_checked_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("health_note", sa.String(240), nullable=False, server_default=""))
        batch.create_index("ix_product_health_status", ["health_status"])

    op.create_table(
        "product_metric_hourly",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("device", sa.String(12), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("product_id", "period_start", "source", "device", name="uq_product_metric_hourly_bucket"),
    )
    op.create_index("ix_product_metric_hourly_product_id", "product_metric_hourly", ["product_id"])
    op.create_index("ix_product_metric_hourly_shop_id", "product_metric_hourly", ["shop_id"])
    op.create_index("ix_product_metric_hourly_period_start", "product_metric_hourly", ["period_start"])

    op.create_table(
        "commission_import",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("marketplace", sa.String(20), nullable=False),
        sa.Column("original_filename", sa.String(160), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_order_value_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_commission_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("shop_id", "marketplace", "content_hash", name="uq_commission_import_file"),
    )
    op.create_index("ix_commission_import_shop_id", "commission_import", ["shop_id"])
    op.create_index("ix_commission_import_marketplace", "commission_import", ["marketplace"])
    op.create_index("ix_commission_import_created_at", "commission_import", ["created_at"])

    op.create_table(
        "commission_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("shop_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer()),
        sa.Column("marketplace", sa.String(20), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("product_name", sa.String(160), nullable=False),
        sa.Column("order_value_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("commission_cents", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="approved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["commission_import.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shop_id"], ["shop.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("shop_id", "row_hash", name="uq_commission_entry_shop_row"),
    )
    op.create_index("ix_commission_entry_import_id", "commission_entry", ["import_id"])
    op.create_index("ix_commission_entry_shop_id", "commission_entry", ["shop_id"])
    op.create_index("ix_commission_entry_product_id", "commission_entry", ["product_id"])
    op.create_index("ix_commission_entry_marketplace", "commission_entry", ["marketplace"])
    op.create_index("ix_commission_entry_occurred_on", "commission_entry", ["occurred_on"])
    op.create_index("ix_commission_entry_status", "commission_entry", ["status"])


def downgrade():
    op.drop_table("commission_entry")
    op.drop_table("commission_import")
    op.drop_table("product_metric_hourly")
    with op.batch_alter_table("product") as batch:
        batch.drop_index("ix_product_health_status")
        batch.drop_column("health_note")
        batch.drop_column("health_checked_at")
        batch.drop_column("health_status")
    with op.batch_alter_table("shop") as batch:
        batch.drop_column("hide_platform_branding")
        batch.drop_column("branding_banner_name")
        batch.drop_column("branding_logo_name")
        batch.drop_column("branding_tagline")
        batch.drop_column("branding_theme")
