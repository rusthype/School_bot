from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.user_service import get_or_create_user
from school_bot.database.models import UserRole


class UserContextMiddleware(BaseMiddleware):
    def __init__(self, superuser_ids: list[int]) -> None:
        self._superuser_ids = set(superuser_ids)

    async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: dict[str, Any],
    ) -> Any:
        session: AsyncSession = data["session"]

        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        full_name = " ".join(p for p in [tg_user.first_name, tg_user.last_name] if p).strip() or None
        username = tg_user.username  # YANGI: username ni olish

        db_user = await get_or_create_user(
            session=session,
            telegram_id=tg_user.id,
            full_name=full_name,
            username=username  # YANGI: username parametri
        )

        # Superuser tekshirish
        is_superuser = (db_user.role == UserRole.superuser) or (tg_user.id in self._superuser_ids)
        is_teacher = db_user.role == UserRole.teacher

        # Agar user .env da superuser bo'lsa, database ni yangilash
        if tg_user.id in self._superuser_ids and db_user.role != UserRole.superuser:
            db_user.role = UserRole.superuser
            await session.commit()
            await session.refresh(db_user)
            is_superuser = True
            is_teacher = False

        # Ma'lumotlarni data ga qo'shish
        data["db_user"] = db_user
        data["is_superuser"] = is_superuser
        data["is_teacher"] = is_teacher

        # DEBUG uchun: superuserligini tekshirish
        print(f"User: {tg_user.id}, username: {username}, is_superuser: {is_superuser}, role: {db_user.role}")

        return await handler(event, data)
