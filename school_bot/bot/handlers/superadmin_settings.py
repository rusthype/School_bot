from __future__ import annotations

import re
from datetime import datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.bot_settings_service import get_or_create_settings, update_settings
from school_bot.bot.services.superadmin_menu_builder import SuperAdminMenuBuilder
from school_bot.database.models import BotSettings, User, UserRole

router = Router(name="superadmin_settings")

BOT_STARTED_AT = datetime.utcnow()


class BotSettingsStates(StatesGroup):
    waiting_monfri = State()
    waiting_sat = State()


def _bool_icon(value: bool) -> str:
    return "✅" if value else "❌"


def _lang_name(code: str) -> str:
    return {"uz": "O'zbek", "ru": "Rus", "en": "English"}.get(code, code)


def _build_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🤖 Bot ma'lumotlari", callback_data="settings:info"),
                InlineKeyboardButton(text="🌐 Til sozlamalari", callback_data="settings:lang"),
            ],
            [
                InlineKeyboardButton(text="🔒 Ruxsatlar", callback_data="settings:perms"),
                InlineKeyboardButton(text="⏱️ Ish vaqti", callback_data="settings:work"),
            ],
            [
                InlineKeyboardButton(text="🔔 Bildirishnomalar", callback_data="settings:notif"),
            ],
            [
                InlineKeyboardButton(text="🔄 Yangilash", callback_data="settings:refresh"),
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="settings:back"),
                InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="settings:home"),
            ],
        ]
    )


async def _format_main_settings(session: AsyncSession, bot_name: str, bot_id: int) -> str:
    settings = await get_or_create_settings(session)
    bot_display = f"@{bot_name}" if bot_name and not bot_name.startswith("@") else bot_name or "Noma'lum"
    superadmins = await session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.superadmin))
    teachers = await session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.teacher))
    librarians = await session.scalar(select(func.count()).select_from(User).where(User.role == UserRole.librarian))

    superadmins = superadmins or 0
    teachers = teachers or 0
    librarians = librarians or 0

    work_sun_text = "❌ Yopiq"
    if settings.work_sun:
        work_sun_text = f"✅ Ochilgan ({settings.work_start_sat}-{settings.work_end_sat})"

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ BOT SOZLAMALARI\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🤖 Bot ma'lumotlari\n"
        f"├─ Nomi: {bot_display}\n"
        f"├─ ID: {bot_id}\n"
        f"└─ Versiya: {settings.bot_version}\n\n"
        "🌐 Til sozlamalari\n"
        f"├─ 🇺🇿 O'zbek\n"
        f"├─ 🇷🇺 Русский\n"
        f"└─ 🇬🇧 English\n"
        f"  Hozirgi: {_lang_name(settings.language)}\n\n"
        "🔒 Ruxsatlar\n"
        f"├─ 👑 Superadmin: {superadmins} ta\n"
        f"├─ 👨‍💼 Admin: {librarians} ta\n"
        f"└─ 🎓 Teacher: {teachers} ta\n\n"
        "⏱️ Ish vaqti\n"
        f"├─ Dushanba-Juma: {settings.work_start_mon_fri}-{settings.work_end_mon_fri}\n"
        f"├─ Shanba: {settings.work_start_sat}-{settings.work_end_sat}\n"
        f"└─ Yakshanba: {work_sun_text}\n\n"
        "🔔 Bildirishnomalar\n"
        f"├─ Yangi topshiriq: {_bool_icon(settings.notify_homework)}\n"
        f"├─ E'lonlar: {_bool_icon(settings.notify_announcements)}\n"
        f"├─ Statistika: {_bool_icon(settings.notify_stats)}\n"
        f"└─ Marketing: {_bool_icon(settings.notify_marketing)}\n"
    )
    return text


def _build_info_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Yangilash", callback_data="settings:info"),
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="settings:back"),
            ]
        ]
    )


def _build_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇿 Tanlash", callback_data="settings:lang:uz"),
                InlineKeyboardButton(text="🇷🇺 Tanlash", callback_data="settings:lang:ru"),
                InlineKeyboardButton(text="🇬🇧 Tanlash", callback_data="settings:lang:en"),
            ],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="settings:back")],
        ]
    )


def _build_work_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Mon-Fri", callback_data="settings:work:monfri"),
                InlineKeyboardButton(text="✏️ Shanba", callback_data="settings:work:sat"),
            ],
            [
                InlineKeyboardButton(text="🔄 Yakshanba", callback_data="settings:work:sun"),
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="settings:back"),
            ],
        ]
    )


def _build_notif_keyboard(settings: BotSettings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📝 Topshiriq {_bool_icon(settings.notify_homework)}",
                    callback_data="settings:notif:homework",
                ),
                InlineKeyboardButton(
                    text=f"📢 E'lonlar {_bool_icon(settings.notify_announcements)}",
                    callback_data="settings:notif:announcements",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"📊 Statistika {_bool_icon(settings.notify_stats)}",
                    callback_data="settings:notif:stats",
                ),
                InlineKeyboardButton(
                    text=f"📧 Marketing {_bool_icon(settings.notify_marketing)}",
                    callback_data="settings:notif:marketing",
                ),
            ],
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="settings:back")],
        ]
    )


@router.message(F.text == "⚙️ Bot sozlamalari")
async def open_settings_menu(message: Message, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await message.answer("❌ Kechirasiz, bu bo'lim faqat superadmin uchun.")
        return

    bot = await message.bot.get_me()
    text = await _format_main_settings(session, bot.username or "Noma'lum", bot.id)
    await message.answer(text, reply_markup=_build_settings_menu())


@router.callback_query(F.data == "settings:refresh")
async def refresh_settings(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return

    bot = await callback.bot.get_me()
    text = await _format_main_settings(session, bot.username or "Noma'lum", bot.id)
    await callback.message.answer(text, reply_markup=_build_settings_menu())
    await callback.answer()


@router.callback_query(F.data == "settings:back")
async def settings_back(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    bot = await callback.bot.get_me()
    text = await _format_main_settings(session, bot.username or "Noma'lum", bot.id)
    await callback.message.answer(text, reply_markup=_build_settings_menu())
    await callback.answer()


@router.callback_query(F.data == "settings:home")
async def settings_home(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    builder = SuperAdminMenuBuilder()
    await callback.message.answer("🏠 Asosiy menyu", reply_markup=builder.build_main_keyboard())
    await callback.answer()


@router.callback_query(F.data == "settings:info")
async def settings_info(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return

    settings = await get_or_create_settings(session)
    bot = await callback.bot.get_me()
    total_users = await session.scalar(select(func.count()).select_from(User)) or 0
    uptime = datetime.utcnow() - BOT_STARTED_AT
    uptime_str = f"{uptime.days} kun {uptime.seconds // 3600} soat"

    bot_username = bot.username or "Noma'lum"
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 Bot ma'lumotlari\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 Nomi: @{bot_username}\n"
        f"🆔 ID: {bot.id}\n"
        f"📦 Versiya: {settings.bot_version}\n"
        f"⏱️ Ish vaqti: {uptime_str}\n"
        f"👥 Jami userlar: {total_users}\n"
    )
    await callback.message.answer(text, reply_markup=_build_info_keyboard())
    await callback.answer()


@router.callback_query(F.data == "settings:lang")
async def settings_language(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return

    settings = await get_or_create_settings(session)
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 Til sozlamalari\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🇺🇿 O'zbek tili {'(hozirgi)' if settings.language == 'uz' else ''}\n"
        f"🇷🇺 Rus tili {'(hozirgi)' if settings.language == 'ru' else ''}\n"
        f"🇬🇧 English {'(hozirgi)' if settings.language == 'en' else ''}\n"
    )
    await callback.message.answer(text, reply_markup=_build_language_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("settings:lang:"))
async def settings_language_set(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, _, lang = callback.data.split(":", 2)
    await update_settings(session, language=lang)
    await callback.message.answer(f"✅ Til yangilandi: {_lang_name(lang)}")
    await callback.answer()


@router.callback_query(F.data == "settings:perms")
async def settings_permissions(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return

    superadmins = (await session.execute(select(User).where(User.role == UserRole.superadmin))).scalars().all()
    teachers = (await session.execute(select(User).where(User.role == UserRole.teacher))).scalars().all()
    librarians = (await session.execute(select(User).where(User.role == UserRole.librarian))).scalars().all()

    def fmt_users(users: list[User]) -> str:
        if not users:
            return "—"
        lines = []
        for user in users[:5]:
            label = user.username or user.full_name or str(user.telegram_id)
            lines.append(f"• {label} ({user.telegram_id})")
        if len(users) > 5:
            lines.append(f"... va yana {len(users) - 5} ta")
        return "\n".join(lines)

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔒 Ruxsatlar boshqaruvi\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👑 Superadminlar:\n{fmt_users(superadmins)}\n\n"
        f"👨‍💼 Adminlar:\n{fmt_users(librarians)}\n\n"
        f"🎓 Teacherlar:\n{fmt_users(teachers)}\n"
    )
    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="settings:back")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "settings:work")
async def settings_work(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    settings = await get_or_create_settings(session)
    work_sun_text = "❌ Yopiq" if not settings.work_sun else "✅ Ochilgan"
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱️ Ish vaqti sozlamalari\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Dushanba-Juma: {settings.work_start_mon_fri}-{settings.work_end_mon_fri}\n"
        f"Shanba: {settings.work_start_sat}-{settings.work_end_sat}\n"
        f"Yakshanba: {work_sun_text}\n\n"
        "Vaqt format: 08:00-18:00"
    )
    await callback.message.answer(text, reply_markup=_build_work_keyboard())
    await callback.answer()


@router.callback_query(F.data == "settings:work:monfri")
async def settings_work_monfri(callback: CallbackQuery, state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    await state.set_state(BotSettingsStates.waiting_monfri)
    await callback.message.answer("Mon-Fri vaqtini yuboring (masalan: 08:00-18:00)")
    await callback.answer()


@router.callback_query(F.data == "settings:work:sat")
async def settings_work_sat(callback: CallbackQuery, state: FSMContext, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    await state.set_state(BotSettingsStates.waiting_sat)
    await callback.message.answer("Shanba vaqtini yuboring (masalan: 09:00-14:00)")
    await callback.answer()


@router.callback_query(F.data == "settings:work:sun")
async def settings_work_sun(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    settings = await get_or_create_settings(session)
    await update_settings(session, work_sun=not settings.work_sun)
    await callback.message.answer("✅ Yakshanba holati yangilandi")
    await callback.answer()


@router.message(BotSettingsStates.waiting_monfri)
async def settings_save_monfri(message: Message, state: FSMContext, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await state.clear()
        return
    value = (message.text or "").strip()
    if not re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", value):
        await message.answer("❌ Noto'g'ri format. Masalan: 08:00-18:00")
        return
    start, end = value.split("-", 1)
    await update_settings(session, work_start_mon_fri=start, work_end_mon_fri=end)
    await state.clear()
    await message.answer("✅ Mon-Fri vaqti yangilandi")


@router.message(BotSettingsStates.waiting_sat)
async def settings_save_sat(message: Message, state: FSMContext, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await state.clear()
        return
    value = (message.text or "").strip()
    if not re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", value):
        await message.answer("❌ Noto'g'ri format. Masalan: 09:00-14:00")
        return
    start, end = value.split("-", 1)
    await update_settings(session, work_start_sat=start, work_end_sat=end)
    await state.clear()
    await message.answer("✅ Shanba vaqti yangilandi")


@router.callback_query(F.data == "settings:notif")
async def settings_notifications(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    settings = await get_or_create_settings(session)
    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔔 Bildirishnoma sozlamalari\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Yangi topshiriq: {_bool_icon(settings.notify_homework)}\n"
        f"E'lonlar: {_bool_icon(settings.notify_announcements)}\n"
        f"Statistika: {_bool_icon(settings.notify_stats)}\n"
        f"Marketing: {_bool_icon(settings.notify_marketing)}\n"
    )
    await callback.message.answer(text, reply_markup=_build_notif_keyboard(settings))
    await callback.answer()


@router.callback_query(F.data.startswith("settings:notif:"))
async def settings_notifications_toggle(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    _, _, key = callback.data.split(":", 2)
    settings = await get_or_create_settings(session)

    field_map = {
        "homework": "notify_homework",
        "announcements": "notify_announcements",
        "stats": "notify_stats",
        "marketing": "notify_marketing",
    }
    field = field_map.get(key)
    if not field:
        await callback.answer()
        return
    current = getattr(settings, field)
    await update_settings(session, **{field: not current})
    await callback.message.answer("✅ Bildirishnoma sozlamasi yangilandi")
    await callback.answer()
