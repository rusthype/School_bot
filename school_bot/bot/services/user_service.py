from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import User, UserRole


async def get_or_create_user(
        session: AsyncSession,
        telegram_id: int,
        full_name: str | None,
        username: str | None = None,  # YANGI: username parametri
) -> User:
    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()

    if user:
        # Agar full_name o'zgargan bo'lsa, yangilash
        if full_name and user.full_name != full_name:
            user.full_name = full_name

        # Agar username o'zgargan bo'lsa, yangilash (YANGI)
        if username and user.username != username:
            user.username = username

        await session.commit()
        return user

    # Yangi user yaratish (username bilan)
    user = User(
        telegram_id=telegram_id,
        full_name=full_name,
        username=username,  # YANGI
        role=None
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user_by_username(
        session: AsyncSession,
        username: str,
) -> User | None:
    """Username orqali userni qidirish (YANGI)"""
    result = await session.execute(
        select(User).where(User.username == username)
    )
    return result.scalar_one_or_none()


async def set_teacher_role(
        session: AsyncSession,
        telegram_id: int,
        username: str | None = None,  # YANGI
) -> tuple[bool, User]:
    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()

    if not user:
        # Yangi user yaratish (agar username berilgan bo'lsa)
        user = User(
            telegram_id=telegram_id,
            full_name=None,
            username=username,  # YANGI
            role=UserRole.teacher
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return True, user

    if user.role == UserRole.teacher:
        return False, user

    user.role = UserRole.teacher
    # Agar username berilgan bo'lsa va userda username yo'q bo'lsa, yangilash
    if username and not user.username:
        user.username = username

    await session.commit()
    await session.refresh(user)
    return True, user


async def remove_teacher_role(session: AsyncSession, telegram_id: int) -> tuple[bool, Optional[User]]:
    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()
    if not user:
        return False, None

    if user.role != UserRole.teacher:
        return False, user

    user.role = None
    await session.commit()
    await session.refresh(user)
    return True, user


async def seed_superusers(session_factory: Callable[[], AsyncSession], superuser_tg_ids: list[int]) -> None:
    if not superuser_tg_ids:
        return

    async with session_factory() as session:
        # upsert-ish: update existing + create missing
        res = await session.execute(select(User).where(User.telegram_id.in_(superuser_tg_ids)))
        existing = {u.telegram_id: u for u in res.scalars().all()}

        for tg_id in superuser_tg_ids:
            user = existing.get(tg_id)
            if user is None:
                session.add(User(telegram_id=tg_id, full_name=None, role=UserRole.superuser))
            else:
                user.role = UserRole.superuser

        await session.commit()
