"""Add administrator-to-promoter CRM messages."""

from alembic import op
import sqlalchemy as sa


revision = "0009_promoter_crm"
down_revision = "0008_promoter_growth_center"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer()),
        sa.Column("sender_email", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("email_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email_status", sa.String(20), nullable=False, server_default="not_requested"),
        sa.Column("email_provider", sa.String(20)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["recipient_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["user.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_crm_message_recipient_id", "crm_message", ["recipient_id"])
    op.create_index("ix_crm_message_sender_id", "crm_message", ["sender_id"])
    op.create_index("ix_crm_message_email_status", "crm_message", ["email_status"])
    op.create_index("ix_crm_message_read_at", "crm_message", ["read_at"])
    op.create_index("ix_crm_message_created_at", "crm_message", ["created_at"])


def downgrade():
    op.drop_table("crm_message")
