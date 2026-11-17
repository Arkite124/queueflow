"""Persist signup network attribution without changing existing accounts."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("registration_ip_group", sa.String(64), nullable=True))
    op.create_index("ix_users_registration_ip_group", "users", ["registration_ip_group"])


def downgrade():
    op.drop_index("ix_users_registration_ip_group", table_name="users")
    op.drop_column("users", "registration_ip_group")
