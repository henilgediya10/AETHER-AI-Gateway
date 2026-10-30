"""Initial users and revoked JWT tables.

Revision ID: 0001_auth_baseline
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_auth_baseline"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table("revoked_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_revoked_tokens_token_hash", "revoked_tokens", ["token_hash"], unique=True)

def downgrade():
    op.drop_index("ix_revoked_tokens_token_hash", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
