"""Link bot_schools to Alochi panel via alochi_school_id (UUID).

Revision ID: 20260501_03
Revises: 20260501_02
Create Date: 2026-05-01

The bot's ``bot_schools`` table and the Alochi panel's ``users_school``
table are two independent ID spaces — bot_schools uses BIGINT autoincrement,
Alochi uses UUID. There is no link between them, which means:

  1. When a teacher gets approved through the bot superadmin flow with a
     bot school selected, ``bot_profiles.school_id`` is set (BIGINT) but
     the Alochi ``apps.teachers.Teacher.school`` FK stays NULL — the panel
     UI shows the teacher as having no school.
  2. The reverse direction is also broken: when an admin assigns a school
     to a Teacher in the panel, the bot has no idea which bot_schools row
     that corresponds to.

This migration adds a nullable ``alochi_school_id`` UUID column on
``bot_schools``. The bot's ``approval_confirm`` handler reads this column
when it constructs the payload for the panel-sync internal endpoint, so a
teacher approved with bot school #5 (which maps to alochi_school_id
``a3512746-…``) lands on the panel with the correct school FK.

The column is nullable + unique:
  * Nullable so the migration applies cleanly to a prod DB where no bot
    schools have been mapped yet.
  * Unique so we never link two bot schools to the same Alochi school
    (which would mean ambiguous reverse lookup).

Backfill is performed manually by an operator via the
``link_bot_schools`` management command (added in the same alochi commit
as this migration), or by editing the row in Django admin.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "20260501_03"
down_revision = "20260501_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_schools",
        sa.Column(
            "alochi_school_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_bot_schools_alochi_school_id",
        "bot_schools",
        ["alochi_school_id"],
        unique=True,
        postgresql_where=sa.text("alochi_school_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bot_schools_alochi_school_id",
        table_name="bot_schools",
    )
    op.drop_column("bot_schools", "alochi_school_id")
