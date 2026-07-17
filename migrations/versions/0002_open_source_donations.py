"""Replace subscriptions with optional one-time donations."""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0002_open_source_donations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("platform_settings") as batch:
        batch.add_column(sa.Column("wallet_provider", sa.String(40), nullable=False, server_default="GCash"))
        batch.add_column(sa.Column("donation_message", sa.String(240), nullable=False, server_default="Your support helps keep SulitShelf free and open source."))

    op.create_table(
        "donation_tier",
        sa.Column("key", sa.String(20), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(160), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("dodo_product_id", sa.String(120)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    now = datetime.now(timezone.utc)
    tier = sa.table(
        "donation_tier",
        sa.column("key", sa.String), sa.column("name", sa.String), sa.column("description", sa.String),
        sa.column("amount_cents", sa.Integer), sa.column("dodo_product_id", sa.String),
        sa.column("is_active", sa.Boolean), sa.column("updated_by", sa.String),
        sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(tier, [
        {"key": "coffee", "name": "Buy us a coffee", "description": "A small thank-you that keeps development moving.", "amount_cents": 4900, "dodo_product_id": None, "is_active": True, "updated_by": "system@sulitshelf.ph", "created_at": now, "updated_at": now},
        {"key": "supporter", "name": "Project supporter", "description": "Help with hosting, maintenance, and improvements.", "amount_cents": 14900, "dodo_product_id": None, "is_active": True, "updated_by": "system@sulitshelf.ph", "created_at": now, "updated_at": now},
        {"key": "sponsor", "name": "Open-source sponsor", "description": "Make a larger contribution to SulitShelf's future.", "amount_cents": 49900, "dodo_product_id": None, "is_active": True, "updated_by": "system@sulitshelf.ph", "created_at": now, "updated_at": now},
    ])
    op.create_table(
        "donation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tier_key", sa.String(20), sa.ForeignKey("donation_tier.key", ondelete="SET NULL")),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reference_number", sa.String(40)),
        sa.Column("receipt_name", sa.String(160)),
        sa.Column("dodo_payment_id", sa.String(160)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(254)),
        sa.Column("review_note", sa.String(300)),
    )
    op.create_index("ix_donation_shop_id", "donation", ["shop_id"])
    op.create_index("ix_donation_tier_key", "donation", ["tier_key"])
    op.create_index("ix_donation_method", "donation", ["method"])
    op.create_index("ix_donation_status", "donation", ["status"])
    op.create_index("ix_donation_reference_number", "donation", ["reference_number"], unique=True)
    op.create_index("ix_donation_dodo_payment_id", "donation", ["dodo_payment_id"], unique=True)
    op.execute("UPDATE shop SET plan_key='free', subscription_status='free'")


def downgrade():
    op.drop_table("donation")
    op.drop_table("donation_tier")
    with op.batch_alter_table("platform_settings") as batch:
        batch.drop_column("donation_message")
        batch.drop_column("wallet_provider")
