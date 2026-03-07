from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.pagination import SchoolPagination
from school_bot.database.models import Profile, User, UserRole, School


_APPROVAL_SELECTIONS: dict[tuple[int, int], set[int]] = defaultdict(set)
_APPROVAL_SCHOOLS: dict[tuple[int, int], int] = {}
logger = get_logger(__name__)


def get_selected_group_ids(admin_id: int, profile_id: int) -> set[int]:
    return set(_APPROVAL_SELECTIONS.get((admin_id, profile_id), set()))


def toggle_selected_group(admin_id: int, profile_id: int, group_id: int) -> set[int]:
    key = (admin_id, profile_id)
    selected = _APPROVAL_SELECTIONS.get(key, set())
    if group_id in selected:
        selected.discard(group_id)
    else:
        selected.add(group_id)
    _APPROVAL_SELECTIONS[key] = selected
    return set(selected)


def clear_selections_for_profile(profile_id: int) -> None:
    keys = [key for key in _APPROVAL_SELECTIONS.keys() if key[1] == profile_id]
    for key in keys:
        _APPROVAL_SELECTIONS.pop(key, None)
    school_keys = [key for key in _APPROVAL_SCHOOLS.keys() if key[1] == profile_id]
    for key in school_keys:
        _APPROVAL_SCHOOLS.pop(key, None)


def set_selected_school(admin_id: int, profile_id: int, school_id: int) -> None:
    _APPROVAL_SCHOOLS[(admin_id, profile_id)] = school_id


def get_selected_school(admin_id: int, profile_id: int) -> int | None:
    return _APPROVAL_SCHOOLS.get((admin_id, profile_id))


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
    selected_ids: set[int],
) -> InlineKeyboardMarkup:
    from school_bot.bot.services.group_service import list_groups_by_school
    groups = await list_groups_by_school(session, school_id)
    builder = InlineKeyboardBuilder()

    for group in groups:
        checked = "✅" if group.id in selected_ids else "⬜"
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

    # Load user for username and telegram id
    user = await session.get(User, profile.user_id)
    username = f"@{user.username}" if user and user.username else "(foydalanuvchi nomi yo'q)"
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()

    requested = profile.registered_at or datetime.utcnow()
    requested_str = requested.strftime("%d.%m.%Y %H:%M")
    user_id_display = user.telegram_id if user else "Noma'lum"
    school_name = None
    if profile.school_id:
        school = await session.get(School, profile.school_id)
        school_name = school.name if school else None

    message_text = (
        "👑 Yangi o'qituvchi ro'yxatdan o'tishi:\n\n"
        f"👤 Ism: {full_name}\n"
        f"🔹 Foydalanuvchi nomi: {username}\n"
        f"📱 Telefon: {profile.phone}\n"
        f"🆔 Telegram ID: {user_id_display}\n"
        f"🏫 Tanlangan maktab: {school_name or 'Tanlanmagan'}\n"
        f"📅 So'rov vaqti: {requested_str}"
    )

    schools_result = await session.execute(select(School).order_by(School.number))
    schools = list(schools_result.scalars().all())
    for superadmin in superadmins:
        try:
            if profile.school_id:
                school = await session.get(School, profile.school_id)
                if school:
                    set_selected_school(superadmin.id, profile.id, school.id)
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
