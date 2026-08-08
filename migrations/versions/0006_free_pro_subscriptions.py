"""Add the permanent Free tier and the monthly Pro subscription."""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0006_free_pro_subscriptions"
down_revision = "0005_session_invalidation"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("shop") as batch:
        batch.add_column(sa.Column("subscription_customer_id", sa.String(160)))
        batch.add_column(sa.Column("subscription_product_id", sa.String(120)))
        batch.add_column(sa.Column("subscription_price_cents", sa.Integer()))
        batch.add_column(sa.Column("subscription_event_at", sa.DateTime(timezone=True)))
        batch.create_index("ix_shop_subscription_customer_id", ["subscription_customer_id"], unique=False)
        batch.create_index("ix_shop_subscription_expiry", ["plan_key", "subscription_status", "subscription_ends_at"], unique=False)

    op.create_table(
        "wallet_reference",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(40), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_wallet_reference_reference", "wallet_reference", ["reference"], unique=True)
    op.execute("INSERT INTO wallet_reference (reference, purpose, created_at) SELECT reference_number, 'donation', submitted_at FROM donation WHERE reference_number IS NOT NULL")
    op.execute("INSERT INTO wallet_reference (reference, purpose, created_at) SELECT p.reference_number, 'subscription', p.submitted_at FROM payment_submission p WHERE NOT EXISTS (SELECT 1 FROM wallet_reference w WHERE w.reference = p.reference_number)")

    now = datetime.now(timezone.utc)
    plans = sa.table(
        "plan",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("price_cents", sa.Integer),
        sa.column("product_limit", sa.Integer),
        sa.column("dodo_product_id", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("updated_by", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    bind = op.get_bind()
    existing = {row[0] for row in bind.execute(sa.text("SELECT key FROM plan")).fetchall()}
    rows = []
    if "free" not in existing:
        rows.append({"key": "free", "name": "Free", "price_cents": 0, "product_limit": 50, "dodo_product_id": None, "is_active": True, "updated_by": "system@sulitshelf.ph", "created_at": now, "updated_at": now})
    if "pro" not in existing:
        rows.append({"key": "pro", "name": "Pro", "price_cents": 4900, "product_limit": 0, "dodo_product_id": None, "is_active": True, "updated_by": "system@sulitshelf.ph", "created_at": now, "updated_at": now})
    if rows:
        op.bulk_insert(plans, rows)

    # The public offer is intentionally fixed at Free (50 total products) and
    # Pro (PHP 49/month). Preserve an administrator-configured Pro product ID.
    op.execute(plans.update().where(plans.c.key == "free").values(name="Free", price_cents=0, product_limit=50, is_active=True, updated_at=now))
    op.execute(plans.update().where(plans.c.key == "pro").values(name="Pro", price_cents=4900, product_limit=0, is_active=True, updated_at=now))
    op.execute("UPDATE shop SET plan_key='free', subscription_status='free' WHERE plan_key NOT IN ('free', 'pro')")


def downgrade():
    op.drop_table("wallet_reference")
    with op.batch_alter_table("shop") as batch:
        batch.drop_index("ix_shop_subscription_expiry")
        batch.drop_index("ix_shop_subscription_customer_id")
        batch.drop_column("subscription_event_at")
        batch.drop_column("subscription_price_cents")
        batch.drop_column("subscription_product_id")
        batch.drop_column("subscription_customer_id")
