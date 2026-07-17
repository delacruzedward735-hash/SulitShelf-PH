"""Initial SulitShelf schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    op.create_table("user", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("email", sa.String(254), nullable=False), sa.Column("display_name", sa.String(80), nullable=False), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("role", sa.String(20), nullable=False), sa.Column("is_active_account", sa.Boolean(), nullable=False), *timestamps())
    op.create_index("ix_user_email", "user", ["email"], unique=True)
    op.create_table("plan", sa.Column("key", sa.String(20), primary_key=True), sa.Column("name", sa.String(50), nullable=False), sa.Column("price_cents", sa.Integer(), nullable=False), sa.Column("product_limit", sa.Integer(), nullable=False), sa.Column("dodo_product_id", sa.String(120)), sa.Column("is_active", sa.Boolean(), nullable=False), sa.Column("updated_by", sa.String(254), nullable=False), *timestamps())
    op.create_table("platform_settings", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("gcash_account_name", sa.String(80), nullable=False), sa.Column("gcash_number", sa.String(20), nullable=False), sa.Column("gcash_qr_name", sa.String(160)), sa.Column("updated_by", sa.String(254), nullable=False), *timestamps())
    op.create_table("webhook_event", sa.Column("id", sa.String(160), primary_key=True), sa.Column("event_type", sa.String(80), nullable=False), sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("audit_log", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("admin_email", sa.String(254), nullable=False), sa.Column("action", sa.String(80), nullable=False), sa.Column("target_type", sa.String(40), nullable=False), sa.Column("target_id", sa.String(80), nullable=False), sa.Column("details", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("shop", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("name", sa.String(80), nullable=False), sa.Column("slug", sa.String(64), nullable=False), sa.Column("bio", sa.String(240), nullable=False), sa.Column("plan_key", sa.String(20), nullable=False), sa.Column("subscription_status", sa.String(30), nullable=False), sa.Column("subscription_source", sa.String(30)), sa.Column("subscription_external_id", sa.String(160)), sa.Column("subscription_ends_at", sa.DateTime(timezone=True), nullable=False), *timestamps())
    op.create_index("ix_shop_slug", "shop", ["slug"], unique=True)
    op.create_table("product", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False), sa.Column("name", sa.String(100), nullable=False), sa.Column("description", sa.String(500), nullable=False), sa.Column("department", sa.String(60), nullable=False), sa.Column("marketplace", sa.String(20), nullable=False), sa.Column("affiliate_url", sa.Text(), nullable=False), sa.Column("price_cents", sa.Integer(), nullable=False), sa.Column("image_name", sa.String(160), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("badge", sa.String(32)), sa.Column("click_count", sa.Integer(), nullable=False), *timestamps())
    op.create_index("ix_product_shop_id", "product", ["shop_id"]); op.create_index("ix_product_department", "product", ["department"]); op.create_index("ix_product_marketplace", "product", ["marketplace"]); op.create_index("ix_product_status", "product", ["status"])
    op.create_table("payment_submission", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shop.id", ondelete="CASCADE"), nullable=False), sa.Column("plan_key", sa.String(20), nullable=False), sa.Column("amount_cents", sa.Integer(), nullable=False), sa.Column("reference_number", sa.String(40), nullable=False), sa.Column("receipt_name", sa.String(160), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("reviewed_by", sa.String(254)), sa.Column("review_note", sa.String(300)))
    op.create_index("ix_payment_submission_shop_id", "payment_submission", ["shop_id"]); op.create_index("ix_payment_submission_status", "payment_submission", ["status"]); op.create_index("ix_payment_submission_reference_number", "payment_submission", ["reference_number"], unique=True)


def downgrade():
    for table in ["payment_submission", "product", "shop", "audit_log", "webhook_event", "platform_settings", "plan", "user"]:
        op.drop_table(table)
