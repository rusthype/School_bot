from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.group_service import list_groups
from school_bot.database.models import Profile, User, UserRole


_APPROVAL_SELECTIONS: dict[tuple[int, int], set[int]] = defaultdict(set)


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


def build_approval_keyboard(
    profile_id: int,
    groups: list,
    selected_ids: set[int],
) -> InlineKeyboardMarkup:
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


async def notify_superusers_new_registration(
    session: AsyncSession,
    bot,
    profile: Profile,
) -> None:
    groups = await list_groups(session)
    result = await session.execute(select(User).where(User.role == UserRole.superuser))
    superusers = result.scalars().all()

    # Load user for username and telegram id
    user = await session.get(User, profile.user_id)
    username = f"@{user.username}" if user and user.username else "(foydalanuvchi nomi yo'q)"
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()

    requested = profile.registered_at or datetime.utcnow()
    requested_str = requested.strftime("%d.%m.%Y %H:%M")
    user_id_display = user.telegram_id if user else "Noma'lum"

    message_text = (
        "👑 Yangi o'qituvchi ro'yxatdan o'tishi:\n\n"
        f"👤 Ism: {full_name}\n"
        f"🔹 Foydalanuvchi nomi: {username}\n"
        f"📱 Telefon: {profile.phone}\n"
        f"🆔 Telegram ID: {user_id_display}\n"
        f"📅 So'rov vaqti: {requested_str}\n\n"
        "Ushbu o'qituvchi uchun guruhlarni tanlang:"
    )

    for superuser in superusers:
        keyboard = build_approval_keyboard(profile.id, groups, set())
        try:
            await bot.send_message(
                chat_id=superuser.telegram_id,
                text=message_text,
                reply_markup=keyboard,
            )
        except Exception:
            continue
