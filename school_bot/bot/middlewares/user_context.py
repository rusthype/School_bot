from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from school_bot.bot.services.user_service import get_or_create_user
from school_bot.database.models import Profile, UserRole
from school_bot.bot.services.logger_service import get_logger

logger = get_logger(__name__)

_admin_cache: dict[int, tuple[list, float]] = {}  # group_id -> (members, timestamp)
_ADMIN_CACHE_TTL = 60.0  # seconds


class UserContextMiddleware(BaseMiddleware):
    def __init__(self, superadmin_ids: list[int], teacher_ids: list[int], admin_group_id: int | None) -> None:
        normalized_ids: set[int] = set()
        for raw_id in superadmin_ids:
            try:
                normalized_ids.add(int(raw_id))
            except (TypeError, ValueError):
                continue
        self._superadmin_ids = normalized_ids
        normalized_teacher_ids: set[int] = set()
        for raw_id in teacher_ids:
            try:
                normalized_teacher_ids.add(int(raw_id))
            except (TypeError, ValueError):
                continue
        self._teacher_ids = normalized_teacher_ids
        self._admin_group_id = admin_group_id
        logger.info(f"SUPERADMIN IDS from env: {sorted(self._superadmin_ids)}")

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

        # Faol emasligini tekshirish — superadminlarga blok qo'llanmaydi
        # /start yuborganda yoki istalgan xabar yuborganda foydalanuvchi avto-qayta faollashtiriladi
        if not db_user.is_active and db_user.role != UserRole.superadmin and tg_user.id not in self._superadmin_ids:
            db_user.is_active = True
            await session.commit()
            await session.refresh(db_user)
            logger.info(
                f"Foydalanuvchi avto-qayta faollashtirildi: telegram_id={tg_user.id}"
            )

        # Profilni olish (registratsiya/approval uchun)
        result = await session.execute(select(Profile).where(Profile.bot_user_id == db_user.id))
        profile = result.scalar_one_or_none()

        # Superadmin tekshirish
        is_superadmin = (db_user.role == UserRole.superadmin) or (tg_user.id in self._superadmin_ids)
        is_teacher = False
        is_student = False
        is_group_admin = False
        is_librarian = db_user.role == UserRole.librarian

        if self._admin_group_id:
            try:
                bot = data.get("bot")
                if bot:
                    cached = _admin_cache.get(self._admin_group_id)
                    now = time.monotonic()
                    if cached is not None and now - cached[1] < _ADMIN_CACHE_TTL:
                        admins = cached[0]
                    else:
                        admins = await bot.get_chat_administrators(self._admin_group_id)
                        _admin_cache[self._admin_group_id] = (admins, now)
                    is_group_admin = any(admin.user.id == tg_user.id for admin in admins)
            except Exception:
                logger.warning("Failed to check group admins", exc_info=True)

        if profile and profile.is_approved:
            if (profile.profile_type or "teacher") == "teacher":
                is_teacher = True
                if db_user.role != UserRole.teacher and not is_superadmin and db_user.role != UserRole.librarian:
                    db_user.role = UserRole.teacher
                    await session.commit()
                    await session.refresh(db_user)
        elif db_user.role == UserRole.teacher and (profile is None or not profile.is_approved):
            # Legacy teacherlar uchun (profil bo'lmasa yoki tasdiqlanmagan bo'lsa ham
            # teacher ruxsatini saqlab qolamiz — qayta faollashtirilgan foydalanuvchilar
            # uchun rolni yo'qotmaslik kerak)
            is_teacher = True
        elif tg_user.id in self._teacher_ids:
            is_teacher = True
        elif is_group_admin:
            is_teacher = True

        # Agar user .env da superadmin bo'lsa, database ni yangilash
        if tg_user.id in self._superadmin_ids and db_user.role != UserRole.superadmin:
            db_user.role = UserRole.superadmin
            await session.commit()
            await session.refresh(db_user)
            is_superadmin = True
            is_teacher = False
            is_librarian = False

        # O'quvchi roli bu botda qo'llab-quvvatlanmaydi — faqat o'qituvchilar uchun bot.
        # Agar foydalanuvchi student roliga ega bo'lsa, blok xabar yuboriladi.
        if not is_superadmin and not is_teacher and not is_librarian:
            if db_user.role == UserRole.student or (profile and profile.profile_type == "student"):
                from aiogram.types import Message, CallbackQuery
                if isinstance(event, Message) and event.chat.type == "private":
                    await event.answer(
                        "⛔ Bu bot faqat o'qituvchilar uchun. "
                        "Tez orada o'quvchilar uchun alohida bot ishga tushiriladi."
                    )
                    return
                if isinstance(event, CallbackQuery) and event.message and event.message.chat.type == "private":
                    await event.answer(
                        "⛔ Bu bot faqat o'qituvchilar uchun.",
                        show_alert=True,
                    )
                    return

        # Ma'lumotlarni data ga qo'shish
        data["db_user"] = db_user
        data["is_superadmin"] = is_superadmin
        data["is_teacher"] = is_teacher
        data["is_librarian"] = is_librarian
        data["is_student"] = False  # O'quvchi roli bu botda qo'llab-quvvatlanmaydi
        data["is_group_admin"] = is_group_admin
        data["profile"] = profile

        logger.info(
            f"SUPERADMIN CHECK: user_id={tg_user.id}, is_superadmin={is_superadmin}, role={db_user.role}"
        )

        return await handler(event, data)
