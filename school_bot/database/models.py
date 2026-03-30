from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    Index,
    String,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from school_bot.database.base import Base


class UserRole(str, enum.Enum):
    superadmin = "superadmin"
    teacher = "teacher"
    librarian = "librarian"
    student = "student"


class User(Base):
    __tablename__ = "bot_users"
    __table_args__ = (Index("ix_bot_users_role_created", "role", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)  # YANGI: username uchun
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[UserRole | None] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )

    tasks: Mapped[list["Task"]] = relationship(back_populates="teacher", cascade="all,delete-orphan")
    profile: Mapped["Profile | None"] = relationship(
        back_populates="user",
        cascade="all,delete-orphan",
        uselist=False,
        foreign_keys="Profile.user_id",
    )


class Task(Base):
    __tablename__ = "bot_tasks"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False, index=True)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    poll_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    poll_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )

    teacher: Mapped[User] = relationship(back_populates="tasks")
    poll_votes: Mapped[list["PollVote"]] = relationship(back_populates="task", cascade="all,delete-orphan")


class PollVote(Base):
    __tablename__ = "bot_poll_votes"
    __table_args__ = (
        Index("ix_bot_poll_votes_user_poll", "user_id", "poll_id"),
        Index("ix_bot_poll_votes_task_option", "task_id", "option_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    poll_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    poll_id: Mapped[str] = mapped_column(Text, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bot_tasks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id", ondelete="CASCADE"), index=True)
    option_id: Mapped[int] = mapped_column(Integer, index=True)
    option_text: Mapped[str] = mapped_column(Text)
    voted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )

    user: Mapped[User] = relationship()
    task: Mapped[Task | None] = relationship(back_populates="poll_votes")


class BookCategory(Base):
    __tablename__ = "bot_book_categories"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    books: Mapped[list["Book"]] = relationship(back_populates="category", cascade="all,delete-orphan")


class Book(Base):
    __tablename__ = "bot_books"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bot_book_categories.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    author: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    category: Mapped[BookCategory] = relationship(back_populates="books")
    order_items: Mapped[list["BookOrderItem"]] = relationship(back_populates="book", cascade="all,delete-orphan")


class BookOrder(Base):
    __tablename__ = "bot_book_orders"
    __table_args__ = (Index("ix_bot_orders_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False, index=True)
    librarian_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    priority: Mapped[str] = mapped_column(Text, default="normal", index=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id"), nullable=True, index=True)
    delivery_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    delivery_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    delivered_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id"), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    teacher: Mapped[User] = relationship(foreign_keys=[teacher_id])
    librarian: Mapped[User | None] = relationship(foreign_keys=[librarian_id])
    deliverer: Mapped[User | None] = relationship(foreign_keys=[delivered_by])
    admin: Mapped[User | None] = relationship(foreign_keys=[updated_by])
    items: Mapped[list["BookOrderItem"]] = relationship(back_populates="order", cascade="all,delete-orphan")
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(
        back_populates="order",
        cascade="all,delete-orphan",
        order_by="OrderStatusHistory.changed_at",
    )


class BookOrderItem(Base):
    __tablename__ = "bot_book_order_items"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_book_orders.id", ondelete="CASCADE"))
    book_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_books.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    order: Mapped[BookOrder] = relationship(back_populates="items")
    book: Mapped[Book] = relationship(back_populates="order_items")


class OrderStatusHistory(Base):
    __tablename__ = "bot_order_status_history"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_book_orders.id", ondelete="CASCADE"), index=True)
    old_status: Mapped[str] = mapped_column(String(50))
    new_status: Mapped[str] = mapped_column(String(50), index=True)
    changed_by: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped[BookOrder] = relationship(back_populates="status_history")
    user: Mapped[User] = relationship(foreign_keys=[changed_by])


class SupportTicket(Base):
    __tablename__ = "bot_support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="open", index=True)
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    admin: Mapped[User | None] = relationship(foreign_keys=[replied_by])


class Profile(Base):
    __tablename__ = "bot_profiles"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id", ondelete="CASCADE"), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str] = mapped_column(Text, nullable=False)
    profile_type: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    assigned_groups: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=list,
    )
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    school_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_schools.id"), nullable=True, index=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="profile", foreign_keys=[user_id])
    approved_by_user: Mapped[User | None] = relationship(foreign_keys=[approved_by])
    school: Mapped[Optional["School"]] = relationship()


class School(Base):
    __tablename__ = "bot_schools"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    radius_m: Mapped[int] = mapped_column(Integer, default=150, server_default="150")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeacherAttendance(Base):
    __tablename__ = "bot_teacher_attendance"
    __table_args__ = (
        UniqueConstraint("teacher_id", "attendance_date", "action", name="uq_bot_teacher_attendance_daily_action"),
        Index("ix_bot_teacher_attendance_date", "attendance_date"),
        Index("ix_bot_teacher_attendance_school_date", "school_id", "attendance_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    teacher_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_users.id", ondelete="CASCADE"), nullable=False, index=True)
    school_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("bot_schools.id", ondelete="CASCADE"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(
        Enum("check_in", "check_out", name="attendance_action"),
        nullable=False,
        index=True,
    )
    teacher_lat: Mapped[float] = mapped_column(Float, nullable=False)
    teacher_lon: Mapped[float] = mapped_column(Float, nullable=False)
    school_lat: Mapped[float] = mapped_column(Float, nullable=False)
    school_lon: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[int] = mapped_column(Integer, nullable=False)
    is_inside: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    teacher: Mapped[User] = relationship(foreign_keys=[teacher_id])
    school: Mapped[School] = relationship(foreign_keys=[school_id])


class Group(Base):
    __tablename__ = "bot_groups"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    invite_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bot_schools.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    school: Mapped[Optional["School"]] = relationship()


# Fixed UUID for the singleton BotSettings row (replaces old integer id=1)
BOT_SETTINGS_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class BotSettings(Base):
    __tablename__ = "bot_settings"  # already prefixed

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=lambda: BOT_SETTINGS_UUID)
    bot_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_version: Mapped[str] = mapped_column(String(50), default="v2.1.0")
    language: Mapped[str] = mapped_column(String(10), default="uz", index=True)

    work_start_mon_fri: Mapped[str] = mapped_column(String(5), default="08:00")
    work_end_mon_fri: Mapped[str] = mapped_column(String(5), default="18:00")
    work_start_sat: Mapped[str] = mapped_column(String(5), default="09:00")
    work_end_sat: Mapped[str] = mapped_column(String(5), default="14:00")
    work_sun: Mapped[bool] = mapped_column(Boolean, default=False)

    notify_homework: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_announcements: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_stats: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_marketing: Mapped[bool] = mapped_column(Boolean, default=False)

    notify_new_registration: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notify_new_order: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    data_retention_days: Mapped[int] = mapped_column(Integer, default=365, server_default="365")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
