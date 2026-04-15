"""Add unique constraint on PollVote(user_id, poll_id) and server_default on BookOrder.updated_at

Revision ID: 20260415_01
Revises: 20260402_01
Create Date: 2026-04-15

- Deduplicates existing (user_id, poll_id) rows in bot_poll_votes, keeping the latest voted_at.
- Adds UniqueConstraint uq_poll_vote_user_poll on (user_id, poll_id).
- Adds server_default NOW() to bot_book_orders.updated_at.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260415_01"
down_revision = "20260402_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: delete duplicate poll votes, keep latest voted_at per (user_id, poll_id)
    op.execute(
        """
        DELETE FROM bot_poll_votes
        WHERE id NOT IN (
            SELECT DISTINCT ON (user_id, poll_id) id
            FROM bot_poll_votes
            ORDER BY user_id, poll_id, voted_at DESC NULLS LAST
        )
        """
    )

    # Step 2: add unique constraint on (user_id, poll_id)
    op.create_unique_constraint(
        "uq_poll_vote_user_poll",
        "bot_poll_votes",
        ["user_id", "poll_id"],
    )

    # Step 3: add server_default NOW() to bot_book_orders.updated_at
    op.alter_column(
        "bot_book_orders",
        "updated_at",
        server_default=sa.text("NOW()"),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "bot_book_orders",
        "updated_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=True,
    )

    op.drop_constraint(
        "uq_poll_vote_user_poll",
        "bot_poll_votes",
        type_="unique",
    )
