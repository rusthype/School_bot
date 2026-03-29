import re
import time
import asyncio
import os
import shutil
import tarfile
from pathlib import Path
from typing import Union
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BufferedInputFile,
    FSInputFile,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.engine.url import make_url
from datetime import datetime
from school_bot.database.models import User, UserRole, Task, School, PollVote
from school_bot.bot.states.new_task import NewTaskStates
from school_bot.bot.states.registration import RegistrationStates
from school_bot.bot.states.book_states import CategoryAddStates
from school_bot.bot.config import Settings
from school_bot.bot.services.profile_service import upsert_profile, upsert_student_profile, can_register_again
from school_bot.bot.services.profile_service import revoke_teacher
from school_bot.bot.services.approval_service import notify_superadmins_new_registration
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.superadmin_menu_builder import SuperAdminMenuBuilder
from school_bot.bot.services.school_service import list_schools, get_school_by_id, get_school_by_number
from school_bot.bot.services.pagination import SchoolPagination
from school_bot.bot.utils.telegram import send_chunked_message
from school_bot.database.models import Profile, Book
router = Router(name=__name__)
logger = get_logger(__name__)
LAST_MENU_MESSAGE: dict[int, int] = {}
async def _send_menu(message: Message, text: str, reply_markup):
    user_id = message.from_user.id if message.from_user else None
    last_id = LAST_MENU_MESSAGE.get(user_id) if user_id else None
    if last_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=last_id,
                text=text,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            pass
    sent = await message.answer(text, reply_markup=reply_markup)
    if user_id:
        LAST_MENU_MESSAGE[user_id] = sent.message_id
    return sent
async def _get_superadmin_overview(session: AsyncSession):
    total_users = await session.scalar(select(func.count()).select_from(User)) or 0
    admin_users = await session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role.in_([UserRole.superadmin, UserRole.librarian]))
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
    from school_bot.bot.services.superadmin_menu_builder import SuperAdminOverview
    return SuperAdminOverview(
        total_users=total_users,
        admin_users=admin_users,
        teacher_users=teacher_users,
        student_users=student_users,
        book_count=book_count,
        task_count=task_count,
        db_size_mb=db_size_mb,
    )
class RemoveTeacherStates:
    waiting_for_selection = "waiting_for_selection"
def get_teacher_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="📝 Yangi topshiriq"),
        KeyboardButton(text="📊 Ovozlar"),
    )
    builder.row(
        KeyboardButton(text="📍 Keldim"),
        KeyboardButton(text="🚪 Ketdim"),
    )
    builder.row(
        KeyboardButton(text="📚 Kitoblar"),
        KeyboardButton(text="👥 O'quvchilar"),
    )
    builder.row(
        KeyboardButton(text="📈 Statistika"),
        KeyboardButton(text="⚙️ Sozlamalar"),
    )
    builder.row(KeyboardButton(text="❓ Yordam"))
    builder.row(KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_admin_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="👥 Foydalanuvchilar"),
        KeyboardButton(text="👨‍🏫 O'qituvchilar"),
    )
    builder.row(
        KeyboardButton(text="📚 Kitoblar"),
        KeyboardButton(text="📊 Umumiy statistika"),
    )
    builder.row(
        KeyboardButton(text="📚 GURUHLAR"),
        KeyboardButton(text="🏫 Maktablar"),
    )
    builder.row(
        KeyboardButton(text="🕒 Davomat"),
    )
    builder.row(
        KeyboardButton(text="⚙️ Bot sozlamalari"),
        KeyboardButton(text="📢 Xabarnoma"),
    )
    builder.row(
        KeyboardButton(text="📥 Backup"),
        KeyboardButton(text="❓ Yordam"),
    )
    builder.row(KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_librarian_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📚 Kutilayotgan buyurtmalar"))
    builder.row(KeyboardButton(text="🔄 Jarayondagi buyurtmalar"))
    builder.row(KeyboardButton(text="✅ Tasdiqlangan buyurtmalar"))
    builder.row(KeyboardButton(text="📦 Yetkazilgan buyurtmalar"))
    builder.row(KeyboardButton(text="📦 Barcha buyurtmalar"))
    builder.row(KeyboardButton(text="📊 Buyurtma statistikasi"))
    builder.row(KeyboardButton(text="❓ Yordam"))
    builder.row(KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_main_keyboard(
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> ReplyKeyboardMarkup:
    """Asosiy menyu tugmalarini yaratish"""
    logger.info(
        f"Building keyboard - superadmin={is_superadmin}, teacher={is_teacher}, librarian={is_librarian}"
    )
    if is_superadmin:
        return get_admin_keyboard()
    if is_librarian:
        return get_librarian_keyboard()
    if is_teacher:
        return get_teacher_keyboard()
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🏠 Bosh menyu"),
        KeyboardButton(text="❓ Yordam"),
    )
    if not (is_superadmin or is_librarian):
        builder.row(KeyboardButton(text="📞 Admin bilan bog'lanish"))
    keyboard = builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
    logger.info(f"Keyboard button rows: {len(keyboard.keyboard)}")
    return keyboard
def get_users_management_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="👥 Foydalanuvchilar"))
    builder.row(KeyboardButton(text="👨‍🏫 O'qituvchilar ro'yxati"))
    builder.row(KeyboardButton(text="⏳ Kutilayotganlar"))
    builder.row(KeyboardButton(text="❌ O'qituvchi o'chirish"))
    builder.row(KeyboardButton(text="❌ Foydalanuvchi o'chirish"))
    builder.row(KeyboardButton(text="➕ Admin qo'shish"))
    builder.row(KeyboardButton(text="❌ Admin o'chirish"))
    builder.row(KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_student_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📚 Kitoblar"))
    builder.row(KeyboardButton(text="📘 Topshiriqlar"))
    builder.row(KeyboardButton(text="📊 Baholar"))
    builder.row(KeyboardButton(text="❓ Yordam"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_teacher_votes_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📋 Joriy topshiriqlar"))
    builder.row(KeyboardButton(text="📊 Baholar jurnali"))
    builder.row(KeyboardButton(text="📈 O'rtacha ball"))
    builder.row(KeyboardButton(text="📤 Eksport"))
    builder.row(KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_teacher_books_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📚 Kitob buyurtma qilish"))
    builder.row(KeyboardButton(text="📦 Mening buyurtmalarim"))
    builder.row(KeyboardButton(text="📤 Yuklash"))
    builder.row(KeyboardButton(text="📋 Barcha kitoblar"))
    builder.row(KeyboardButton(text="🔍 Qidirish"))
    builder.row(KeyboardButton(text="📂 Kategoriyalar"))
    builder.row(KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_teacher_students_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="➕ Yangi o'quvchi"))
    builder.row(KeyboardButton(text="📋 Ro'yxat"))
    builder.row(KeyboardButton(text="📊 Davomat"))
    builder.row(KeyboardButton(text="📧 Xabar yuborish"))
    builder.row(KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_teacher_stats_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="👥 Faol o'quvchilar"))
    builder.row(KeyboardButton(text="📝 Topshiriqlar"))
    builder.row(KeyboardButton(text="📚 Kitoblar (stat)"))
    builder.row(KeyboardButton(text="📊 Umumiy hisobot"))
    builder.row(KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
def get_teacher_settings_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🔔 Bildirishnomalar"))
    builder.row(KeyboardButton(text="🔒 Maxfiylik"))
    builder.row(KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu"))
    return builder.as_markup(resize_keyboard=True, input_field_placeholder="👇 Menyudan tanlang...")
async def _try_edit_prompt(
    bot,
    chat_id: int,
    message_id: int | None,
    text: str,
    reply_markup=None,
) -> bool:
    if not message_id:
        return False
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True
    except Exception:
        return False
async def cancel_current_action(
    target: Union[Message, CallbackQuery],
    state: FSMContext,
    db_user=None,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    try:
        data = await state.get_data()
    except Exception:
        data = {}
    last_prompt_id = data.get("last_prompt_message_id")
    await state.clear()
    if db_user and getattr(db_user, "role", None) == UserRole.superadmin:
        is_superadmin = True
        is_teacher = False
        is_librarian = False
    keyboard = get_main_keyboard(is_superadmin, is_teacher, is_librarian)
    if isinstance(target, CallbackQuery):
        edited = False
        if target.message:
            edited = await _try_edit_prompt(
                target.bot,
                target.message.chat.id,
                target.message.message_id,
                "✅ Jarayon bekor qilindi.",
                reply_markup=keyboard,
            )
        if not edited and target.message:
            try:
                await target.message.delete()
            except Exception:
                pass
            await target.message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)
        await target.answer()
        return
    # Message flow: try edit last prompt, fall back to previous message
    edited = False
    if last_prompt_id:
        edited = await _try_edit_prompt(
            target.bot,
            target.chat.id,
            last_prompt_id,
            "✅ Jarayon bekor qilindi.",
            reply_markup=keyboard,
        )
    if not edited:
        edited = await _try_edit_prompt(
            target.bot,
            target.chat.id,
            target.message_id - 1,
            "✅ Jarayon bekor qilindi.",
            reply_markup=keyboard,
        )
    if edited:
        return
    await target.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)
async def exit_to_main_menu(
    message: Message,
    state: FSMContext,
    db_user=None,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
    notice: str | None = "✅ Jarayon bekor qilindi.",
) -> None:
    try:
        data = await state.get_data()
    except Exception:
        data = {}
    last_prompt_id = data.get("last_prompt_message_id")
    await state.clear()
    await state.update_data(menu_active=True)
    if db_user and getattr(db_user, "role", None) == UserRole.superadmin:
        is_superadmin = True
        is_teacher = False
        is_librarian = False
    keyboard = get_main_keyboard(is_superadmin, is_teacher, is_librarian)
    edited = False
    if last_prompt_id:
        edited = await _try_edit_prompt(
            message.bot,
            message.chat.id,
            last_prompt_id,
            notice or "Asosiy menyu",
            reply_markup=keyboard,
        )
    if not edited:
        edited = await _try_edit_prompt(
            message.bot,
            message.chat.id,
            message.message_id - 1,
            notice or "Asosiy menyu",
            reply_markup=keyboard,
        )
    if edited:
        return
    if notice:
        await message.answer(notice, reply_markup=keyboard)
    else:
        await message.answer("Asosiy menyu", reply_markup=keyboard)
def get_registration_start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Ro'yxatdan o'tish"), KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tanlang..."
    )
def get_contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Telefon raqamini yuboring..."
    )
def get_skip_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭️ O'tkazib yuborish"), KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tanlang..."
    )
def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tanlang..."
    )
def get_contact_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Telefon raqamini yuboring..."
    )
def build_school_keyboard(prefix: str, schools: list) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for school in schools:
        builder.button(text=school.name, callback_data=f"{prefix}:{school.id}")
    builder.adjust(5)
    return builder
def build_registration_school_keyboard(
    schools: list,
    page: int = 1,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    pagination = SchoolPagination(page=page, per_page=per_page, total_schools=len(schools))
    start_index = (pagination.page - 1) * pagination.per_page
    end_index = start_index + pagination.per_page
    page_schools = schools[start_index:end_index]
    builder = InlineKeyboardBuilder()
    for i in range(0, len(page_schools), 5):
        row = []
        for school in page_schools[i:i + 5]:
            row.append(
                InlineKeyboardButton(
                    text=f"{school.number}-m",
                    callback_data=f"reg_school_select:{school.number}",
                )
            )
        if row:
            builder.row(*row)
    nav_row = []
    if pagination.has_previous():
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Oldingi",
                callback_data=f"reg_school_page:{pagination.page - 1}",
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"📍 {pagination.page}/{pagination.total_pages}",
            callback_data="reg_school_page_info",
        )
    )
    if pagination.has_next():
        nav_row.append(
            InlineKeyboardButton(
                text="▶️ Keyingi",
                callback_data=f"reg_school_page:{pagination.page + 1}",
            )
        )
    if nav_row:
        builder.row(*nav_row)
    builder.row(
        InlineKeyboardButton(
            text="❌ Bekor qilish",
            callback_data="reg_school_cancel",
        )
    )
    return builder.as_markup()

@router.message(RegistrationStates.welcome, F.text == "✅ Ro'yxatdan o'tish")
async def registration_begin(
    message: Message,
    state: FSMContext,
) -> None:
    await state.set_state(RegistrationStates.first_name)
    await message.answer(
        "👤 Iltimos, ismingizni kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(RegistrationStates.welcome, F.text == "❌ Bekor qilish")
async def registration_cancel_welcome(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()
    await message.answer("❌ Ro'yxatdan o'tish bekor qilindi.", reply_markup=ReplyKeyboardRemove())


@router.message(RegistrationStates.first_name, F.text)
async def registration_first_name(
    message: Message,
    state: FSMContext,
) -> None:
    first_name = (message.text or "").strip()
    if not first_name:
        await message.answer("❌ Ism bo'sh bo'lmasligi kerak. Qayta kiriting:")
        return
    await state.update_data(first_name=first_name)
    await state.set_state(RegistrationStates.last_name)
    await message.answer("👤 Iltimos, familiyangizni kiriting:")


@router.message(RegistrationStates.last_name, F.text)
async def registration_last_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    last_name = (message.text or "").strip()
    await state.update_data(last_name=last_name)
    schools = await list_schools(session)
    if not schools:
        await message.answer("❌ Maktablar ro'yxati bo'sh. Administrator bilan bog'laning.")
        await state.clear()
        return
    await state.set_state(RegistrationStates.school)
    total_pages = max(1, (len(schools)+9)//10)
    keyboard = build_registration_school_keyboard(schools, page=1)
    await message.answer(
        f"🏫 Maktabingizni tanlang (1/{total_pages} sahifa):",
        reply_markup=keyboard,
    )


@router.callback_query(RegistrationStates.school, lambda c: c.data.startswith("reg_school_page:"))
async def registration_school_page(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    try:
        page = int(callback.data.split(":")[1])
    except Exception:
        page = 1
    schools = await list_schools(session)
    if not schools:
        await callback.answer("❌ Maktablar ro'yxati bo'sh", show_alert=True)
        return
    total_pages = max(1, (len(schools)+9)//10)
    keyboard = build_registration_school_keyboard(schools, page=page)
    await callback.message.edit_text(
        f"🏫 Maktabingizni tanlang ({page}/{total_pages} sahifa):",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(RegistrationStates.school, lambda c: c.data.startswith("reg_school_select:"))
async def registration_school_select(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        number = int(callback.data.split(":")[1])
    except Exception:
        await callback.answer("❌ Noto'g'ri maktab", show_alert=True)
        return
    school = await get_school_by_number(session, number)
    if not school:
        await callback.answer("❌ Maktab topilmadi", show_alert=True)
        return
    await state.update_data(school_id=school.id)
    await state.set_state(RegistrationStates.phone)
    await callback.message.edit_text(
        f"🏫 Tanlangan maktab: {school.name}\n\n📱 Telefon raqamingizni yuboring:")
    await callback.message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=get_contact_keyboard(),
    )
    await callback.answer()


@router.callback_query(RegistrationStates.school, lambda c: c.data == "reg_school_cancel")
async def registration_school_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text("❌ Ro'yxatdan o'tish bekor qilindi.")
    except Exception:
        pass
    await callback.answer()


@router.message(RegistrationStates.phone, F.contact)
async def registration_phone_contact(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    contact = message.contact
    if not contact or not contact.phone_number:
        await message.answer("❌ Telefon raqam topilmadi. Qayta yuboring.")
        return
    await state.update_data(phone=contact.phone_number)
    data = await state.get_data()
    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    school_id = data.get("school_id")
    school_name = "Tanlanmagan"
    if school_id:
        school = await session.get(School, school_id)
        if school:
            school_name = school.name

    text = (
        "📋 **Ma'lumotlaringiz:**\n"
        f"👤 Ism: {first_name} {last_name}\n"
        f"🏫 Maktab: {school_name}\n"
        f"📱 Telefon: {contact.phone_number}\n\n"
        "✅ Tasdiqlaysizmi?"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="reg_confirm")
    builder.button(text="❌ Qayta kiritish", callback_data="reg_restart")
    builder.adjust(2)
    await state.set_state(RegistrationStates.confirm)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.message(RegistrationStates.phone)
async def registration_phone_text(message: Message) -> None:
    await message.answer("📱 Telefon raqamingizni yuboring (tugma orqali).")


@router.callback_query(RegistrationStates.confirm, lambda c: c.data == "reg_restart")
async def registration_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(RegistrationStates.first_name)
    await callback.message.edit_text("👤 Iltimos, ismingizni kiriting:")
    await callback.answer()


@router.callback_query(RegistrationStates.confirm, lambda c: c.data == "reg_confirm")
async def registration_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user,
) -> None:
    data = await state.get_data()
    first_name = data.get("first_name")
    last_name = data.get("last_name")
    phone = data.get("phone")
    school_id = data.get("school_id")
    reg_type = data.get("reg_type", "teacher")
    if not (first_name and phone):
        await callback.answer("❌ Ma'lumotlar to'liq emas", show_alert=True)
        return

    profile = await upsert_profile(
        session,
        user_id=db_user.id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        school_id=school_id,
        profile_type=reg_type,
    )
    await notify_superadmins_new_registration(session, callback.bot, profile)

    await state.clear()
    await callback.message.edit_text(
        "✅ Ro'yxatdan o'tish muvaffaqiyatli yakunlandi. Administrator tasdig'ini kuting.")
    await callback.answer()


@router.message(Command("start"))
async def cmd_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        profile,
        is_superadmin: bool = False,
        is_teacher: bool = False,
        is_librarian: bool = False,
        is_group_admin: bool = False,
        is_student: bool = False,
) -> None:
    """Start komandasi"""
    start_time = time.time()
    user_id = message.from_user.id if message.from_user else "-"
    chat_id = message.chat.id if message.chat else "-"
    logger.info(
        "Foydalanuvchi /start buyrug'ini yubordi",
        extra={"user_id": user_id, "chat_id": chat_id, "command": "start"},
    )
    await state.update_data(menu_active=False)
    is_authorized = bool(is_superadmin or is_teacher or is_librarian or is_group_admin)
    if is_student:
        await state.update_data(menu_active=True)
        await message.answer(
            "Xush kelibsiz! Menyudan tanlang:",
            reply_markup=get_student_keyboard(),
        )
        return
    if not is_authorized:
        # Teacher registration flow for pending teacher profiles
        if profile and (profile.profile_type or "teacher") == "teacher" and not profile.is_approved:
            if can_register_again(profile):
                await state.clear()
                await state.update_data(reg_type="teacher")
                await state.set_state(RegistrationStates.welcome)
                await message.answer(
                    "📝 Botdan foydalanish uchun ro'yxatdan o'tishingiz kerak. Ro'yxatdan o'tishni boshlaymizmi?",
                    reply_markup=get_registration_start_keyboard(),
                )
                exec_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "Foydalanuvchi qayta ro'yxatdan o'tish jarayoniga yo'naltirildi",
                    extra={"user_id": user_id, "chat_id": chat_id, "command": "start", "exec_ms": exec_ms},
                )
                return
            await message.answer(
                "⏳ Ro'yxatdan o'tishingiz tasdiqlanishi kutilmoqda. Administrator tasdig'ini kuting."
            )
            exec_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "Foydalanuvchi tasdiqlanishni kutish holatida",
                extra={"user_id": user_id, "chat_id": chat_id, "command": "start", "exec_ms": exec_ms},
            )
            return
        await state.clear()
        await state.update_data(menu_active=False, reg_type="student")
        await state.set_state(RegistrationStates.first_name)
        await message.answer(
            "Assalomu alaykum! Botdan foydalanish uchun ro'yxatdan o'tishingiz kerak.\n"
            "Ismingizni kiriting:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    await state.clear()
    await state.update_data(menu_active=True)
    lines: list[str] = [
        "📚 **Maktab topshiriqlari bot**",
        "",
        "Quyidagi tugmalardan foydalanishingiz mumkin:",
        "🏠 Bosh menyu - asosiy menyu",
        "❓ Yordam - yordam olish",
    ]
    if is_teacher and not is_superadmin:
        lines += [
            "",
            "👨‍🏫 **O'qituvchi buyruqlari:**",
            "📝 Yangi topshiriq",
            "📍 Keldim",
            "🚪 Ketdim",
            "📚 Kitob buyurtma qilish",
            "📦 Mening buyurtmalarim",
        ]
        lines.insert(lines.index("📝 Yangi topshiriq") + 1, "📊 Ovozlar")
    if is_librarian and not is_superadmin:
        lines += [
            "",
            "📚 **Kutubxona buyruqlari:**",
            "📚 Buyurtmalar ro'yxati",
            "📊 Buyurtma statistikasi",
        ]
    if is_superadmin:
        lines += [
            "",
            "👑 **Superadmin buyruqlari:**",
            "📚 KITOBLAR",
            "👥 FOYDALANUVCHILAR",
            "📊 STATISTIKA",
            "📚 GURUHLAR",
            "🕒 DAVOMAT",
        ]
    lines += [
        "",
        "📊 Oddiy foydalanuvchilar so'rovnomalarda qatnashishi mumkin.",
        "",
        "ℹ️ Tugmalarni bosish yoki komandalarni yozish orqali botdan foydalaning."
    ]
    if is_superadmin:
        builder = SuperAdminMenuBuilder()
        await state.update_data(menu_active=True)
        await message.answer(
            builder.build_dashboard_text(
                await _get_superadmin_overview(session)
            ),
            reply_markup=builder.build_main_keyboard(),
        )
        return
    keyboard = get_main_keyboard(is_superadmin, is_teacher, is_librarian)
    await state.update_data(menu_active=True)
    await message.answer("\n".join(lines), reply_markup=keyboard)
    exec_ms = int((time.time() - start_time) * 1000)
    logger.info(
        "/start buyrug'i bajarildi",
        extra={"user_id": user_id, "chat_id": chat_id, "command": "start", "exec_ms": exec_ms},
    )
@router.message(Command("help"))
async def cmd_help(
    message: Message,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
    is_student: bool = False,
) -> None:
    if is_superadmin or is_teacher or is_librarian:
        await message.answer(
            "Yordam:\n"
            "/start - menyu\n"
            "/help - yordam\n"
            "/stop - menyuni yopish",
        )
        return
    if is_student:
        await message.answer(
            "Mavjud buyruqlar:\n"
            "/start - menyu\n"
            "/help - yordam\n"
            "/stop - menyuni yopish",
        )
        return
    await message.answer(
        "Mavjud buyruqlar:\n"
        "/start - ro'yxatdan o'tish\n"
        "/help - yordam",
        reply_markup=ReplyKeyboardRemove(),
    )
@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(menu_active=False)
    await message.answer(
        "Menu yopildi. Qayta ochish uchun /start bosing.",
        reply_markup=ReplyKeyboardRemove(),
    )
@router.message(F.text == "🏠 Bosh menyu")
async def button_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        profile,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
    is_group_admin: bool = False,
    is_student: bool = False,
) -> None:
    await exit_to_main_menu(
        message,
        state,
        db_user=db_user,
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
        notice=None,
    )
@router.message(F.text == "❓ Yordam")
async def button_help(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        profile,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
    is_group_admin: bool = False,
    is_student: bool = False,
) -> None:
    await cmd_help(message, is_superadmin, is_teacher, is_librarian, is_student)
@router.message(F.text == "📦 Buyurtmalar")
async def button_admin_orders(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu bo'lim faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.superadmin_orders import admin_orders_command
    await admin_orders_command(message, session, is_superadmin)
@router.message(F.text == "📚 Kitoblar")
async def books_menu(
    message: Message,
    session: AsyncSession,
    is_student: bool = False,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if is_superadmin:
        await show_books_menu(message, is_superadmin=True)
        return
    if is_teacher:
        await message.answer("📚 **KITOBLAR**", reply_markup=get_teacher_books_keyboard())
        return
    if not is_student:
        return
    from school_bot.bot.services.book_catalog_service import list_categories
    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Hozircha kitoblar ro'yxati mavjud emas.")
        return
    lines = ["📚 Kitoblar bo'limi:", ""]
    for category in categories:
        lines.append(f"• {category.name}")
    await message.answer("\n".join(lines), reply_markup=get_student_keyboard())
@router.message(F.text == "📘 Topshiriqlar")
async def student_tasks_menu(
    message: Message,
    session: AsyncSession,
    db_user,
    profile,
    is_student: bool = False,
) -> None:
    if not is_student:
        return

    # Get the student's assigned groups (list of group names)
    assigned_groups: list[str] = (profile.assigned_groups or []) if profile else []
    if not assigned_groups:
        await message.answer(
            "Sizning guruhingiz uchun topshiriqlar yo'q.",
            reply_markup=get_student_keyboard(),
        )
        return

    # Find teacher profiles that have any overlap with student's groups
    teacher_profiles_stmt = select(Profile).where(
        Profile.profile_type == "teacher",
        Profile.is_approved == True,
    )
    teacher_profiles = (await session.execute(teacher_profiles_stmt)).scalars().all()

    # Filter to teachers whose assigned_groups overlap with student's groups
    student_groups_set = set(assigned_groups)
    matching_teacher_user_ids = [
        tp.user_id
        for tp in teacher_profiles
        if student_groups_set.intersection(set(tp.assigned_groups or []))
    ]

    if not matching_teacher_user_ids:
        await message.answer(
            "Sizning guruhingiz uchun topshiriqlar yo'q.",
            reply_markup=get_student_keyboard(),
        )
        return

    tasks_stmt = (
        select(Task)
        .where(Task.teacher_id.in_(matching_teacher_user_ids))
        .order_by(Task.created_at.desc())
        .limit(10)
    )
    tasks = (await session.execute(tasks_stmt)).scalars().all()

    if not tasks:
        await message.answer(
            "Sizning guruhingiz uchun topshiriqlar yo'q.",
            reply_markup=get_student_keyboard(),
        )
        return

    lines: list[str] = ["📘 <b>Topshiriqlar:</b>", ""]
    for task in tasks:
        desc_preview = (task.description or "")[:80]
        if len(task.description or "") > 80:
            desc_preview += "..."
        date_str = task.created_at.strftime("%d.%m.%Y")
        import html as _html
        lines.append(f"#{task.id} — <b>{_html.escape(task.topic)}</b>")
        lines.append(f"📝 {_html.escape(desc_preview)}")
        lines.append(f"🕒 {date_str}")
        lines.append("")

    from school_bot.bot.utils.telegram import send_chunked_message
    await send_chunked_message(
        message,
        "\n".join(lines).strip(),
        reply_markup=get_student_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "📊 Baholar")
async def student_grades_menu(
    message: Message,
    session: AsyncSession,
    db_user,
    is_student: bool = False,
) -> None:
    if not is_student:
        return

    votes_stmt = (
        select(PollVote)
        .where(PollVote.user_id == db_user.id)
        .order_by(PollVote.voted_at.desc())
        .limit(30)
    )
    votes = (await session.execute(votes_stmt)).scalars().all()

    if not votes:
        await message.answer(
            "Siz hali hech qanday topshiriqqa javob bermadingiz.",
            reply_markup=get_student_keyboard(),
        )
        return

    # Fetch task topics for all task IDs referenced in votes
    task_ids = list({v.task_id for v in votes if v.task_id})
    tasks_map: dict[int, Task] = {}
    if task_ids:
        tasks_stmt = select(Task).where(Task.id.in_(task_ids))
        tasks_map = {t.id: t for t in (await session.execute(tasks_stmt)).scalars().all()}

    import html as _html
    lines: list[str] = ["📊 <b>Baholar:</b>", ""]
    for vote in votes:
        task = tasks_map.get(vote.task_id) if vote.task_id else None
        topic = _html.escape(task.topic) if task else "Noma'lum topshiriq"
        option_text = _html.escape(vote.option_text or "")
        date_str = vote.voted_at.strftime("%d.%m.%Y") if vote.voted_at else "—"
        lines.append(f"📋 {topic} — {option_text} ({date_str})")

    from school_bot.bot.utils.telegram import send_chunked_message
    await send_chunked_message(
        message,
        "\n".join(lines),
        reply_markup=get_student_keyboard(),
        parse_mode="HTML",
    )
@router.message(F.text == "📝 Yangi topshiriq")
async def button_new_task(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
    db_user,
    profile,
    is_teacher: bool = False,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /new_task buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "new_task"},
    )
    if not (is_teacher or is_superadmin):
        await message.answer("⛔ Bu tugma faqat o'qituvchilar uchun.")
        return
    from school_bot.bot.handlers.teacher import cmd_new_task
    await cmd_new_task(message, state, session, profile, is_teacher or is_superadmin, is_superadmin)
@router.message(F.text == "📊 Ovozlar")
async def button_poll_voters(
        message: Message,
        session: AsyncSession,
        state: FSMContext,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False
) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📊 **OVOZLAR (GRADES)**", reply_markup=get_teacher_votes_keyboard())
@router.message(F.text == "👥 O'quvchilar")
async def teacher_students_menu(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("👥 **O'QUVCHILAR**", reply_markup=get_teacher_students_keyboard())
@router.message(F.text == "📈 Statistika")
async def teacher_stats_menu(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📈 **STATISTIKA**", reply_markup=get_teacher_stats_keyboard())
@router.message(F.text == "⚙️ Sozlamalar")
async def teacher_settings_menu(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    if is_superadmin:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="➕ Maktab qo'shish")],
                [KeyboardButton(text="❌ Maktab o'chirish")],
                [KeyboardButton(text="🔔 Bildirishnomalar")],
                [KeyboardButton(text="🔒 Maxfiylik")],
                [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
            ],
            resize_keyboard=True,
            input_field_placeholder="👇 Menyudan tanlang...",
        )
        await message.answer("⚙️ **SOZLAMALAR**", reply_markup=keyboard)
        return
    await message.answer("⚙️ **SOZLAMALAR**", reply_markup=get_teacher_settings_keyboard())
@router.message(F.text == "📊 Barcha ovozlar")
async def button_all_polls(
        message: Message,
        session: AsyncSession,
        state: FSMContext,
    db_user,
    is_superadmin: bool = False,
) -> None:
    logger.info(
        "Foydalanuvchi /all_polls buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "all_polls"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_all_polls
    await cmd_all_polls(message, session, state, is_superadmin)
@router.message(F.text == "📤 Eksport")
async def teacher_export(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📤 Eksport funksiyasi tez orada qo'shiladi.")
@router.message(F.text == "📤 Yuklash")
async def teacher_upload_book(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📤 Kitob yuklash tez orada ishga tushadi.")
@router.message(F.text == "📋 Barcha kitoblar")
async def teacher_list_books(message: Message, session: AsyncSession, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    from school_bot.bot.services.book_catalog_service import list_categories
    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Kitoblar ro'yxati mavjud emas.")
        return
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"teacher_books_cat:{category.id}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
    await message.answer("📚 Kategoriyani tanlang:", reply_markup=builder.as_markup())
@router.callback_query(lambda c: c.data.startswith("teacher_books_cat:"))
async def teacher_books_category_select(
    callback: CallbackQuery,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        category_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("❌ Noto'g'ri tanlov", show_alert=True)
        return
    from school_bot.bot.services.book_catalog_service import get_category_by_id, list_books_by_category
    category = await get_category_by_id(session, category_id)
    if not category:
        await callback.answer("❌ Kategoriya topilmadi", show_alert=True)
        return
    books = await list_books_by_category(session, category_id)
    if not books:
        text = f"📭 {category.name} kategoriyasida kitob yo'q."
    else:
        lines = [f"📚 {category.name} kategoriyasidagi kitoblar ({len(books)} ta):", ""]
        for i, book in enumerate(books, 1):
            author = f" — {book.author}" if getattr(book, 'author', None) else ""
            lines.append(f"{i}. {book.title}{author}")
        text = "\n".join(lines)
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="teacher_books_back"))
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()
@router.callback_query(lambda c: c.data == "teacher_books_back")
async def teacher_books_back(
    callback: CallbackQuery,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    from school_bot.bot.services.book_catalog_service import list_categories
    categories = await list_categories(session)
    if not categories:
        await callback.answer("📭 Kategoriyalar yo'q.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"teacher_books_cat:{category.id}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
    await callback.message.edit_text("📚 Kategoriyani tanlang:", reply_markup=builder.as_markup())
    await callback.answer()
@router.message(F.text == "🔍 Qidirish")
async def teacher_search_books(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("🔍 Qidirish funksiyasi tez orada qo'shiladi.")
@router.message(F.text == "📂 Kategoriyalar")
async def teacher_book_categories(message: Message, session: AsyncSession, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    from school_bot.bot.services.book_catalog_service import list_categories
    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Kategoriyalar ro'yxati mavjud emas.")
        return
    lines = ["📂 Kategoriyalar:", ""]
    for category in categories:
        lines.append(f"• {category.name}")
    await message.answer("\n".join(lines))
@router.message(F.text == "➕ Yangi o'quvchi")
async def teacher_add_student(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("➕ O'quvchi qo'shish funksiyasi tez orada qo'shiladi.")
@router.message(F.text == "📋 Ro'yxat")
async def teacher_student_list(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📋 O'quvchilar ro'yxati tez orada qo'shiladi.")
@router.message(F.text == "📊 Davomat")
async def teacher_attendance(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📊 Davomat bo'limi tez orada ishga tushadi.")
@router.message(F.text == "📧 Xabar yuborish")
async def teacher_send_message(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📧 Xabar yuborish funksiyasi tez orada qo'shiladi.")
@router.message(F.text == "👥 Faol o'quvchilar")
async def teacher_active_students(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("👥 Faol o'quvchilar statistikasi tez orada qo'shiladi.")
@router.message(F.text == "📝 Topshiriqlar")
async def teacher_task_stats(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📝 Topshiriqlar statistikasi tez orada qo'shiladi.")
@router.message(F.text == "📚 Kitoblar (stat)")
async def teacher_book_stats(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📚 Kitoblar statistikasi tez orada qo'shiladi.")
@router.message(F.text == "📊 Umumiy hisobot")
async def teacher_general_report(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📊 Umumiy hisobot tez orada qo'shiladi.")
@router.message(F.text == "🔔 Bildirishnomalar")
async def teacher_notifications_settings(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("🔔 Bildirishnomalar sozlamalari tez orada qo'shiladi.")
@router.message(F.text == "🔒 Maxfiylik")
async def teacher_privacy_settings(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("🔒 Maxfiylik sozlamalari tez orada qo'shiladi.")
@router.message(F.text == "➕ Maktab qo'shish")
async def button_add_school(
        message: Message,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    await state.set_state("add_school_waiting")
    await message.answer("🏫 Maktab nomini kiriting (raqam bo'lsa yaxshi, masalan: 12-maktab, 7-A yoki A'lochi math):")
@router.message(F.text == "❌ Maktab o'chirish")
async def button_remove_school(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    from school_bot.bot.services.school_service import list_schools
    schools = await list_schools(session)
    if not schools:
        await message.answer("📭 Maktablar ro'yxati bo'sh.")
        return
    builder = InlineKeyboardBuilder()
    for school in schools:
        builder.button(text=f"{school.number}-m", callback_data=f"del_school:{school.id}")
    builder.adjust(5)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
    await message.answer("❌ O'chirmoqchi bo'lgan maktabni tanlang:", reply_markup=builder.as_markup())

@router.message(StateFilter("add_school_waiting"))
async def add_school_input(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("❌ Maktab nomi bo'sh bo'lmasligi kerak. Qayta kiriting.")
        return

    # Extract number from input (e.g. "12-maktab", "7-A"). If no number, auto-assign.
    import re as _re
    m = _re.search(r"(\d+)", raw)
    number = int(m.group(1)) if m else None

    from school_bot.bot.services.school_service import add_school, get_school_by_number
    if number is not None:
        existing = await get_school_by_number(session, number)
        if existing:
            await message.answer(f"⚠️ {number}-maktab allaqachon mavjud. Raqamsiz nom yuboring yoki boshqa raqam kiriting.")
            await state.clear()
            return
    else:
        # auto-assign next number
        max_number = await session.scalar(select(func.max(School.number))) or 0
        number = max_number + 1

    school = await add_school(session, number, name=raw)
    await state.clear()
    await message.answer(f"✅ Maktab qo'shildi: {school.name}")


@router.callback_query(lambda c: c.data.startswith("del_school:"))
async def confirm_delete_school(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        school_id = int(callback.data.split(":")[1])
    except Exception:
        await callback.answer("❌ Noto'g'ri so'rov", show_alert=True)
        return
    school = await session.get(School, school_id)
    if not school:
        await callback.answer("❌ Maktab topilmadi", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, o'chirish", callback_data=f"del_school_confirm:{school.id}")
    builder.button(text="❌ Yo'q", callback_data="cancel")
    builder.adjust(2)
    await callback.message.edit_text(
        f"❌ Maktabni o'chirish\n\n🏫 {school.number}-maktab\n\nO'chirishni tasdiqlaysizmi?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
@router.callback_query(lambda c: c.data.startswith("del_school_confirm:"))
async def delete_school(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        school_id = int(callback.data.split(":")[1])
    except Exception:
        await callback.answer("❌ Noto'g'ri so'rov", show_alert=True)
        return
    from school_bot.bot.services.school_service import remove_school
    ok = await remove_school(session, school_id)
    if not ok:
        await callback.answer("❌ Maktab topilmadi", show_alert=True)
        return
    await callback.message.edit_text("✅ Maktab o'chirildi")
    await callback.answer()

@router.message(F.text == "🏫 Maktablar")
async def show_schools_menu(
        message: Message,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Maktab qo'shish")],
            [KeyboardButton(text="❌ Maktab o'chirish")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )
    await _send_menu(message, "🏫 **MAKTABLAR BO'LIMI**", reply_markup=keyboard)


@router.message(F.text == "📚 KITOBLAR")
async def show_books_menu(
        message: Message,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Kitob kategoriyalari")],
            [KeyboardButton(text="📖 Kitoblar ro'yxati")],
            [KeyboardButton(text="➕ Kategoriya qo'shish")],
            [KeyboardButton(text="➕ Kitob qo'shish")],
            [KeyboardButton(text="📦 Buyurtmalar")],
            [KeyboardButton(text="📚 Kitob buyurtma qilish")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )
    await _send_menu(message, "📚 **KITOBLAR BO'LIMI**", reply_markup=keyboard)
@router.message(F.text == "📦 Buyurtmalar")
async def button_admin_orders_from_books(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu bo'lim faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.superadmin_orders import admin_orders_command
    await admin_orders_command(message, session, is_superadmin)
@router.message(F.text == "👥 Foydalanuvchilar")
async def admin_users_menu_alias(
    message: Message,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    await show_users_menu(message, is_superadmin=is_superadmin)
@router.message(F.text == "👨‍🏫 O'qituvchilar")
async def admin_teachers_menu_alias(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    from school_bot.bot.handlers.admin import cmd_list_teachers
    await cmd_list_teachers(message, session, is_superadmin=is_superadmin)
@router.message(F.text == "👑 Adminlar")
async def admin_admins_menu_alias(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    from school_bot.bot.handlers.admin_management import cmd_list_admins
    await cmd_list_admins(message, session, is_superadmin=is_superadmin)
@router.message(F.text == "📊 Umumiy statistika")
async def admin_stats_menu_alias(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    await show_stats_menu(message, is_superadmin=True)
@router.message(F.text == "📊 Statistika")
async def admin_stats_menu_alias_short(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    await show_stats_menu(message, is_superadmin=True)
@router.message(F.text == "📢 Xabarnoma")
async def admin_broadcast(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    await message.answer("📢 Xabarnoma yuborish bo'limi tez orada qo'shiladi.")
@router.message(F.text == "📥 Backup")
async def admin_backup(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    loading = await message.answer("⏳ Backup tayyorlanmoqda...")
    try:
        settings = Settings()
        url = make_url(settings.database_url)
        db_name = url.database
        db_user = url.username or ""
        db_password = url.password or ""
        db_host = url.host or "localhost"
        db_port = str(url.port or 5432)
        if not shutil.which("pg_dump"):
            await loading.edit_text("❌ pg_dump topilmadi. Serverda postgresql-client o'rnatilmagan.")
            return
        project_root = Path(__file__).resolve().parents[3]
        backup_dir = project_root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_dir = backup_dir / f"tmp_{timestamp}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        db_dump_file = tmp_dir / f"db_{db_name}_{timestamp}.sql"
        env = os.environ.copy()
        if db_password:
            env["PGPASSWORD"] = db_password
        proc = await asyncio.create_subprocess_exec(
            "pg_dump",
            "-h", db_host,
            "-p", db_port,
            "-U", db_user,
            "-d", db_name,
            "-f", str(db_dump_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="ignore")
            await loading.edit_text(f"❌ Backup xatosi: {err[:400]}")
            return
        include_dirs = []
        photos_dir = project_root / "photos"
        covers_dir = project_root / "covers"
        if photos_dir.exists():
            include_dirs.append((photos_dir, "photos"))
        if covers_dir.exists():
            include_dirs.append((covers_dir, "covers"))
        files_root = tmp_dir / "files"
        files_root.mkdir(parents=True, exist_ok=True)
        for src, name in include_dirs:
            dest = files_root / name
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest)
        archive_name = f"backup_{db_name}_{timestamp}.tar.gz"
        archive_path = backup_dir / archive_name
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(db_dump_file, arcname=db_dump_file.name)
            if include_dirs:
                tar.add(files_root, arcname="files")
        await message.answer_document(
            FSInputFile(str(archive_path)),
            caption=(
                f"✅ Backup tayyor: {archive_name}\n"
                f"• DB: {db_dump_file.name}\n"
                f"• Photos: {'ha' if photos_dir.exists() else 'yoq'}\n"
                f"• Covers: {'ha' if covers_dir.exists() else 'yoq'}"
            ),
        )
        await loading.delete()
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as exc:
        logger.exception("Backup failed: %s", exc)
        try:
            await loading.edit_text("❌ Backup yaratib bo'lmadi.")
        except Exception:
            pass
@router.message(F.text == "📋 Loglar")
async def admin_logs(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    from school_bot.bot.handlers.logs import send_logs_menu
    await send_logs_menu(message)
@router.message(F.text == "👥 FOYDALANUVCHILAR")
async def show_users_menu(
        message: Message,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    await _send_menu(message, "Menyudan tanlang...", reply_markup=get_users_management_keyboard())
@router.message(F.text == "👤 Oddiy foydalanuvchilar")
async def button_list_regular_users(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu bo'lim faqat superadminlar uchun.")
        return
    result = await session.execute(
        select(User)
        .where((User.role == UserRole.student) | (User.role.is_(None)))
        .order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    if not users:
        await message.answer("📭 Oddiy foydalanuvchilar yo'q.")
        return
    lines = ["👤 **Oddiy foydalanuvchilar**", ""]
    for user in users:
        name = user.full_name or f"ID: {user.telegram_id}"
        username = f" (@{user.username})" if user.username else ""
        created = user.created_at.strftime('%d.%m.%Y') if user.created_at else ""
        lines.append(f"• {name}{username} {created}")
    text = "\n".join(lines)
    await send_chunked_message(message, text)
@router.message(F.text == "📊 STATISTIKA")
async def show_stats_menu(
        message: Message,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika")],
            [KeyboardButton(text="📈 Grafiklar")],
            [KeyboardButton(text="📊 Barcha ovozlar")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )
    await _send_menu(message, "📊 **STATISTIKA BO'LIMI**", reply_markup=keyboard)
@router.message(F.text == "📈 Grafiklar")
async def show_chart_menu(
        message: Message,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Eng faol o'qituvchilar")],
            [KeyboardButton(text="📈 Kunlik buyurtmalar")],
            [KeyboardButton(text="📚 Kitoblar kategoriyalar")],
            [KeyboardButton(text="📦 Buyurtma statuslari")],
            [KeyboardButton(text="👨‍🏫 O'qituvchilar fanlar")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Grafik turini tanlang...",
    )
    await _send_menu(message, "📈 **Grafiklar bo'limi**", reply_markup=keyboard)
async def _send_chart(
        message: Message,
        session: AsyncSession,
        chart_func,
        caption: str,
) -> None:
    loading = await message.answer("⏳ Grafik tayyorlanmoqda...")
    try:
        chart = await chart_func(session)
        if not chart:
            await message.answer("📭 Ma'lumot topilmadi.")
            await loading.delete()
            return
        data = chart.getvalue()
        chart.close()
        await message.answer_photo(
            BufferedInputFile(data, filename="chart.png"),
            caption=caption,
        )
        await loading.delete()
    except Exception as exc:
        logger.exception("Chart generation failed: %s", exc)
        try:
            await loading.delete()
        except Exception:
            pass
        await message.answer("❌ Grafikni yaratib bo'lmadi. Keyinroq urinib ko'ring.")
@router.message(F.text == "📊 Eng faol o'qituvchilar")
async def show_teacher_activity_chart(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.services.chart_service import create_teacher_activity_chart
    await _send_chart(
        message,
        session,
        create_teacher_activity_chart,
        "📊 **Eng faol o'qituvchilar**\nTop 10 o'qituvchi",
    )
@router.message(F.text == "📈 Kunlik buyurtmalar")
async def show_daily_orders_chart(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.services.chart_service import create_daily_orders_chart
    await _send_chart(
        message,
        session,
        create_daily_orders_chart,
        "📈 **Kunlik buyurtmalar**\nOxirgi 30 kun",
    )
@router.message(F.text == "📚 Kitoblar kategoriyalar")
async def show_books_by_category_chart(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.services.chart_service import create_books_by_category_chart
    await _send_chart(
        message,
        session,
        create_books_by_category_chart,
        "📚 **Kitoblar kategoriyalar bo'yicha**",
    )
@router.message(F.text == "📦 Buyurtma statuslari")
async def show_order_status_chart(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.services.chart_service import create_order_status_chart
    await _send_chart(
        message,
        session,
        create_order_status_chart,
        "📦 **Buyurtma statuslari**",
    )
@router.message(F.text == "👨‍🏫 O'qituvchilar fanlar")
async def show_teacher_subjects_chart(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.services.chart_service import create_teacher_subjects_chart
    await _send_chart(
        message,
        session,
        create_teacher_subjects_chart,
        "👨‍🏫 **Fanlar bo'yicha o'qituvchilar**",
    )
@router.message(F.text == "📚 GURUHLAR")
async def show_groups_menu(
        message: Message,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Guruhlar ro'yxati")],
            [KeyboardButton(text="🆔 Guruh chat idlari")],
            [KeyboardButton(text="⏳ Kutilayotgan guruhlar")],
            [KeyboardButton(text="➕ Guruh qo'shish")],
            [KeyboardButton(text="✏️ Guruh tahrirlash")],
            [KeyboardButton(text="🗑️ Guruh o'chirish")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )
    await _send_menu(message, "📚 **GURUHLAR BO'LIMI**", reply_markup=keyboard)

@router.message(F.text.in_({"📋 Guruhlar ro'yxati", "Guruhlar ro'yxati"}))
async def groups_list_button(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    from school_bot.bot.handlers.admin import cmd_groups
    await cmd_groups(message, session, is_superadmin=is_superadmin)


@router.message(F.text.in_({"🆔 Guruh chat idlari", "Guruh chat idlari"}))
async def groups_ids_button(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    from school_bot.bot.handlers.admin import cmd_groups_ids
    await cmd_groups_ids(message, session, is_superadmin=is_superadmin)


@router.message(F.text.in_({"⏳ Kutilayotgan guruhlar", "Kutilayotgan guruhlar"}))
async def groups_pending_button(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    from school_bot.bot.handlers.admin import cmd_pending_groups
    await cmd_pending_groups(message, session, is_superadmin=is_superadmin)


@router.message(F.text.in_({"➕ Guruh qo'shish", "Guruh qo'shish"}))
async def groups_add_button(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    from school_bot.bot.handlers.admin import cmd_add_group_start
    await cmd_add_group_start(message, state, session, is_superadmin=is_superadmin)


@router.message(F.text.in_({"✏️ Guruh tahrirlash", "Guruh tahrirlash"}))
async def groups_edit_button(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    from school_bot.bot.handlers.admin import cmd_edit_group_start
    await cmd_edit_group_start(message, state, session, is_superadmin=is_superadmin)


@router.message(F.text.in_({"🗑️ Guruh o'chirish", "Guruh o'chirish"}))
async def groups_remove_button(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    from school_bot.bot.handlers.admin import cmd_remove_group_start
    await cmd_remove_group_start(message, session, is_superadmin=is_superadmin)

@router.message(F.text == "🔙 Orqaga")
async def go_back_to_main(
        message: Message,
        state: FSMContext,
        is_superadmin: bool = False,
        is_teacher: bool = False,
        is_librarian: bool = False,
) -> None:
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher, is_librarian=is_librarian)
    last_id = None
    if message.from_user:
        last_id = LAST_MENU_MESSAGE.get(message.from_user.id)
    if last_id:
        try:
            await message.bot.edit_message_text(
                text="🏠 **Asosiy menyu**",
                chat_id=message.chat.id,
                message_id=last_id,
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass
    await _send_menu(message, "🏠 **Asosiy menyu**", reply_markup=keyboard)
@router.message(F.text == "📚 Kitob buyurtma")
@router.message(F.text == "📚 Kitob buyurtma qilish")
async def button_order_book(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /order_book buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "order_book"},
    )
    if not (is_teacher or is_superadmin):
        await message.answer("⛔ Bu tugma faqat o'qituvchilar va superadminlar uchun.")
        return
    from school_bot.bot.handlers.book_order_cart import cmd_order_books
    await cmd_order_books(message, state, session, is_teacher, is_superadmin)
@router.message(F.text == "📦 Mening buyurtmalarim")
async def button_my_orders(
        message: Message,
        session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /my_orders buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "my_orders"},
    )
    if not (is_teacher or is_superadmin):
        await message.answer("⛔ Bu tugma faqat o'qituvchilar uchun.")
        return
    from school_bot.bot.handlers.book_order_cart import cmd_my_orders
    await cmd_my_orders(message, session, db_user, is_teacher or is_superadmin, is_superadmin)
@router.message(F.text == "➕ O'qituvchi qo'shish")
async def button_add_teacher(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
    db_user,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /add_teacher buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_teacher"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_add_teacher_start
    await cmd_add_teacher_start(message, state, is_superadmin)
@router.message(F.text == "❌ O'qituvchi o'chirish")
async def button_remove_teacher(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
    db_user,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /remove_teacher buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "remove_teacher"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    # Teacherlar ro'yxatini olish
    result = await session.execute(
        select(User).where(User.role == UserRole.teacher).order_by(User.full_name)
    )
    teachers = result.scalars().all()
    if not teachers:
        await message.answer("📭 Hozircha hech qanday o'qituvchi yo'q.")
        return
    # Teacherlar ro'yxatini inline keyboard ko'rinishida ko'rsatish
    builder = InlineKeyboardBuilder()
    for teacher in teachers:
        if teacher.full_name:
            teacher_name = teacher.full_name
        else:
            teacher_name = f"ID: {teacher.telegram_id}"
        username = f"@{teacher.username}" if teacher.username else ""
        button_text = f"👨‍🏫 {teacher_name} {username}".strip()
        builder.button(text=button_text, callback_data=f"common_del_teacher_{teacher.id}")
    builder.adjust(1)
    # RemoveTeacherStates ni dinamik ravishda saqlash
    await state.update_data(awaiting_teacher_selection=True)
    await message.answer(
        "👨‍🏫 O'chirmoqchi bo'lgan o'qituvchingizni tanlang:",
        reply_markup=builder.as_markup()
    )
@router.callback_query(lambda c: c.data.startswith("common_del_teacher_"))
async def process_remove_teacher_selection(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    """Tanlangan o'qituvchini o'chirish"""
    teacher_id = int(callback.data.replace("common_del_teacher_", ""))
    logger.info(
        "O'qituvchini o'chirish so'rovi",
        extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "remove_teacher", "target_id": teacher_id},
    )
    # Teacherni bazadan olish
    result = await session.execute(
        select(User).where(User.id == teacher_id)
    )
    teacher = result.scalar_one_or_none()
    if not teacher:
        logger.warning(
            "O'qituvchi topilmadi",
            extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "remove_teacher", "target_id": teacher_id},
        )
        await callback.message.edit_text("❌ O'qituvchi topilmadi.")
        await callback.answer()
        return
    # Teacher ismini saqlab qolish
    if teacher.full_name:
        teacher_name = teacher.full_name
    else:
        teacher_name = f"ID: {teacher.telegram_id}"
    logger.info(
        "O'qituvchi olib tashlanmoqda",
        extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "remove_teacher", "target_name": teacher_name},
    )
    # Teacherni o'chirish (profile va role ni yangilash)
    await revoke_teacher(session, teacher.id)
    await callback.message.edit_text(
        f"✅ O'qituvchi olib tashlandi: {teacher_name}\n"
        f"📊 Endi u oddiy foydalanuvchi."
    )
    await state.clear()
    await callback.answer()
async def _get_regular_users_for_deletion(session: AsyncSession):
    result = await session.execute(
        select(User)
        .where((User.role == UserRole.student) | (User.role.is_(None)))
        .order_by(User.created_at.desc())
    )
    return list(result.scalars().all())
def _build_user_delete_list(users: list[User]):
    lines = [
        "👥 Oddiy foydalanuvchilar:",
        "",
        "O'chirmoqchi bo'lgan foydalanuvchini tanlang:",
        "",
    ]
    builder = InlineKeyboardBuilder()
    for user in users:
        display_name = user.full_name or f"ID: {user.telegram_id}"
        username = f" (@{user.username})" if user.username else ""
        role = user.role or UserRole.student
        role_label = {
            UserRole.superadmin: "superadmin",
            UserRole.teacher: "teacher",
            UserRole.librarian: "librarian",
            UserRole.student: "student",
        }.get(role, str(role))
        builder.button(
            text=f"{display_name}{username} [{role_label}]",
            callback_data=f"user_del_select:{user.id}",
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="🔙 Ortga", callback_data="user_del_back"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="user_del_cancel"),
    )
    return "\n".join(lines).strip(), builder.as_markup()
@router.message(F.text == "❌ Foydalanuvchi o'chirish")
async def button_remove_user(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    users = await _get_regular_users_for_deletion(session)
    if not users:
        await message.answer("📭 Oddiy foydalanuvchilar yo'q.")
        return
    text, keyboard = _build_user_delete_list(users)
    await send_chunked_message(message, text, reply_markup=keyboard)
@router.callback_query(lambda c: c.data in ("user_del_back", "user_del_cancel"))
async def user_delete_back_or_cancel(
    callback: CallbackQuery,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _send_menu(callback.message, "Menyudan tanlang...", reply_markup=get_users_management_keyboard())
    if callback.data == "user_del_cancel":
        await callback.answer("✅ Bekor qilindi")
    else:
        await callback.answer()
@router.callback_query(lambda c: c.data.startswith("user_del_select:"))
async def confirm_delete_user(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        user_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri foydalanuvchi", show_alert=True)
        return
    user = await session.get(User, user_id)
    if not user:
        await callback.answer("❌ Foydalanuvchi topilmadi!", show_alert=True)
        return
    display_name = user.full_name or f"ID: {user.telegram_id}"
    username = f"@{user.username}" if user.username else "username yo'q"
    created = user.created_at.strftime('%d.%m.%Y') if user.created_at else "Noma'lum"
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, o'chirish", callback_data=f"user_del_confirm:{user.id}")
    builder.button(text="❌ Yo'q", callback_data="user_del_cancel")
    builder.adjust(2)
    text_msg = (
        "❌ Foydalanuvchini o'chirish\n\n"
        f"👤 Ism: {display_name}\n"
        f"🆔 ID: {user.telegram_id}\n"
        f"🔹 Username: {username}\n"
        f"📅 Qo'shilgan: {created}\n\n"
        "Bu foydalanuvchini o'chirmoqchimisiz?"
    )
    await callback.message.edit_text(
        text_msg,
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
@router.callback_query(lambda c: c.data.startswith("user_del_confirm:"))
async def execute_delete_user(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        user_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri foydalanuvchi", show_alert=True)
        return
    user = await session.get(User, user_id)
    if not user:
        await callback.answer("❌ Foydalanuvchi topilmadi!", show_alert=True)
        return
    display_name = user.full_name or f"ID: {user.telegram_id}"
    username = f"@{user.username}" if user.username else ""
    await session.delete(user)
    await session.commit()
    await callback.message.edit_text(f"✅ Foydalanuvchi o'chirildi: {display_name} {username}".strip())
    await callback.answer()
@router.message(F.text == "👨‍🏫 O'qituvchilar ro'yxati")
async def button_list_teachers(
        message: Message,
    session: AsyncSession,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /list_teachers buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "list_teachers"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_list_teachers
    await cmd_list_teachers(message, session, is_superadmin)
@router.message(F.text == "❌ O'qituvchi o'chirish")
async def button_remove_teacher(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_remove_teacher_start
    await cmd_remove_teacher_start(message, state, session, db_user)
@router.message(F.text == "⏳ Kutilayotganlar")
async def button_pending_approvals(
        message: Message,
    session: AsyncSession,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /pending_approvals buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "pending_approvals"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_pending_approvals
    await cmd_pending_approvals(message, session, is_superadmin)
@router.message(F.text == "📊 Statistika")
async def button_stats(
        message: Message,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /stats buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "stats"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_stats
    await cmd_stats(message, session, is_superadmin)
@router.message(F.text == "📚 Kitob kategoriyalari")
async def button_list_categories(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.book_categories import cmd_list_categories
    await cmd_list_categories(message, session, is_superadmin)
@router.message(F.text == "➕ Kategoriya qo'shish")
async def button_add_category(
        message: Message,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    await state.set_state(CategoryAddStates.waiting_for_name)
    prompt = await message.answer(
        "📚 Kategoriya nomini yozing (faqat 1-sinf, 2-sinf, 3-sinf, 4-sinf).\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )
    await state.update_data(last_prompt_message_id=prompt.message_id)
@router.message(F.text == "📖 Kitoblar ro'yxati")
async def button_list_books(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.book_management import cmd_list_books_start
    await cmd_list_books_start(message, session, None, is_superadmin)
@router.message(F.text == "➕ Kitob qo'shish")
async def button_add_book(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.book_management import cmd_add_book
    await cmd_add_book(message, state, session, is_superadmin)
@router.message(F.text == "👥 Adminlar ro'yxati")
async def button_list_admins(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin_management import cmd_list_admins
    await cmd_list_admins(message, session, is_superadmin)
@router.message(F.text == "➕ Admin qo'shish")
async def button_add_admin(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin_management import cmd_add_admin
    await cmd_add_admin(message, state, session, None, is_superadmin)
@router.message(F.text == "❌ Admin o'chirish")
async def button_remove_admin(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin_management import cmd_remove_admin
    await cmd_remove_admin(message, state, session, None, is_superadmin)
