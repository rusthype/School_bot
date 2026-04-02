"""Add is_active column to bot_users for soft delete

Revision ID: 20260402_01
Revises: 20260330_01
Create Date: 2026-04-02

Adds is_active (boolean, default true) to bot_users so that
teachers can be soft-deleted (archived) instead of hard-deleted.
Existing rows are backfilled to is_active=true.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260402_01"
down_revision = "20260330_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_users",
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default="true",
        ),
    )
    op.create_index("ix_bot_users_is_active", "bot_users", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_bot_users_is_active", table_name="bot_users")
    op.drop_column("bot_users", "is_active")
