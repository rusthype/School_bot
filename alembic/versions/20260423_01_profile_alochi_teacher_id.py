"""Add alochi_teacher_id to bot_profiles

Revision ID: 20260423_01
Revises: 20260415_01
Create Date: 2026-04-23

Links bot Profile rows to Alochi panel teachers (teachers_teacher.id).
- Adds bot_profiles.alochi_teacher_id (String(36), nullable, unique, indexed).
- Does NOT backfill. Auto-link runs in the registration flow going forward;
  historical rows can be backfilled via the Alochi panel management command
  (sync_bot_teachers), which writes both sides symmetrically.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260423_01"
down_revision = "20260415_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_profiles",
        sa.Column("alochi_teacher_id", sa.String(length=36), nullable=True),
    )
    op.create_unique_constraint(
        "uq_bot_profiles_alochi_teacher_id",
        "bot_profiles",
        ["alochi_teacher_id"],
    )
    op.create_index(
        "ix_bot_profiles_alochi_teacher_id",
        "bot_profiles",
        ["alochi_teacher_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_bot_profiles_alochi_teacher_id", table_name="bot_profiles")
    op.drop_constraint(
        "uq_bot_profiles_alochi_teacher_id",
        "bot_profiles",
        type_="unique",
    )
    op.drop_column("bot_profiles", "alochi_teacher_id")
