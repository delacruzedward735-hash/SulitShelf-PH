"""Add server-verifiable login session versions."""

from alembic import op
import sqlalchemy as sa


revision = "0005_session_invalidation"
down_revision = "0004_growth_features"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    with op.batch_alter_table("user") as batch:
        batch.drop_column("session_version")
