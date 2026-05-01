"""Add teacher_notif_message_id to bot_tasks

Revision ID: 20260501_01
Revises: 20260423_01
Create Date: 2026-05-01

Adds bot_tasks.teacher_notif_message_id to track the LIVE results card
the bot DMs to the teacher. Each time a parent casts a vote on the poll,
the bot deletes the previous results message and sends a fresh one so it
bubbles to the top of the teacher's chat. The id is needed to delete the
old card before sending the new one.

Nullable because the column is empty until the FIRST vote arrives on a
task. Indexed because the column is read on the per-vote hot path inside
``handle_poll_answer`` (one SELECT per vote).

Note on revision chain
~~~~~~~~~~~~~~~~~~~~~~

down_revision is ``20260423_01`` (alochi_teacher_id) and NOT ``20260426_01``
(bot_login_system) because the 20260426 migration ships on a feature branch
(``feat/bot-login-system``) that has not been merged to main. Anchoring this
migration to the last revision that's actually deployed on prod prevents
Alembic from blowing up with KeyError: '20260426_01' on a fresh container.
When the feature branch eventually lands, its merge will need to rewrite
its chain to land *after* 20260501_01 — trivial, but worth flagging.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260501_01"
down_revision = "20260423_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bot_tasks",
        sa.Column("teacher_notif_message_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_bot_tasks_teacher_notif_message_id",
        "bot_tasks",
        ["teacher_notif_message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bot_tasks_teacher_notif_message_id",
        table_name="bot_tasks",
    )
    op.drop_column("bot_tasks", "teacher_notif_message_id")
