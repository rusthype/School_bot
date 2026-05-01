"""Link bot_groups to Alochi panel via alochi_group_id (UUID).

Revision ID: 20260501_04
Revises: 20260501_03
Create Date: 2026-05-01

Same pattern as 20260501_03 (bot_schools.alochi_school_id) but for the
``bot_groups`` <-> ``groups_group`` cross-link.

The bot tracks Telegram chats as ``bot_groups`` (BIGINT id, ``chat_id``
is the Telegram chat id, e.g. -1003750573425). The panel tracks
pedagogical groups as ``groups_group`` (UUID id, name e.g. "1-guruh").
Until now these were two parallel universes:

  * bot_profiles.assigned_groups stored bot group NAMES as a JSON list
    of strings ("A'lochi 1-group / 39-maktab"), with no FK
  * panel groups_group_students M2M was always empty because nothing
    pushed enrollments from the bot side
  * Lessons were created on the panel side (44 of them in production)
    but no attendance was recorded because no student was in any group

This column closes the loop. Once populated by the
``link_bot_groups`` management command (or by future bot->panel sync),
both sides agree on which Telegram chat corresponds to which
pedagogical group, and downstream features (group-name renames,
soft-delete propagation, future student-enrollment sync) can rely on
the FK instead of fuzzy name matching.

The column is nullable + unique:
  * Nullable so the migration applies cleanly to prod where no
    bot_groups are linked yet.
  * Unique so two bot Telegram chats cannot point at the same panel
    pedagogical group (which would make panel->bot sync ambiguous).

Backfill is performed manually by an operator via the
``link_bot_groups`` Django management command on the alochi side
(see alochi.git for the matching commit).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "20260501_04"
down_revision = "20260501_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_groups",
        sa.Column(
            "alochi_group_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_bot_groups_alochi_group_id",
        "bot_groups",
        ["alochi_group_id"],
        unique=True,
        postgresql_where=sa.text("alochi_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bot_groups_alochi_group_id",
        table_name="bot_groups",
    )
    op.drop_column("bot_groups", "alochi_group_id")
