"""Add hashed, expiring password reset grants."""

from alembic import op
import sqlalchemy as sa


revision = "0007_password_recovery"
down_revision = "0006_free_pro_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "password_reset_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_password_reset_token_user_id", "password_reset_token", ["user_id"])
    op.create_index("ix_password_reset_token_token_hash", "password_reset_token", ["token_hash"], unique=True)
    op.create_index("ix_password_reset_token_expires_at", "password_reset_token", ["expires_at"])


def downgrade():
    op.drop_table("password_reset_token")
