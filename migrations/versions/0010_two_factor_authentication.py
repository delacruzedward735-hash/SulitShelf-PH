"""Add authenticator-app two-factor authentication and recovery codes."""

from alembic import op
import sqlalchemy as sa


revision = "0010_two_factor_authentication"
down_revision = "0009_promoter_crm"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("two_factor_secret_ciphertext", sa.String(512)))
        batch.add_column(sa.Column("two_factor_enabled_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("two_factor_last_counter", sa.BigInteger()))

    op.create_table(
        "two_factor_recovery_code",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_two_factor_recovery_code_user_id", "two_factor_recovery_code", ["user_id"])
    op.create_index("ix_two_factor_recovery_code_code_hash", "two_factor_recovery_code", ["code_hash"], unique=True)
    op.create_index("ix_two_factor_recovery_code_used_at", "two_factor_recovery_code", ["used_at"])


def downgrade():
    op.drop_table("two_factor_recovery_code")
    with op.batch_alter_table("user") as batch:
        batch.drop_column("two_factor_last_counter")
        batch.drop_column("two_factor_enabled_at")
        batch.drop_column("two_factor_secret_ciphertext")
