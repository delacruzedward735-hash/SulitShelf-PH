"""Add api_token table for native (Android) client auth, and a composite
product index that matches the mobile catalog API's query shape."""
from alembic import op
import sqlalchemy as sa

revision = "0011_api_tokens"
down_revision = "0010_two_factor_authentication"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("device_label", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_api_token_user_id", "api_token", ["user_id"])
    op.create_index("ix_api_token_token_hash", "api_token", ["token_hash"], unique=True)
    op.create_index("ix_api_token_expires_at", "api_token", ["expires_at"])
    op.create_index("ix_api_token_revoked_at", "api_token", ["revoked_at"])

    # The mobile catalog list (GET /api/v1/shops/<slug>/products) always
    # filters by shop_id + status together, then orders by created_at. The
    # existing single-column indexes on product force a merge-intersection
    # (or a full shop_id scan) instead of one index-only lookup.
    op.create_index(
        "ix_product_shop_status_created",
        "product",
        ["shop_id", "status", "created_at"],
    )


def downgrade():
    op.drop_index("ix_product_shop_status_created", table_name="product")
    op.drop_index("ix_api_token_revoked_at", table_name="api_token")
    op.drop_index("ix_api_token_expires_at", table_name="api_token")
    op.drop_index("ix_api_token_token_hash", table_name="api_token")
    op.drop_index("ix_api_token_user_id", table_name="api_token")
    op.drop_table("api_token")
