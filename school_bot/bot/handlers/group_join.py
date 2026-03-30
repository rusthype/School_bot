from __future__ import annotations

import uuid
from datetime import datetime

from aiogram import Router
from aiogram.types import ChatMemberUpdated, CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.pagination import SchoolPagination
from school_bot.bot.services.school_service import list_schools, get_school_by_id
from school_bot.bot.services.group_service import get_group_by_chat_id, get_group_by_name, add_group, update_group
from school_bot.database.models import User, UserRole
from school_bot.bot.services.logger_service import get_logger

router = Router(name=__name__)
logger = get_logger(__name__)


def _build_group_join_school_keyboard(chat_id: int, schools: list, page: int = 1, per_page: int = 10):
    pagination = SchoolPagination(page=page, per_page=per_page, total_schools=len(schools))
    start_index = (pagination.page - 1) * pagination.per_page
    end_index = start_index + pagination.per_page
    page_schools = schools[start_index:end_index]

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    for school in page_schools:
        builder.button(
            text=f"{school.number}-m",
            callback_data=f"groupjoin_school:{chat_id}:{school.id}:{pagination.page}",
        )

    nav_row = []
    if pagination.has_previous():
        nav_row.append(("◀️ Oldingi", f"groupjoin_page:{chat_id}:{pagination.page - 1}"))
    nav_row.append((f"📍 {pagination.page}/{pagination.total_pages}", f"groupjoin_page_info:{chat_id}:{pagination.page}"))
    if pagination.has_next():
        nav_row.append(("▶️ Keyingi", f"groupjoin_page:{chat_id}:{pagination.page + 1}"))

    for text, data in nav_row:
        builder.button(text=text, callback_data=data)

    builder.adjust(5)
    return builder.as_markup()


@router.my_chat_member()
async def on_bot_added_to_group(event: ChatMemberUpdated, session: AsyncSession) -> None:
    chat = event.chat
    if chat.type not in ("group", "supergroup"):
        return

    new_status = event.new_chat_member.status
    if new_status not in ("member", "administrator"):
        return

    if not event.new_chat_member.user.is_bot:
        return

    members_count = None
    try:
        members_count = await event.bot.get_chat_member_count(chat.id)
    except Exception:
        members_count = None
    members_display = members_count if members_count is not None else "Noma'lum"

    requested_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    result = await session.execute(select(User).where(User.role == UserRole.superadmin))
    superadmins = result.scalars().all()

    group = await get_group_by_chat_id(session, chat.id)
    if group:
        school_name = "Noma'lum"
        if group.school_id:
            school = await get_school_by_id(session, group.school_id)
            if school:
                school_name = school.name
        group_title = chat.title or group.name or f"Guruh {chat.id}"

        if group.status == "pending" or group.school_id is None:
            schools = await list_schools(session)
            if not schools:
                for su in superadmins:
                    await event.bot.send_message(
                        chat_id=su.telegram_id,
                        text=(
                            "❌ Maktablar ro'yxati bo'sh. Avval /add_school orqali maktab qo'shing.\n\n"
                            f"📌 Guruh nomi: {group_title}\n"
                            f"🆔 Chat ID: {chat.id}\n"
                            f"👥 A'zolar soni: {members_display}\n"
                            f"📅 Qo'shilgan vaqt: {requested_str}"
                        ),
                    )
                return
            total_pages = max(1, (len(schools) + 9) // 10)
            keyboard = _build_group_join_school_keyboard(chat.id, schools, page=1, per_page=10)
            for su in superadmins:
                await event.bot.send_message(
                    chat_id=su.telegram_id,
                    text=(
                        "⏳ Guruh kutish ro'yxatida. Maktabni tanlang.\n\n"
                        f"📌 Guruh nomi: {group_title}\n"
                        f"🆔 Chat ID: {chat.id}\n"
                        f"👥 A'zolar soni: {members_display}\n"
                        f"📅 Qo'shilgan vaqt: {requested_str}\n\n"
                        f"🏫 Maktabni tanlang (1/{total_pages} sahifa):"
                    ),
                    reply_markup=keyboard,
                )
            return
        for su in superadmins:
            await event.bot.send_message(
                chat_id=su.telegram_id,
                text=(
                    "⚠️ Bot allaqachon mavjud guruhga qo'shildi!\n\n"
                    f"📌 Yangi nom: {group_title}\n"
                    f"🆔 Chat ID: {chat.id}\n\n"
                    "📚 Database dagi ma'lumot:\n"
                    f"🏫 Maktab: {school_name}\n"
                    f"📌 Nom: {group.name}\n\n"
                    "🔄 Yangilash uchun /edit_group dan foydalaning."
                ),
            )
        return

    is_admin = new_status == "administrator"
    schools = await list_schools(session)

    admin_status = "Ha" if is_admin else "Yo'q"
    group_title = chat.title or f"Guruh {chat.id}"
    existing_name = await get_group_by_name(session, group_title)
    if existing_name and existing_name.chat_id != chat.id:
        group_title = f"{group_title} ({chat.id})"

    await add_group(
        session,
        name=group_title,
        chat_id=chat.id,
        school_id=None,
        status="pending",
    )

    if not schools:
        for su in superadmins:
            await event.bot.send_message(
                chat_id=su.telegram_id,
                text=(
                    "❌ Maktablar ro'yxati bo'sh. Avval /add_school orqali maktab qo'shing.\n\n"
                    f"📌 Guruh nomi: {group_title}\n"
                    f"🆔 Chat ID: {chat.id}\n"
                    f"👥 A'zolar soni: {members_display}\n"
                    f"🤖 Admin: {admin_status}\n"
                    f"📅 Qo'shilgan vaqt: {requested_str}"
                ),
            )
        return

    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = _build_group_join_school_keyboard(chat.id, schools, page=1, per_page=10)

    for su in superadmins:
        await event.bot.send_message(
            chat_id=su.telegram_id,
            text=(
                "🤖 Bot yangi guruhga qo'shildi!\n\n"
                f"📌 Guruh nomi: {group_title}\n"
                f"🆔 Chat ID: {chat.id}\n"
                f"👥 A'zolar soni: {members_display}\n"
                f"🤖 Admin: {admin_status}\n"
                f"📅 Qo'shilgan vaqt: {requested_str}\n\n"
                "ℹ️ Bu guruh database da mavjud emas.\n"
                f"🏫 Maktabni tanlang (1/{total_pages} sahifa):"
            ),
            reply_markup=keyboard,
        )


@router.callback_query(lambda c: c.data.startswith("groupjoin_page:"))
async def group_join_page(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        _, chat_id_str, page_str = callback.data.split(":")
        chat_id = int(chat_id_str)
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    schools = await list_schools(session)
    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = _build_group_join_school_keyboard(chat_id, schools, page=page, per_page=10)
    await callback.message.edit_text(
        f"🏫 Maktabni tanlang ({page}/{total_pages} sahifa):",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("groupjoin_page_info:"))
async def group_join_page_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("groupjoin_school:"))
async def group_join_select_school(callback: CallbackQuery, session: AsyncSession) -> None:
    try:
        _, chat_id_str, school_id_str, _page = callback.data.split(":")
        chat_id = int(chat_id_str)
        school_id = uuid.UUID(school_id_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    school = await get_school_by_id(session, school_id)
    if not school:
        await callback.answer("❌ Maktab topilmadi.", show_alert=True)
        return

    chat = await callback.bot.get_chat(chat_id)
    group = await get_group_by_chat_id(session, chat_id)

    group_title = chat.title or f"Guruh {chat_id}"
    existing_name = await get_group_by_name(session, group_title)
    if existing_name and existing_name.chat_id != chat_id:
        group_title = f"{group_title} ({chat_id})"

    if group:
        await update_group(session, group, name=group_title, school_id=school.id, status="active")
    else:
        await add_group(session, name=group_title, chat_id=chat_id, school_id=school.id, status="active")

    await callback.message.edit_text(
        "✅ Guruh muvaffaqiyatli sozlandi!\n"
        f"🏫 Maktab: {school.name}\n"
        f"📌 Guruh: {chat.title}\n"
        f"🆔 Chat ID: {chat_id}"
    )
    await callback.answer()
