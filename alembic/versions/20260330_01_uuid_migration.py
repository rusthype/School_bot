"""Migrate all integer PKs and FKs to UUID

Revision ID: 20260330_01
Revises: 20260329_01
Create Date: 2026-03-30

This is a destructive migration: all bot_ tables are dropped and recreated
with UUID primary keys. No data is preserved. This is intentional because
the bot is connecting to the Alochi platform's PostgreSQL database where
all IDs are already UUIDs.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


revision = "20260330_01"
down_revision = "20260329_01"
branch_labels = None
depends_on = None

BOT_TABLES = [
    "bot_poll_votes",
    "bot_order_items",
    "bot_order_status_history",
    "bot_book_orders",
    "bot_books",
    "bot_book_categories",
    "bot_tasks",
    "bot_support_tickets",
    "bot_teacher_attendance",
    "bot_profiles",
    "bot_groups",
    "bot_settings",
    "bot_schools",
    "bot_users",
]


def upgrade() -> None:
    # Drop all bot tables in dependency order (children first)
    for table in BOT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Drop legacy enum types
    op.execute("DROP TYPE IF EXISTS user_role CASCADE")
    op.execute("DROP TYPE IF EXISTS attendance_action CASCADE")

    # Recreate enum types
    op.execute("CREATE TYPE user_role AS ENUM ('superadmin', 'teacher', 'librarian', 'student')")
    op.execute("CREATE TYPE attendance_action AS ENUM ('check_in', 'check_out')")

    # ---- bot_users ----
    op.create_table(
        "bot_users",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("telegram_id", sa.BigInteger, unique=True, nullable=False),
        sa.Column("full_name", sa.String, nullable=True),
        sa.Column("username", sa.String, nullable=True),
        sa.Column("role", sa.Enum("superadmin", "teacher", "librarian", "student", name="user_role", create_type=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_schools ----
    op.create_table(
        "bot_schools",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("latitude", sa.Float, nullable=True),
        sa.Column("longitude", sa.Float, nullable=True),
        sa.Column("radius_m", sa.Integer, server_default="150"),
    )

    # ---- bot_settings ----
    op.create_table(
        "bot_settings",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bot_token", sa.String, nullable=True),
        sa.Column("maintenance_mode", sa.Boolean, server_default="false"),
        sa.Column("welcome_message", sa.Text, nullable=True),
        sa.Column("notify_new_registration", sa.Boolean, server_default="true", nullable=False),
        sa.Column("notify_new_order", sa.Boolean, server_default="true", nullable=False),
        sa.Column("data_retention_days", sa.Integer, server_default="365", nullable=False),
    )

    # ---- bot_groups ----
    op.create_table(
        "bot_groups",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("chat_id", sa.BigInteger, unique=True, nullable=False),
        sa.Column("invite_link", sa.String, nullable=True),
        sa.Column("school_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_schools.id"), nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_profiles ----
    op.create_table(
        "bot_profiles",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("first_name", sa.String, nullable=False),
        sa.Column("last_name", sa.String, nullable=True),
        sa.Column("phone", sa.String, nullable=True),
        sa.Column("profile_type", sa.String, nullable=True),
        sa.Column("school_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_schools.id"), nullable=True),
        sa.Column("assigned_groups", sa.JSON, server_default="[]"),
        sa.Column("is_approved", sa.Boolean, server_default="false"),
        sa.Column("approved_by", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_tasks ----
    op.create_table(
        "bot_tasks",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("teacher_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("topic", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("poll_message_id", sa.BigInteger, nullable=True),
        sa.Column("poll_id", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_poll_votes ----
    op.create_table(
        "bot_poll_votes",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("poll_message_id", sa.BigInteger, nullable=True),
        sa.Column("poll_id", sa.String, nullable=True),
        sa.Column("task_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_tasks.id"), nullable=True),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=True),
        sa.Column("option_id", sa.Integer, nullable=False),
        sa.Column("option_text", sa.String, nullable=True),
        sa.Column("voted_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_book_categories ----
    op.create_table(
        "bot_book_categories",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String, unique=True, nullable=False),
        sa.Column("display_order", sa.Integer, server_default="0"),
    )

    # ---- bot_books ----
    op.create_table(
        "bot_books",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("category_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_book_categories.id"), nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("author", sa.String, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("cover_image", sa.String, nullable=True),
        sa.Column("is_available", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_book_orders ----
    op.create_table(
        "bot_book_orders",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("teacher_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("status", sa.String, server_default="pending"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("priority", sa.String, server_default="normal"),
        sa.Column("delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated", sa.Boolean, server_default="false"),
        sa.Column("librarian_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_by", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_order_items ----
    op.create_table(
        "bot_order_items",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_book_orders.id"), nullable=False),
        sa.Column("book_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_books.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
    )

    # ---- bot_order_status_history ----
    op.create_table(
        "bot_order_status_history",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_book_orders.id"), nullable=False),
        sa.Column("old_status", sa.String, nullable=True),
        sa.Column("new_status", sa.String, nullable=False),
        sa.Column("changed_by", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ---- bot_support_tickets ----
    op.create_table(
        "bot_support_tickets",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id"), nullable=False),
        sa.Column("ticket_number", sa.Integer, nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=True),
        sa.Column("status", sa.String, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ---- bot_teacher_attendance ----
    op.create_table(
        "bot_teacher_attendance",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("teacher_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("school_id", PG_UUID(as_uuid=True), sa.ForeignKey("bot_schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.Enum("check_in", "check_out", name="attendance_action", create_type=False), nullable=False),
        sa.Column("teacher_lat", sa.Float, nullable=False),
        sa.Column("teacher_lon", sa.Float, nullable=False),
        sa.Column("school_lat", sa.Float, nullable=False),
        sa.Column("school_lon", sa.Float, nullable=False),
        sa.Column("distance_m", sa.Integer, nullable=False),
        sa.Column("is_inside", sa.Boolean, server_default="false"),
        sa.Column("attendance_date", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_unique_constraint(
        "uq_bot_teacher_attendance_daily_action",
        "bot_teacher_attendance",
        ["teacher_id", "attendance_date", "action"],
    )
    op.create_index("ix_bot_teacher_attendance_date", "bot_teacher_attendance", ["attendance_date"])
    op.create_index("ix_bot_teacher_attendance_school_date", "bot_teacher_attendance", ["school_id", "attendance_date"])


def downgrade() -> None:
    # Destructive migration — downgrade just drops everything too
    for table in BOT_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TYPE IF EXISTS user_role CASCADE")
    op.execute("DROP TYPE IF EXISTS attendance_action CASCADE")
