"""Add student_daily_attendance table.

Revision ID: 20260505_01
Revises: 20260501_04
Create Date: 2026-05-05

"""
from alembic import op
import sqlalchemy as sa


revision = "20260505_01"
down_revision = "20260501_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_daily_attendance",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), nullable=False),
        sa.Column("student_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("photo_file_id", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=10), server_default="manual", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["student_profile_id"], ["bot_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["bot_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("teacher_id", "student_profile_id", "attendance_date", name="uq_sda_teacher_student_date")
    )
    op.create_index("ix_sda_date", "student_daily_attendance", ["attendance_date"], unique=False)
    op.create_index("ix_sda_student_profile_id", "student_daily_attendance", ["student_profile_id"], unique=False)
    op.create_index("ix_sda_teacher_date", "student_daily_attendance", ["teacher_id", "attendance_date"], unique=False)
    op.create_index("ix_sda_teacher_id", "student_daily_attendance", ["teacher_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sda_teacher_id", table_name="student_daily_attendance")
    op.drop_index("ix_sda_teacher_date", table_name="student_daily_attendance")
    op.drop_index("ix_sda_student_profile_id", table_name="student_daily_attendance")
    op.drop_index("ix_sda_date", table_name="student_daily_attendance")
    op.drop_table("student_daily_attendance")
