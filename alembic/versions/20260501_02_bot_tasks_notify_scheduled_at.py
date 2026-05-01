"""Add notify_scheduled_at to bot_tasks for the 24h delayed teacher digest

Revision ID: 20260501_02
Revises: 20260501_01
Create Date: 2026-05-01

Replaces the per-vote teacher notification (Bosqich 2) with a single
delayed digest fired 24h after the poll lands in the parent group. The
``notify_scheduled_at`` column stores the scheduled fire time so that:

  1. ``send_task_poll`` writes ``notify_scheduled_at = now() + 24h``
     and schedules an asyncio task with sleep(86400) that calls the
     notifier when the timer expires.
  2. On bot restart, ``main.start_pending_notifications`` scans every
     row where ``notify_scheduled_at IS NOT NULL`` AND
     ``teacher_notif_message_id IS NULL`` (i.e. fire time set, but
     not yet delivered) and re-schedules an asyncio task with the
     remaining delay — or fires immediately if the deadline has
     already passed while the bot was down.

The pre-existing ``teacher_notif_message_id`` column doubles as the
"already delivered" marker: NULL = pending, non-NULL = sent. This
gives us idempotency without a second boolean column.

Indexed because the startup recovery query filters on
``notify_scheduled_at IS NOT NULL AND teacher_notif_message_id IS NULL``;
even at 10k tasks/year the index keeps the scan O(pending) instead
of O(table).

Note on revision chain
~~~~~~~~~~~~~~~~~~~~~~

``down_revision = "20260501_01"`` chains directly after the
``teacher_notif_message_id`` migration that shipped earlier today.
The new column is logically paired with that one — both are part of
the teacher-notification feature surface — and pinning the chain
straight to it keeps the dependency obvious.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_02"
down_revision = "20260501_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_tasks",
        sa.Column(
            "notify_scheduled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Partial-style index: most tasks will have notify_scheduled_at set
    # for their lifetime, but the column is still nullable for tasks
    # created BEFORE this migration ships (those rows stay NULL forever
    # and never fire — desired behaviour). A plain b-tree on the column
    # is fine; the recovery query also filters on
    # teacher_notif_message_id IS NULL but Postgres can combine indexes.
    op.create_index(
        "ix_bot_tasks_notify_scheduled_at",
        "bot_tasks",
        ["notify_scheduled_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bot_tasks_notify_scheduled_at",
        table_name="bot_tasks",
    )
    op.drop_column("bot_tasks", "notify_scheduled_at")
