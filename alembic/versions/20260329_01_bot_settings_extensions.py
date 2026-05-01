"""Add notify_new_registration, notify_new_order, data_retention_days to bot_settings

Revision ID: 20260329_01
Revises: 20260312_01
Create Date: 2026-03-29

Note: ``down_revision`` was previously set to ``None``, which produced
two parallel heads in the alembic chain (``20260312_01`` was orphaned
on one branch, ``20260329_01`` on the other). Anchoring this migration
to ``20260312_01`` heals the chain so ``alembic upgrade head`` resolves
to a single linear path. The docstring header already declared the
correct parent — only the runtime constant was wrong.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260329_01"
down_revision = "20260312_01"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _column_exists("bot_settings", "notify_new_registration"):
        op.add_column(
            "bot_settings",
            sa.Column("notify_new_registration", sa.Boolean(), server_default="true", nullable=False),
        )
    if not _column_exists("bot_settings", "notify_new_order"):
        op.add_column(
            "bot_settings",
            sa.Column("notify_new_order", sa.Boolean(), server_default="true", nullable=False),
        )
    if not _column_exists("bot_settings", "data_retention_days"):
        op.add_column(
            "bot_settings",
            sa.Column("data_retention_days", sa.Integer(), server_default="365", nullable=False),
        )


def downgrade() -> None:
    if _column_exists("bot_settings", "data_retention_days"):
        op.drop_column("bot_settings", "data_retention_days")
    if _column_exists("bot_settings", "notify_new_order"):
        op.drop_column("bot_settings", "notify_new_order")
    if _column_exists("bot_settings", "notify_new_registration"):
        op.drop_column("bot_settings", "notify_new_registration")
