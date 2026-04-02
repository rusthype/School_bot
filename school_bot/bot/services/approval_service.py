from __future__ import annotations

import json
from datetime import datetime, timezone

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.config import Settings
from school_bot.bot.services.pagination import SchoolPagination
from school_bot.database.models import Profile, User, UserRole, School


logger = get_logger(__name__)

_APPROVAL_TTL = 86400  # 24 hours (was 10 minutes in-memory)
_redis: Redis | None = None


async def get_redis() -> Redis:
    """Return a shared Redis client for approval state, lazily initialised."""
    global _redis
    if _redis is None:
        settings = Settings()
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _sel_key(admin_id: int, profile_id: int) -> str:
    return f"approval:selections:{admin_id}:{profile_id}"


def _school_key(admin_id: int, profile_id: int) -> str:
    return f"approval:school:{admin_id}:{profile_id}"


# --------------- selections ---------------

async def get_selected_group_ids(admin_id: int, profile_id: int) -> set[str]:
    r = await get_redis()
    raw = await r.get(_sel_key(admin_id, profile_id))
    if raw is None:
        return set()
    return set(json.loads(raw))


async def toggle_selected_group(admin_id: int, profile_id: int, group_id: int) -> set[str]:
    key = _sel_key(admin_id, profile_id)
    r = await get_redis()
    raw = await r.get(key)
    selected: set[str] = set(json.loads(raw)) if raw else set()
    group_id_str = str(group_id)
    if group_id_str in selected:
        selected.discard(group_id_str)
    else:
        selected.add(group_id_str)
    await r.set(key, json.dumps(list(selected)), ex=_APPROVAL_TTL)
    return set(selected)


async def clear_selections_for_profile(profile_id: int) -> None:
    """Remove all approval state keys for a given profile (any admin)."""
    r = await get_redis()
    pattern = f"approval:*:*:{profile_id}"
    cursor: int | str = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            await r.delete(*keys)
        if cursor == 0:
            break


# --------------- school selection ---------------

async def set_selected_school(admin_id: int, profile_id: int, school_id: int) -> None:
    r = await get_redis()
    await r.set(_school_key(admin_id, profile_id), str(school_id), ex=_APPROVAL_TTL)


async def get_selected_school(admin_id: int, profile_id: int) -> int | None:
    r = await get_redis()
    raw = await r.get(_school_key(admin_id, profile_id))
    return int(raw) if raw else None


# --------------- keyboard builders (unchanged logic) ---------------

def build_school_keyboard(
    profile_id: int,
    schools: list[School],
    page: int = 1,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    pagination = SchoolPagination(page=page, per_page=per_page, total_schools=len(schools))
    start_index = (pagination.page - 1) * pagination.per_page
    end_index = start_index + pagination.per_page
    page_schools = schools[start_index:end_index]

    builder = InlineKeyboardBuilder()
    for school in page_schools:
        builder.button(
            text=f"{school.number}-m",
            callback_data=f"approve_school:{profile_id}:{school.id}",
        )

    nav_row = []
    if pagination.has_previous():
        nav_row.append(("◀️ Oldingi", f"school_page:{profile_id}:{pagination.page - 1}"))
    nav_row.append((f"📍 {pagination.page}/{pagination.total_pages}", f"school_page_info:{profile_id}:{pagination.page}"))
    if pagination.has_next():
        nav_row.append(("▶️ Keyingi", f"school_page:{profile_id}:{pagination.page + 1}"))

    for text, data in nav_row:
        builder.button(text=text, callback_data=data)

    builder.adjust(5)
    return builder.as_markup()


async def build_approval_keyboard(
    session: AsyncSession,
    profile_id: int,
    school_id: int,
    selected_ids: set[str],
) -> InlineKeyboardMarkup:
    from school_bot.bot.services.group_service import list_groups_by_school
    groups = await list_groups_by_school(session, school_id)
    builder = InlineKeyboardBuilder()

    for group in groups:
        checked = "✅" if str(group.id) in selected_ids else "⬜"
        builder.button(
            text=f"{checked} {group.name}",
            callback_data=f"approve_toggle:{profile_id}:{group.id}",
        )

    builder.button(
        text="✅ Tanlangan guruhlar bilan tasdiqlash",
        callback_data=f"approve_confirm:{profile_id}",
    )
    builder.button(
        text="❌ Rad etish",
        callback_data=f"approve_reject:{profile_id}",
    )

    builder.adjust(1)
    return builder.as_markup()


async def notify_superadmins_new_registration(
    session: AsyncSession,
    bot,
    profile: Profile,
) -> None:
    result = await session.execute(select(User).where(User.role == UserRole.superadmin))
    superadmins = result.scalars().all()

    if not superadmins:
        settings = Settings()
        if settings.superadmin_ids:
            for tg_id in settings.superadmin_ids:
                user = (await session.execute(select(User).where(User.telegram_id == tg_id))).scalar_one_or_none()
                if user is None:
                    user = User(telegram_id=tg_id, full_name=None, role=UserRole.superadmin)
                    session.add(user)
                    await session.commit()
                    await session.refresh(user)
                else:
                    if user.role != UserRole.superadmin:
                        user.role = UserRole.superadmin
                        await session.commit()
                superadmins.append(user)

    # Load user for username and telegram id
    user = await session.get(User, profile.bot_user_id)
    username = f"@{user.username}" if user and user.username else "(foydalanuvchi nomi yo'q)"
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()

    requested = profile.registered_at or datetime.now(timezone.utc)
    requested_str = requested.strftime("%d.%m.%Y %H:%M")
    user_id_display = user.telegram_id if user else "Noma'lum"
    school_name = None
    if profile.school_id:
        school = await session.get(School, profile.school_id)
        school_name = school.name if school else None

    # If school_id is not set, check last_name for free-text school entered during
    # the post-role registration flow (stored there until admin assigns a real school).
    school_display = school_name
    if not school_display and profile.last_name:
        school_display = profile.last_name  # free-text school name from registration

    role_label = {
        "teacher": "O'qituvchi",
        "parent": "Ota-ona",
    }.get(profile.profile_type or "", profile.profile_type or "Noma'lum")

    message_text = (
        "Yangi foydalanuvchi ro'yxatdan o'tishi:\n\n"
        f"Ism: {full_name}\n"
        f"Rol: {role_label}\n"
        f"Foydalanuvchi nomi: {username}\n"
        f"Telefon: {profile.phone or '—'}\n"
        f"Telegram ID: {user_id_display}\n"
        f"Maktab (o'zi kiritgan): {school_display or 'Kiritilmagan'}\n"
        f"So'rov vaqti: {requested_str}"
    )

    schools_result = await session.execute(select(School).order_by(School.number))
    schools = list(schools_result.scalars().all())
    for superadmin in superadmins:
        try:
            if profile.school_id:
                school = await session.get(School, profile.school_id)
                if school:
                    await set_selected_school(superadmin.id, profile.id, school.id)
                    keyboard = await build_approval_keyboard(session, profile.id, school.id, set())
                    await bot.send_message(
                        chat_id=superadmin.telegram_id,
                        text=f"{message_text}\n\n📚 {school.name} uchun guruhlarni tanlang:",
                        reply_markup=keyboard,
                    )
                else:
                    keyboard = build_school_keyboard(profile.id, schools, page=1, per_page=10)
                    await bot.send_message(
                        chat_id=superadmin.telegram_id,
                        text=f"{message_text}\n\n🏫 Maktabni tanlang (1/{max(1, (len(schools)+9)//10)} sahifa):",
                        reply_markup=keyboard,
                    )
            else:
                keyboard = build_school_keyboard(profile.id, schools, page=1, per_page=10)
                await bot.send_message(
                    chat_id=superadmin.telegram_id,
                    text=f"{message_text}\n\n🏫 Maktabni tanlang (1/{max(1, (len(schools)+9)//10)} sahifa):",
                    reply_markup=keyboard,
                )

            logger.info(
                f"Yangi ro'yxatdan o'tish superadminga yuborildi: {superadmin.telegram_id}",
                extra={"user_id": superadmin.telegram_id, "chat_id": superadmin.telegram_id, "command": "approval_notify"},
            )
        except Exception:
            logger.error(
                f"Superadminga xabar yuborilmadi: {superadmin.telegram_id}",
                exc_info=True,
                extra={"user_id": superadmin.telegram_id, "chat_id": superadmin.telegram_id, "command": "approval_notify"},
            )
            continue
