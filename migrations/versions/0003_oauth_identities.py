"""Add explicitly linked social sign-in identities."""

from alembic import op
import sqlalchemy as sa

revision = "0003_oauth_identities"
down_revision = "0002_open_source_donations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oauth_identity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_user_id", sa.String(191), nullable=False),
        sa.Column("email_at_link", sa.String(254)),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_identity_provider_user"),
        sa.UniqueConstraint("user_id", "provider", name="uq_oauth_identity_user_provider"),
    )
    op.create_index("ix_oauth_identity_user_id", "oauth_identity", ["user_id"])


def downgrade():
    op.drop_table("oauth_identity")
