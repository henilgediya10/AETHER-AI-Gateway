"""Add payment_events table for idempotent billing webhooks.
Revision ID: 0003_payment_events
Revises: 0002_usage_plans
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_payment_events"
down_revision = "0002_usage_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False, server_default=""),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "event_id", name="uq_payment_events_provider_event"),
    )
    op.create_index("ix_payment_events_user_id", "payment_events", ["user_id"])


def downgrade():
    op.drop_index("ix_payment_events_user_id", table_name="payment_events")
    op.drop_table("payment_events")
