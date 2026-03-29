"""add teacher attendance and school geofence

Revision ID: 20260312_01
Revises:
Create Date: 2026-03-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260312_01"
down_revision = None
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return constraint_name in {con["name"] for con in inspector.get_unique_constraints(table_name)}


def upgrade() -> None:
    if not _has_column("schools", "latitude"):
        op.add_column("schools", sa.Column("latitude", sa.Float(), nullable=True))
    if not _has_column("schools", "longitude"):
        op.add_column("schools", sa.Column("longitude", sa.Float(), nullable=True))
    if not _has_column("schools", "radius_m"):
        op.add_column("schools", sa.Column("radius_m", sa.Integer(), nullable=False, server_default="150"))

    bind = op.get_bind()
    action_enum = sa.Enum("check_in", "check_out", name="attendance_action")
    if bind.dialect.name == "postgresql":
        action_enum.create(bind, checkfirst=True)

    if not _has_table("teacher_attendance"):
        op.create_table(
            "teacher_attendance",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("teacher_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("school_id", sa.Integer(), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action", action_enum, nullable=False),
            sa.Column("teacher_lat", sa.Float(), nullable=False),
            sa.Column("teacher_lon", sa.Float(), nullable=False),
            sa.Column("school_lat", sa.Float(), nullable=False),
            sa.Column("school_lon", sa.Float(), nullable=False),
            sa.Column("distance_m", sa.Integer(), nullable=False),
            sa.Column("is_inside", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("attendance_date", sa.Date(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _has_unique_constraint("teacher_attendance", "uq_teacher_attendance_daily_action"):
        op.create_unique_constraint(
            "uq_teacher_attendance_daily_action",
            "teacher_attendance",
            ["teacher_id", "attendance_date", "action"],
        )
    if not _has_index("teacher_attendance", "ix_teacher_attendance_teacher_id"):
        op.create_index("ix_teacher_attendance_teacher_id", "teacher_attendance", ["teacher_id"])
    if not _has_index("teacher_attendance", "ix_teacher_attendance_school_id"):
        op.create_index("ix_teacher_attendance_school_id", "teacher_attendance", ["school_id"])
    if not _has_index("teacher_attendance", "ix_teacher_attendance_action"):
        op.create_index("ix_teacher_attendance_action", "teacher_attendance", ["action"])
    if not _has_index("teacher_attendance", "ix_teacher_attendance_date"):
        op.create_index("ix_teacher_attendance_date", "teacher_attendance", ["attendance_date"])
    if not _has_index("teacher_attendance", "ix_teacher_attendance_school_date"):
        op.create_index("ix_teacher_attendance_school_date", "teacher_attendance", ["school_id", "attendance_date"])


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table("teacher_attendance"):
        if _has_index("teacher_attendance", "ix_teacher_attendance_school_date"):
            op.drop_index("ix_teacher_attendance_school_date", table_name="teacher_attendance")
        if _has_index("teacher_attendance", "ix_teacher_attendance_date"):
            op.drop_index("ix_teacher_attendance_date", table_name="teacher_attendance")
        if _has_index("teacher_attendance", "ix_teacher_attendance_action"):
            op.drop_index("ix_teacher_attendance_action", table_name="teacher_attendance")
        if _has_index("teacher_attendance", "ix_teacher_attendance_school_id"):
            op.drop_index("ix_teacher_attendance_school_id", table_name="teacher_attendance")
        if _has_index("teacher_attendance", "ix_teacher_attendance_teacher_id"):
            op.drop_index("ix_teacher_attendance_teacher_id", table_name="teacher_attendance")
        if _has_unique_constraint("teacher_attendance", "uq_teacher_attendance_daily_action"):
            op.drop_constraint("uq_teacher_attendance_daily_action", "teacher_attendance", type_="unique")
        op.drop_table("teacher_attendance")

    if _has_column("schools", "radius_m"):
        op.drop_column("schools", "radius_m")
    if _has_column("schools", "longitude"):
        op.drop_column("schools", "longitude")
    if _has_column("schools", "latitude"):
        op.drop_column("schools", "latitude")

    if bind.dialect.name == "postgresql":
        action_enum = sa.Enum("check_in", "check_out", name="attendance_action")
        action_enum.drop(bind, checkfirst=True)
