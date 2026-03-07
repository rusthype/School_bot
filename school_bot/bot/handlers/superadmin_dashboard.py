from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.superadmin_menu_builder import SuperAdminMenuBuilder, SuperAdminOverview
from school_bot.database.models import User, UserRole, Profile, Book, Task

router = Router(name="superadmin_dashboard")


async def _get_overview(session: AsyncSession) -> SuperAdminOverview:
    total_users = await session.scalar(select(func.count()).select_from(User)) or 0
    admin_users = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.superadmin)
    ) or 0
    teacher_users = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.teacher)
    ) or 0
    student_users = await session.scalar(
        select(func.count()).select_from(Profile).where(Profile.profile_type == "student")
    ) or 0
    book_count = await session.scalar(select(func.count()).select_from(Book)) or 0
    task_count = await session.scalar(select(func.count()).select_from(Task)) or 0

    db_size_mb = None
    try:
        result = await session.execute(select(func.pg_database_size(func.current_database())))
        size_bytes = result.scalar_one_or_none()
        if size_bytes:
            db_size_mb = int(size_bytes / (1024 * 1024))
    except Exception:
        db_size_mb = None

    return SuperAdminOverview(
        total_users=total_users,
        admin_users=admin_users,
        teacher_users=teacher_users,
        student_users=student_users,
        book_count=book_count,
        task_count=task_count,
        db_size_mb=db_size_mb,
    )


@router.message(Command("dashboard"))
async def superadmin_dashboard_command(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return

    builder = SuperAdminMenuBuilder()
    overview = await _get_overview(session)
    await message.answer(
        builder.build_dashboard_text(overview),
        reply_markup=builder.build_main_keyboard(),
    )
