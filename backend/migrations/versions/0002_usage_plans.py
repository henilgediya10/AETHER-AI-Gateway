"""Add customer plan and usage tracking.
Revision ID: 0002_usage_plans
Revises: 0001_auth_baseline
"""
from alembic import op
import sqlalchemy as sa
revision = "0002_usage_plans"
down_revision = "0001_auth_baseline"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("plan", sa.String(20), nullable=False, server_default="free"))
    op.create_table("monthly_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "month", name="uq_monthly_usage_user_month"))
    op.create_index("ix_monthly_usage_user_id", "monthly_usage", ["user_id"])
    op.create_index("ix_monthly_usage_month", "monthly_usage", ["month"])
    op.create_table("ai_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_ai_usage_events_user_id", "ai_usage_events", ["user_id"])

def downgrade():
    op.drop_index("ix_ai_usage_events_user_id", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
    op.drop_index("ix_monthly_usage_month", table_name="monthly_usage")
    op.drop_index("ix_monthly_usage_user_id", table_name="monthly_usage")
    op.drop_table("monthly_usage")
    op.drop_column("users", "plan")
