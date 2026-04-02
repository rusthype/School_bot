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
from school_bot.database.models import (
    User, UserRole, Task, School, PollVote, Profile, Book,
    BookOrder, BookOrderItem, BookCategory, TeacherAttendance, BotSettings,
)
from school_bot.bot.states.new_task import NewTaskStates
from school_bot.bot.states.registration import RegistrationStates, RoleSelectStates, PostRoleRegistrationStates
from school_bot.bot.states.book_states import CategoryAddStates
from school_bot.bot.states.dashboard_states import (
    SearchStates,
    AddStudentStates,
    BroadcastStates,
    SendMessageStates,
    PrivacySettingsStates,
)
from school_bot.bot.config import Settings
from school_bot.bot.services.profile_service import (
    upsert_profile,
    upsert_student_profile,
    can_register_again,
    revoke_teacher,
    get_profile_by_user_id,
    update_teacher_profile,
)
from school_bot.bot.states.admin_states import TeacherSelfEditStates, AddTeacherStates
from school_bot.bot.services.approval_service import notify_superadmins_new_registration
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.superadmin_menu_builder import SuperAdminMenuBuilder
from school_bot.bot.services.school_service import list_schools, get_school_by_id, get_school_by_number
from school_bot.bot.services.pagination import SchoolPagination
from school_bot.bot.utils.telegram import send_chunked_message
from school_bot.bot.services.bot_settings_service import get_or_create_settings, update_settings
from school_bot.bot.services.book_order_service import get_order_stats
router = Router(name=__name__)
logger = get_logger(__name__)
from collections import OrderedDict


class _LRUDict(OrderedDict):
    def __init__(self, maxsize=1000):
        super().__init__()
        self._maxsize = maxsize

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self._maxsize:
            self.popitem(last=False)


LAST_MENU_MESSAGE: _LRUDict = _LRUDict(maxsize=1000)
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
    builder.row(KeyboardButton(text="♻️ O'qituvchini tiklash"))
    builder.row(KeyboardButton(text="❌ Foydalanuvchi o'chirish"))
    builder.row(KeyboardButton(text="➕ Admin qo'shish"))
    builder.row(KeyboardButton(text="❌ Admin o'chirish"))
    builder.row(KeyboardButton(text="➕ O'qituvchi qo'shish"))
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
    builder.row(KeyboardButton(text="✏️ Profilni tahrirlash"))
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
    await state.update_data(menu_active=True)
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


_NAME_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЎўҚқҲҳЭэ\s'\-]+$")
_PHONE_PATTERN = re.compile(r"^\+?[0-9]{9,13}$")


def _validate_name(name: str) -> str | None:
    if len(name) < 2 or len(name) > 50:
        return "Ism 2 dan 50 gacha belgi bo'lishi kerak."
    if not _NAME_PATTERN.match(name):
        return "Ismda faqat harflar, probel, apostrof va tire ishlatish mumkin."
    return None


@router.message(RegistrationStates.first_name, F.text)
async def registration_first_name(
    message: Message,
    state: FSMContext,
) -> None:
    first_name = (message.text or "").strip()
    if not first_name:
        await message.answer("❌ Ism bo'sh bo'lmasligi kerak. Qayta kiriting:")
        return
    error = _validate_name(first_name)
    if error:
        await message.answer(f"❌ {error} Qayta kiriting:")
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
    if last_name:
        error = _validate_name(last_name)
        if error:
            await message.answer(f"❌ {error} Qayta kiriting:")
            return
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
    await state.update_data(school_id=str(school.id))
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
    raw_school_id = data.get("school_id")
    school_id = int(raw_school_id) if raw_school_id else None
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
    raw_school_id = data.get("school_id")
    school_id = int(raw_school_id) if raw_school_id else None
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
        # Himoya: agar DB dagi rol o'qituvchi, kutubxonachi yoki superadmin bo'lsa,
        # middleware xatoligi yuzaga kelsa ham (masalan, tasdiqlanmagan profil bilan
        # qayta faollashtirilgan foydalanuvchi), rolni hech qachon o'zgartirmaymiz.
        # Bu holda to'g'ri menyuni ko'rsatamiz.
        if db_user.role == UserRole.teacher:
            logger.warning(
                "cmd_start: db_user.role=teacher lekin is_teacher=False — middleware xatoligi. "
                "Foydalanuvchiga teacher menyusi ko'rsatilmoqda, rol o'zgartirilmaydi.",
                extra={"user_id": user_id},
            )
            is_teacher = True
            is_authorized = True
        elif db_user.role == UserRole.librarian:
            logger.warning(
                "cmd_start: db_user.role=librarian lekin is_librarian=False — middleware xatoligi. "
                "Foydalanuvchiga librarian menyusi ko'rsatilmoqda, rol o'zgartirilmaydi.",
                extra={"user_id": user_id},
            )
            is_librarian = True
            is_authorized = True

    if not is_authorized:
        # Teacher registration flow for pending teacher profiles
        if profile and not profile.is_approved:
            await state.clear()
            await state.set_state(RoleSelectStates.waiting_role)
            role_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="👨\u200d🏫 O'qituvchi", callback_data="role_select:teacher"),
                    InlineKeyboardButton(text="👨\u200d👩\u200d👧 Ota-ona", callback_data="role_select:parent"),
                ],
                [
                    InlineKeyboardButton(text="🎓 O'quvchi", callback_data="role_select:student"),
                ],
            ])
            await message.answer(
                "Assalomu alaykum! Botga xush kelibsiz.\n\nIltimos, o'zingizning rolingizni tanlang:",
                reply_markup=role_keyboard,
            )
            return
        await state.clear()
        await state.set_state(RoleSelectStates.waiting_role)
        role_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="👨‍🏫 O'qituvchi", callback_data="role_select:teacher"),
                InlineKeyboardButton(text="👨‍👩‍👧 Ota-ona", callback_data="role_select:parent"),
            ],
            [
                InlineKeyboardButton(text="🎓 O'quvchi", callback_data="role_select:student"),
            ],
        ])
        await message.answer(
            "Assalomu alaykum! Botga xush kelibsiz.\n\nIltimos, o'zingizning rolingizni tanlang:",
            reply_markup=role_keyboard,
        )
        return
    # Agar foydalanuvchi roli bor lekin profili tasdiqlanmagan bo'lsa (rad etilgan),
    # menyuga o'tkazmasdan rol tanlash keyboard ko'rsatiladi.
    # Bu holat Fix 1 bilan birga ishlaydi: reject_profile endi rolni o'zgartirmaydi,
    # shuning uchun middleware is_teacher=True qilib qo'yadi, lekin profil hali
    # tasdiqlanmagan — foydalanuvchi qayta ro'yxatdan o'tishi shart.
    if not is_superadmin and not is_student and profile is not None and not profile.is_approved:
        await state.clear()
        await state.set_state(RoleSelectStates.waiting_role)
        role_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="👨‍🏫 O'qituvchi", callback_data="role_select:teacher"),
                InlineKeyboardButton(text="👨‍👩‍👧 Ota-ona", callback_data="role_select:parent"),
            ],
            [
                InlineKeyboardButton(text="🎓 O'quvchi", callback_data="role_select:student"),
            ],
        ])
        await message.answer(
            "Assalomu alaykum! Botga xush kelibsiz.\n\nIltimos, o'zingizning rolingizni tanlang:",
            reply_markup=role_keyboard,
        )
        exec_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Rad etilgan foydalanuvchi rol tanlash sahifasiga yo'naltirildi",
            extra={"user_id": user_id, "chat_id": chat_id, "command": "start", "exec_ms": exec_ms},
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


_VALID_ROLES = {"teacher", "parent", "student"}
_ROLE_LABELS = {
    "teacher": "O'qituvchi",
    "parent": "Ota-ona",
    "student": "O'quvchi",
}


@router.callback_query(lambda c: c.data and c.data.startswith("role_select:"))
async def handle_role_selection(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        db_user,
) -> None:
    """Yangi foydalanuvchi rol tanladi — ismini so'rash uchun ro'yxatdan o'tish oqimini boshlaydi."""
    role = callback.data.split(":", 1)[1] if callback.data else ""
    if role not in _VALID_ROLES:
        await callback.answer("Noto'g'ri tanlov. Iltimos, tugmadan foydalaning.", show_alert=True)
        return

    # Save role on db_user (parent maps to None role — profile_type carries it)
    if role == "teacher":
        db_user.role = UserRole.teacher
    elif role == "student":
        db_user.role = UserRole.student
    else:
        # parent: no UserRole enum value — leave role as-is, use profile_type
        pass

    await session.commit()

    # Store role in FSM so subsequent steps can use it
    await state.set_data({"post_role_type": role})
    await state.set_state(PostRoleRegistrationStates.waiting_name)

    role_label = _ROLE_LABELS[role]
    await callback.message.edit_text(
        f"Siz {role_label} sifatida ro'yxatdan o'tyapsiz.\n\n"
        "Ismingizni kiriting (to'liq ism):"
    )
    await callback.answer()
    logger.info(
        "Yangi foydalanuvchi rol tanladi, ism kutilmoqda",
        extra={"user_id": db_user.telegram_id, "role": role},
    )


_NAME_RE = re.compile(r"^[\w\s'\-]{2,80}$", re.UNICODE)


@router.message(PostRoleRegistrationStates.waiting_name, Command("cancel"))
@router.message(PostRoleRegistrationStates.waiting_name, F.text == "❌ Bekor qilish")
async def post_role_name_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Ro'yxatdan o'tish bekor qilindi.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(PostRoleRegistrationStates.waiting_name, F.text)
async def post_role_name_handler(
    message: Message,
    state: FSMContext,
) -> None:
    """Step 1: collect and validate the user's full name."""
    name = (message.text or "").strip()
    if not _NAME_RE.match(name):
        await message.answer(
            "Ism faqat harflar, bo'shliq, apostrof yoki tire bo'lishi mumkin. "
            "Qayta kiriting:",
            reply_markup=get_cancel_keyboard(),
        )
        return

    await state.update_data(post_role_name=name)
    await state.set_state(PostRoleRegistrationStates.waiting_school)
    await message.answer(
        "Maktabingizni kiriting (masalan: Qo'qon 7-maktab):",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(PostRoleRegistrationStates.waiting_school, Command("cancel"))
@router.message(PostRoleRegistrationStates.waiting_school, F.text == "❌ Bekor qilish")
async def post_role_school_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Ro'yxatdan o'tish bekor qilindi.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(PostRoleRegistrationStates.waiting_school, F.text)
async def post_role_school_handler(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
) -> None:
    """Step 2: collect school text, save profile, notify superadmins."""
    school_text = (message.text or "").strip()
    if not school_text:
        await message.answer(
            "Maktab nomini kiriting:",
            reply_markup=get_cancel_keyboard(),
        )
        return

    data = await state.get_data()
    full_name = data.get("post_role_name", db_user.full_name or "")
    role = data.get("post_role_type", "student")

    # upsert_profile requires phone; use empty string for pending approvals
    # Store school free-text in last_name column (temporary) so superadmins see it
    profile = await upsert_profile(
        session,
        user_id=db_user.id,
        first_name=full_name,
        last_name=school_text,
        phone="",
        school_id=None,
        profile_type=role,
    )

    await state.clear()

    await message.answer(
        "Ma'lumotlaringiz qabul qilindi. Admin tasdiqlashini kuting.",
        reply_markup=ReplyKeyboardRemove(),
    )

    try:
        await notify_superadmins_new_registration(session, message.bot, profile)
    except Exception:
        logger.error(
            "Superadminlarga ro'yxatdan o'tish xabari yuborilmadi",
            exc_info=True,
            extra={"user_id": db_user.telegram_id, "command": "post_role_notify"},
        )

    logger.info(
        "Yangi foydalanuvchi ro'yxatdan o'tdi, tasdiqlash kutilmoqda",
        extra={"user_id": db_user.telegram_id, "role": role, "school": school_text},
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
    from school_bot.bot.services.book_service import list_categories
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
async def teacher_export(
    message: Message,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    from school_bot.bot.services.attendance_service import tashkent_today
    today = tashkent_today()
    result = await session.execute(
        select(TeacherAttendance, User)
        .join(User, User.id == TeacherAttendance.teacher_id)
        .where(TeacherAttendance.attendance_date == today)
        .order_by(TeacherAttendance.created_at)
    )
    rows = result.all()
    if not rows:
        await message.answer("Bugun davomat ma'lumotlari yo'q")
        return
    import io
    buf = io.StringIO()
    buf.write("teacher_name,action,timestamp,distance_m,is_inside\n")
    for attendance, user in rows:
        name = (user.full_name or str(user.telegram_id)).replace(",", " ")
        ts = attendance.created_at.strftime("%Y-%m-%d %H:%M:%S") if attendance.created_at else ""
        buf.write(f"{name},{attendance.action},{ts},{attendance.distance_m},{attendance.is_inside}\n")
    buf.seek(0)
    doc = BufferedInputFile(buf.getvalue().encode("utf-8"), filename=f"davomat_{today}.csv")
    await message.answer_document(doc, caption=f"Bugungi davomat: {len(rows)} ta yozuv")
@router.message(F.text == "📤 Yuklash")
async def teacher_upload_book(
    message: Message,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    from sqlalchemy.orm import selectinload
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(BookOrder)
        .where(BookOrder.created_at >= month_start)
        .options(
            selectinload(BookOrder.items).selectinload(BookOrderItem.book),
            selectinload(BookOrder.teacher),
        )
        .order_by(BookOrder.created_at.desc())
    )
    orders = result.scalars().all()
    if not orders:
        await message.answer("Bu oy buyurtmalar yo'q")
        return
    import io
    buf = io.StringIO()
    buf.write("order_id,teacher_name,book_title,quantity,status,created_at\n")
    for order in orders:
        teacher_name = (order.teacher.full_name or str(order.teacher.telegram_id)).replace(",", " ") if order.teacher else "Noma'lum"
        for item in order.items:
            title = (item.book.title if item.book else f"ID:{item.book_id}").replace(",", " ")
            ts = order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else ""
            buf.write(f"{order.id},{teacher_name},{title},{item.quantity},{order.status},{ts}\n")
    buf.seek(0)
    month_str = now.strftime("%Y_%m")
    doc = BufferedInputFile(buf.getvalue().encode("utf-8"), filename=f"buyurtmalar_{month_str}.csv")
    await message.answer_document(doc, caption=f"Bu oy buyurtmalar: {len(orders)} ta")
@router.message(F.text == "📋 Barcha kitoblar")
async def teacher_list_books(message: Message, session: AsyncSession, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    from school_bot.bot.services.book_service import list_categories
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
    from school_bot.bot.services.book_service import get_category_by_id, list_books_by_category
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
    from school_bot.bot.services.book_service import list_categories
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
async def teacher_search_books(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    await state.set_state(SearchStates.waiting_for_query)
    await message.answer(
        "🔍 Qidiruv so'zini kiriting (ism yoki telefon):",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(SearchStates.waiting_for_query, F.text == "❌ Bekor qilish")
async def search_cancel(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    await message.answer("Qidiruv bekor qilindi.", reply_markup=keyboard)


@router.message(SearchStates.waiting_for_query, F.text)
async def search_query_handler(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    query = (message.text or "").strip()
    if not query or len(query) < 2:
        await message.answer("Kamida 2 ta belgi kiriting:")
        return
    like_q = f"%{query}%"
    result = await session.execute(
        select(User, Profile)
        .outerjoin(Profile, User.id == Profile.bot_user_id)
        .where(
            (User.full_name.ilike(like_q)) | (Profile.phone.ilike(like_q))
        )
        .order_by(User.full_name)
        .limit(10)
    )
    rows = result.all()
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    if not rows:
        await message.answer("Hech narsa topilmadi.", reply_markup=keyboard)
        return
    lines = [f"🔍 Qidiruv natijalari ({len(rows)} ta):", ""]
    for user, profile in rows:
        name = user.full_name or f"ID: {user.telegram_id}"
        role = user.role.value if user.role else "foydalanuvchi"
        school_info = ""
        if profile and profile.school_id:
            school = await session.get(School, profile.school_id)
            if school:
                school_info = f" | {school.name}"
        lines.append(f"- {name} ({role}){school_info}")
    await message.answer("\n".join(lines), reply_markup=keyboard)
@router.message(F.text == "📂 Kategoriyalar")
async def teacher_book_categories(message: Message, session: AsyncSession, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    from school_bot.bot.services.book_service import list_categories
    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Kategoriyalar ro'yxati mavjud emas.")
        return
    lines = ["📂 Kategoriyalar:", ""]
    for category in categories:
        lines.append(f"• {category.name}")
    await message.answer("\n".join(lines))
@router.message(F.text == "➕ Yangi o'quvchi")
async def teacher_add_student(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    await state.set_state(AddStudentStates.first_name)
    await message.answer(
        "👤 O'quvchining ismini kiriting:",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(AddStudentStates.first_name, F.text == "❌ Bekor qilish")
@router.message(AddStudentStates.last_name, F.text == "❌ Bekor qilish")
@router.message(AddStudentStates.phone, F.text == "❌ Bekor qilish")
async def add_student_cancel(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    await message.answer("O'quvchi qo'shish bekor qilindi.", reply_markup=keyboard)


@router.message(AddStudentStates.first_name, F.text)
async def add_student_first_name(message: Message, state: FSMContext) -> None:
    first_name = (message.text or "").strip()
    if not first_name:
        await message.answer("Ism bo'sh bo'lmasligi kerak. Qayta kiriting:")
        return
    error = _validate_name(first_name)
    if error:
        await message.answer(f"❌ {error} Qayta kiriting:")
        return
    await state.update_data(student_first_name=first_name)
    await state.set_state(AddStudentStates.last_name)
    await message.answer("👤 Familiyani kiriting:", reply_markup=get_cancel_keyboard())


@router.message(AddStudentStates.last_name, F.text)
async def add_student_last_name(message: Message, state: FSMContext) -> None:
    last_name = (message.text or "").strip()
    if last_name:
        error = _validate_name(last_name)
        if error:
            await message.answer(f"❌ {error} Qayta kiriting:")
            return
    await state.update_data(student_last_name=last_name)
    await state.set_state(AddStudentStates.phone)
    await message.answer("📱 Telefon raqamini kiriting (+998...):", reply_markup=get_cancel_keyboard())


@router.message(AddStudentStates.phone, F.text)
async def add_student_phone(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    phone = (message.text or "").strip()
    if not _PHONE_PATTERN.match(phone):
        await message.answer("❌ Telefon raqami noto'g'ri (9-13 raqam, boshida + bo'lishi mumkin). Qayta kiriting:")
        return
    await state.update_data(student_phone=phone)
    schools = await list_schools(session)
    if not schools:
        await message.answer("Maktablar topilmadi. Administrator bilan bog'laning.")
        await state.clear()
        return
    await state.set_state(AddStudentStates.school_selection)
    keyboard = build_registration_school_keyboard(schools, page=1)
    await message.answer("🏫 Maktabni tanlang:", reply_markup=keyboard)


@router.callback_query(AddStudentStates.school_selection, lambda c: c.data.startswith("reg_school_select:"))
async def add_student_school_select(
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
    await state.update_data(student_school_id=str(school.id))
    await state.set_state(AddStudentStates.class_group)
    await callback.message.edit_text(
        f"Maktab: {school.name}\n\nSinf nomini kiriting (masalan: 3-A):"
    )
    await callback.answer()


@router.callback_query(AddStudentStates.school_selection, lambda c: c.data.startswith("reg_school_page:"))
async def add_student_school_page(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    try:
        page = int(callback.data.split(":")[1])
    except Exception:
        page = 1
    schools = await list_schools(session)
    if not schools:
        await callback.answer("Maktablar topilmadi", show_alert=True)
        return
    keyboard = build_registration_school_keyboard(schools, page=page)
    total_pages = max(1, (len(schools) + 9) // 10)
    await callback.message.edit_text(
        f"🏫 Maktabni tanlang ({page}/{total_pages} sahifa):",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(AddStudentStates.class_group, F.text)
async def add_student_class_group(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    class_name = (message.text or "").strip()
    if not class_name:
        await message.answer("Sinf nomi bo'sh bo'lmasligi kerak. Qayta kiriting:")
        return
    data = await state.get_data()
    first_name = data.get("student_first_name", "")
    last_name = data.get("student_last_name", "")
    phone = data.get("student_phone", "")
    raw_school_id = data.get("student_school_id")
    school_id = int(raw_school_id) if raw_school_id else None
    # Create a placeholder user for the student (no telegram_id yet)
    # We use upsert_student_profile approach but need a user row
    # Since this is a manual add, we create user with telegram_id=0 + random offset
    import random
    placeholder_tg_id = -(random.randint(100000, 9999999))
    from school_bot.bot.services.user_service import get_or_create_user
    student_user = await get_or_create_user(session, telegram_id=placeholder_tg_id, full_name=f"{first_name} {last_name}".strip())
    student_user.role = UserRole.student
    await session.commit()
    await upsert_student_profile(
        session,
        user_id=student_user.id,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        class_name=class_name,
        school_id=school_id,
    )
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    await message.answer(
        f"✅ O'quvchi qo'shildi!\n\n"
        f"👤 {first_name} {last_name}\n"
        f"📱 {phone}\n"
        f"📚 Sinf: {class_name}",
        reply_markup=keyboard,
    )
@router.message(F.text == "📋 Ro'yxat")
async def teacher_student_list(
    message: Message,
    session: AsyncSession,
    db_user,
    profile,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    result = await session.execute(
        select(User, Profile)
        .join(Profile, User.id == Profile.bot_user_id)
        .where(Profile.profile_type == "student")
        .order_by(Profile.first_name)
        .limit(50)
    )
    rows = result.all()
    if not rows:
        await message.answer("📭 O'quvchilar topilmadi.", reply_markup=get_teacher_students_keyboard())
        return
    lines = [f"📋 O'quvchilar ({len(rows)} ta):", ""]
    for user, prof in rows:
        name = f"{prof.first_name} {prof.last_name or ''}".strip()
        groups = ", ".join(prof.assigned_groups) if prof.assigned_groups else "-"
        lines.append(f"- {name} | {groups}")
    await send_chunked_message(message, "\n".join(lines), reply_markup=get_teacher_students_keyboard())
@router.message(F.text == "📊 Davomat")
async def teacher_attendance(
    message: Message,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    from school_bot.bot.services.attendance_service import tashkent_today
    today = tashkent_today()
    result = await session.execute(
        select(TeacherAttendance, User)
        .join(User, User.id == TeacherAttendance.teacher_id)
        .where(TeacherAttendance.attendance_date == today)
        .order_by(TeacherAttendance.created_at)
    )
    rows = result.all()
    if not rows:
        await message.answer("Bugun davomat ma'lumotlari yo'q.", reply_markup=get_teacher_students_keyboard())
        return
    lines = [f"📊 Bugungi davomat ({len(rows)} ta):", ""]
    for att, user in rows:
        name = user.full_name or str(user.telegram_id)
        status = "maktabda" if att.is_inside else f"tashqarida ({att.distance_m}m)"
        action = "Keldim" if att.action == "check_in" else "Ketdim"
        ts = att.created_at.strftime("%H:%M") if att.created_at else ""
        lines.append(f"- {name}: {action} {ts} ({status})")
    await send_chunked_message(message, "\n".join(lines), reply_markup=get_teacher_students_keyboard())
@router.message(F.text == "📧 Xabar yuborish")
async def teacher_send_message(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    await state.set_state(SendMessageStates.waiting_for_target)
    await message.answer(
        "Foydalanuvchining username yoki Telegram ID sini kiriting:",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(SendMessageStates.waiting_for_target, F.text == "❌ Bekor qilish")
@router.message(SendMessageStates.waiting_for_text, F.text == "❌ Bekor qilish")
async def send_msg_cancel(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    await message.answer("Xabar yuborish bekor qilindi.", reply_markup=keyboard)


@router.message(SendMessageStates.waiting_for_target, F.text)
async def send_msg_target(message: Message, state: FSMContext, session: AsyncSession) -> None:
    target = (message.text or "").strip().lstrip("@")
    if not target:
        await message.answer("Iltimos, username yoki ID kiriting:")
        return
    # Try as telegram ID first
    user = None
    try:
        tg_id = int(target)
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
    except ValueError:
        # Search by username
        result = await session.execute(select(User).where(User.username == target))
        user = result.scalar_one_or_none()
    if not user:
        await message.answer("❌ Foydalanuvchi topilmadi. Qayta kiriting yoki ❌ Bekor qilish bosing.")
        return
    await state.update_data(send_target_tg_id=user.telegram_id, send_target_name=user.full_name or str(user.telegram_id))
    await state.set_state(SendMessageStates.waiting_for_text)
    await message.answer(
        f"Xabar matni kiriting ({user.full_name or user.telegram_id} ga yuboriladi):",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(SendMessageStates.waiting_for_text, F.text)
async def send_msg_text(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Xabar bo'sh bo'lmasligi kerak:")
        return
    data = await state.get_data()
    tg_id = data.get("send_target_tg_id")
    name = data.get("send_target_name", "")
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    try:
        await message.bot.send_message(chat_id=tg_id, text=text)
        await message.answer(f"✅ Xabar yuborildi: {name}", reply_markup=keyboard)
    except Exception as exc:
        await message.answer(f"❌ Xabar yuborib bo'lmadi: {exc}", reply_markup=keyboard)
@router.message(F.text == "👥 Faol o'quvchilar")
async def teacher_active_students(
    message: Message,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    from datetime import timedelta
    seven_days_ago = datetime.now() - timedelta(days=7)
    # Find users who voted in last 7 days
    result = await session.execute(
        select(User)
        .join(PollVote, PollVote.user_id == User.id)
        .where(PollVote.voted_at >= seven_days_ago)
        .group_by(User.id)
        .order_by(func.count(PollVote.id).desc())
        .limit(30)
    )
    users = result.scalars().all()
    if not users:
        await message.answer("Oxirgi 7 kunda faol o'quvchilar topilmadi.", reply_markup=get_teacher_stats_keyboard())
        return
    lines = [f"👥 Faol o'quvchilar (oxirgi 7 kun, {len(users)} ta):", ""]
    for i, user in enumerate(users, 1):
        name = user.full_name or f"ID: {user.telegram_id}"
        lines.append(f"{i}. {name}")
    await send_chunked_message(message, "\n".join(lines), reply_markup=get_teacher_stats_keyboard())
@router.message(F.text == "📝 Topshiriqlar")
async def teacher_task_stats(
    message: Message,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    from sqlalchemy.orm import selectinload
    stmt = (
        select(Task)
        .where(Task.teacher_id == db_user.id)
        .options(selectinload(Task.poll_votes))
        .order_by(Task.created_at.desc())
        .limit(20)
    )
    tasks = (await session.execute(stmt)).scalars().all()
    if not tasks:
        await message.answer("Topshiriqlar topilmadi.", reply_markup=get_teacher_stats_keyboard())
        return
    lines = [f"📝 Topshiriqlar statistikasi ({len(tasks)} ta):", ""]
    for task in tasks:
        votes_by_option: dict[int, int] = {}
        for vote in task.poll_votes:
            votes_by_option[vote.option_id] = votes_by_option.get(vote.option_id, 0) + 1
        total_votes = sum(votes_by_option.values())
        import html as _html
        topic = _html.escape(task.topic or "Mavzu yo'q")
        if votes_by_option:
            # Summarize: option 0,1 = "yaxshi", option 2,3 = "yomon"
            good = votes_by_option.get(0, 0) + votes_by_option.get(1, 0)
            bad = votes_by_option.get(2, 0) + votes_by_option.get(3, 0)
            lines.append(f"📋 {topic}")
            lines.append(f"  Jami: {total_votes} | Yaxshi: {good} | Yomon: {bad}")
        else:
            lines.append(f"📋 {topic}")
            lines.append("  Ovozlar yo'q")
        lines.append("")
    await send_chunked_message(message, "\n".join(lines), reply_markup=get_teacher_stats_keyboard())
@router.message(F.text == "📚 Kitoblar (stat)")
async def teacher_book_stats(
    message: Message,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    order_stats = await get_order_stats(session)
    # Top 5 most ordered books
    top_books_result = await session.execute(
        select(Book.title, func.sum(BookOrderItem.quantity).label("total"))
        .join(BookOrderItem, BookOrderItem.book_id == Book.id)
        .group_by(Book.id, Book.title)
        .order_by(func.sum(BookOrderItem.quantity).desc())
        .limit(5)
    )
    top_books = top_books_result.all()
    # Orders by category
    cat_result = await session.execute(
        select(BookCategory.name, func.count(BookOrderItem.id).label("cnt"))
        .join(Book, Book.category_id == BookCategory.id)
        .join(BookOrderItem, BookOrderItem.book_id == Book.id)
        .group_by(BookCategory.id, BookCategory.name)
        .order_by(func.count(BookOrderItem.id).desc())
    )
    categories = cat_result.all()
    lines = ["📚 Kitoblar statistikasi:", ""]
    lines.append("Buyurtma statuslari:")
    for status, count in order_stats.items():
        lines.append(f"  {status}: {count}")
    lines.append("")
    if top_books:
        lines.append("Eng ko'p buyurtma qilingan kitoblar (Top 5):")
        for i, (title, total) in enumerate(top_books, 1):
            lines.append(f"  {i}. {title} — {int(total or 0)} dona")
        lines.append("")
    if categories:
        lines.append("Kategoriyalar bo'yicha buyurtmalar:")
        for name, cnt in categories:
            lines.append(f"  {name}: {int(cnt or 0)} ta")
    await send_chunked_message(message, "\n".join(lines), reply_markup=get_teacher_stats_keyboard())
@router.message(F.text == "📊 Umumiy hisobot")
async def teacher_general_report(
    message: Message,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Users registered this month
    new_users = await session.scalar(
        select(func.count()).select_from(User).where(User.created_at >= month_start)
    ) or 0
    # Orders this month by status
    order_stats = {}
    for status in ["pending", "confirmed", "delivered", "rejected"]:
        cnt = await session.scalar(
            select(func.count()).where(
                BookOrder.status == status,
                BookOrder.created_at >= month_start,
            )
        ) or 0
        order_stats[status] = cnt
    # Tasks this month
    tasks_count = await session.scalar(
        select(func.count()).select_from(Task).where(Task.created_at >= month_start)
    ) or 0
    # Attendance this month
    from school_bot.bot.services.attendance_service import tashkent_today
    att_check_in = await session.scalar(
        select(func.count()).where(
            TeacherAttendance.action == "check_in",
            TeacherAttendance.created_at >= month_start,
        )
    ) or 0
    att_late = await session.scalar(
        select(func.count()).where(
            TeacherAttendance.action == "check_in",
            TeacherAttendance.is_inside == False,
            TeacherAttendance.created_at >= month_start,
        )
    ) or 0
    month_name = now.strftime("%B %Y")
    lines = [
        f"📊 Umumiy hisobot ({month_name})",
        "",
        f"👥 Yangi foydalanuvchilar: {new_users}",
        "",
        "📦 Buyurtmalar:",
        f"  Kutilmoqda: {order_stats.get('pending', 0)}",
        f"  Tasdiqlangan: {order_stats.get('confirmed', 0)}",
        f"  Yetkazilgan: {order_stats.get('delivered', 0)}",
        f"  Rad etilgan: {order_stats.get('rejected', 0)}",
        "",
        f"📝 Topshiriqlar: {tasks_count}",
        "",
        f"🕒 Davomat (check_in): {att_check_in}",
        f"  Kech kelganlar (tashqarida): {att_late}",
    ]
    await message.answer("\n".join(lines), reply_markup=get_teacher_stats_keyboard())
def _notif_toggle_icon(val: bool) -> str:
    return "✅" if val else "❌"


def _build_notification_settings_keyboard(settings: BotSettings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{_notif_toggle_icon(settings.notify_new_registration)} Yangi ro'yxatdan o'tish",
                    callback_data="notif_toggle:notify_new_registration",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"{_notif_toggle_icon(settings.notify_new_order)} Yangi buyurtma",
                    callback_data="notif_toggle:notify_new_order",
                ),
            ],
            [
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="notif_toggle:back"),
            ],
        ]
    )


@router.message(F.text == "🔔 Bildirishnomalar")
async def teacher_notifications_settings(
    message: Message,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    settings = await get_or_create_settings(session)
    await message.answer(
        "🔔 Bildirishnoma sozlamalari:",
        reply_markup=_build_notification_settings_keyboard(settings),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("notif_toggle:"))
async def notif_toggle_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await callback.answer()
        return
    field = callback.data.split(":", 1)[1]
    if field == "back":
        keyboard = get_teacher_settings_keyboard()
        await callback.message.answer("⚙️ Sozlamalar", reply_markup=keyboard)
        await callback.answer()
        return
    settings = await get_or_create_settings(session)
    if not hasattr(settings, field):
        await callback.answer("Noto'g'ri sozlama", show_alert=True)
        return
    current = getattr(settings, field)
    await update_settings(session, **{field: not current})
    settings = await get_or_create_settings(session)
    await callback.message.edit_reply_markup(reply_markup=_build_notification_settings_keyboard(settings))
    await callback.answer("Yangilandi")
@router.message(F.text == "🔒 Maxfiylik")
async def teacher_privacy_settings(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return
    settings = await get_or_create_settings(session)
    text = (
        "🔒 Maxfiylik sozlamalari\n\n"
        "Ma'lumotlar saqlanish siyosati:\n"
        "- Shaxsiy ma'lumotlar faqat bot ishlashi uchun saqlanadi\n"
        "- Foydalanuvchi istalgan vaqtda /stop orqali o'z ma'lumotlarini o'chirishni so'rashi mumkin\n"
        "- Uchinchi tomonlarga ma'lumot berilmaydi\n\n"
        f"Nofaol foydalanuvchilarni o'chirish: {settings.data_retention_days} kun\n"
    )
    if is_superadmin:
        text += "\nKunlar sonini o'zgartirish uchun raqam kiriting:"
        await state.set_state(PrivacySettingsStates.waiting_for_days)
        await message.answer(text, reply_markup=get_cancel_keyboard())
    else:
        await message.answer(text, reply_markup=get_teacher_settings_keyboard())


@router.message(PrivacySettingsStates.waiting_for_days, F.text == "❌ Bekor qilish")
async def privacy_cancel(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    await message.answer("Bekor qilindi.", reply_markup=keyboard)


@router.message(PrivacySettingsStates.waiting_for_days, F.text)
async def privacy_set_days(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_teacher: bool = False,
) -> None:
    if not is_superadmin:
        await state.clear()
        return
    text = (message.text or "").strip()
    try:
        days = int(text)
        if days < 30 or days > 3650:
            raise ValueError
    except ValueError:
        await message.answer("30 dan 3650 gacha bo'lgan raqam kiriting:")
        return
    await update_settings(session, data_retention_days=days)
    await state.clear()
    await state.update_data(menu_active=True)
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    await message.answer(f"✅ Ma'lumot saqlash muddati {days} kunga o'zgartirildi.", reply_markup=keyboard)
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
async def admin_broadcast(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    total_users = await session.scalar(
        select(func.count()).select_from(User).where(User.telegram_id > 0)
    ) or 0
    await state.set_state(BroadcastStates.waiting_for_message)
    await message.answer(
        f"📢 Xabarnoma yuborish\n\nBarcha foydalanuvchilarga ({total_users} ta) yuboriladi.\n"
        "Xabar matnini kiriting (rasm ham yuborsa bo'ladi):",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(BroadcastStates.waiting_for_message, F.text == "❌ Bekor qilish")
@router.message(BroadcastStates.waiting_for_confirm, F.text == "❌ Bekor qilish")
async def broadcast_cancel(
    message: Message,
    state: FSMContext,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    await state.update_data(menu_active=True)
    builder = SuperAdminMenuBuilder()
    await message.answer("Xabarnoma bekor qilindi.", reply_markup=builder.build_main_keyboard())


@router.message(BroadcastStates.waiting_for_message, F.photo)
async def broadcast_photo(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    caption = message.caption or ""
    await state.update_data(broadcast_photo_id=photo.file_id, broadcast_text=caption)
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, yuborish", callback_data="broadcast_confirm")
    builder.button(text="❌ Bekor qilish", callback_data="broadcast_cancel")
    builder.adjust(2)
    await state.set_state(BroadcastStates.waiting_for_confirm)
    await message.answer(
        f"Rasm + matn yuboriladi. Tasdiqlaysizmi?",
        reply_markup=builder.as_markup(),
    )


@router.message(BroadcastStates.waiting_for_message, F.text)
async def broadcast_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Xabar bo'sh bo'lmasligi kerak:")
        return
    await state.update_data(broadcast_text=text, broadcast_photo_id=None)
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, yuborish", callback_data="broadcast_confirm")
    builder.button(text="❌ Bekor qilish", callback_data="broadcast_cancel")
    builder.adjust(2)
    await state.set_state(BroadcastStates.waiting_for_confirm)
    await message.answer(
        f"Xabar yuboriladi. Tasdiqlaysizmi?\n\nMatn: {text[:200]}",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(BroadcastStates.waiting_for_confirm, lambda c: c.data == "broadcast_cancel")
async def broadcast_cancel_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(menu_active=True)
    builder = SuperAdminMenuBuilder()
    await callback.message.answer("Xabarnoma bekor qilindi.", reply_markup=builder.build_main_keyboard())
    await callback.answer()


@router.callback_query(BroadcastStates.waiting_for_confirm, lambda c: c.data == "broadcast_confirm")
async def broadcast_confirm_cb(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    photo_id = data.get("broadcast_photo_id")
    await state.clear()
    await state.update_data(menu_active=True)
    result = await session.execute(
        select(User.telegram_id).where(User.telegram_id > 0)
    )
    tg_ids = [row[0] for row in result.all()]
    loading = await callback.message.answer(f"Yuborilmoqda... (0/{len(tg_ids)})")
    success = 0
    failed = 0
    for i, tg_id in enumerate(tg_ids):
        try:
            if photo_id:
                await callback.bot.send_photo(chat_id=tg_id, photo=photo_id, caption=text)
            else:
                await callback.bot.send_message(chat_id=tg_id, text=text)
            success += 1
        except Exception:
            failed += 1
        if (i + 1) % 50 == 0:
            try:
                await loading.edit_text(f"Yuborilmoqda... ({i + 1}/{len(tg_ids)})")
            except Exception:
                pass
        # Telegram rate limit: ~30 msg/sec
        if (i + 1) % 25 == 0:
            import asyncio as _asyncio
            await _asyncio.sleep(1)
    builder = SuperAdminMenuBuilder()
    try:
        await loading.edit_text(
            f"✅ Xabarnoma yakunlandi!\n\n"
            f"Yuborildi: {success} ta\n"
            f"Xato: {failed} ta"
        )
    except Exception:
        pass
    await callback.message.answer("Asosiy menyu:", reply_markup=builder.build_main_keyboard())
    await callback.answer()
@router.message(F.text == "📥 Backup")
async def admin_backup(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    loading = await message.answer("⏳ Backup tayyorlanmoqda...")
    try:
        settings = Settings()
        url = make_url(settings.alochi_db_url)
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
async def button_remove_teacher_inline(
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
    # Faqat faol teacherlar ro'yxatini olish
    result = await session.execute(
        select(User).where(
            User.role == UserRole.teacher,
            User.is_active.is_(True),
        ).order_by(User.full_name)
    )
    teachers = result.scalars().all()
    if not teachers:
        await message.answer("📭 Hozircha hech qanday faol o'qituvchi yo'q.")
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
    # Teacherni soft-delete qilish (is_active=False, role=None)
    await revoke_teacher(session, teacher.id)
    await callback.message.edit_text(
        f"✅ O'qituvchi o'chirildi (arxivlandi): {teacher_name}\n"
        f"Tiklash uchun 'Foydalanuvchilar' → '♻️ O'qituvchini tiklash' tugmasidan foydalaning."
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
async def button_remove_teacher_fsm(
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
@router.message(F.text == "♻️ O'qituvchini tiklash")
async def button_restore_teacher(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
) -> None:
    logger.info(
        "Foydalanuvchi 'O'qituvchini tiklash' tugmasini bosdi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "restore_teacher"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_restore_teacher_start
    await cmd_restore_teacher_start(message, state, session, db_user)
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


@router.message(F.text == "➕ O'qituvchi qo'shish")
async def button_add_teacher(
        message: Message,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    await state.set_state(AddTeacherStates.waiting_telegram_id)
    await message.answer(
        "👨‍🏫 O'qituvchi Telegram ID sini kiriting:\n\n"
        "❌ Bekor qilish uchun /cancel bosing",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(AddTeacherStates.waiting_telegram_id, F.text)
async def add_teacher_waiting_id(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    raw_text = (message.text or "").strip()

    if raw_text == "❌ Bekor qilish":
        await cancel_current_action(message, state, session, db_user, is_superadmin)
        return

    if not raw_text.lstrip("-").isdigit():
        await message.answer(
            "❌ Noto'g'ri ID. Faqat raqam kiriting:",
            reply_markup=get_cancel_keyboard(),
        )
        return

    telegram_id = int(raw_text)

    from school_bot.bot.services.user_service import get_or_create_user
    from datetime import datetime, timezone

    user = await get_or_create_user(session, telegram_id=telegram_id, full_name=None)

    # Update role to teacher
    user.role = UserRole.teacher
    await session.flush()

    # Create or update profile with is_approved=True
    profile = await get_profile_by_user_id(session, user.id)
    if profile is None:
        full_name = user.full_name or ""
        parts = full_name.split()
        first_name = parts[0] if parts else "Noma'lum"
        last_name = " ".join(parts[1:]) if len(parts) > 1 else None
        profile = Profile(
            bot_user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            phone="Noma'lum",
            assigned_groups=[],
            profile_type="teacher",
            is_approved=True,
            approved_by_id=db_user.id if db_user else None,
            approved_at=datetime.now(timezone.utc),
        )
        session.add(profile)
    else:
        profile.is_approved = True
        profile.approved_by_id = db_user.id if db_user else None
        profile.approved_at = datetime.now(timezone.utc)

    await session.commit()
    await state.clear()

    username = f"@{user.username}" if user.username else ""
    name = user.full_name or "Ism yo'q"
    keyboard = get_users_management_keyboard()
    display_name = f"{name} {username}".strip()
    await message.answer(
        f"✅ O'qituvchi muvaffaqiyatli qo'shildi!\n\n"
        f"👤 Foydalanuvchi: {display_name}\n"
        f"🆔 Telegram ID: {telegram_id}\n"
        f"📌 Rol: O'qituvchi\n"
        f"✅ Tasdiqlangan: Ha",
        reply_markup=keyboard,
    )


# ============== TEACHER SELF-EDIT ==============

_SELF_PHONE_RE = re.compile(r"^\+998\d{9}$")
_SELF_NAME_PATTERN = re.compile(r"^[A-Za-zА-Яа-яЎўҚқҲҳЭэ\s'\-]+$")


def _build_self_edit_field_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Ism", callback_data="self_edit_field:first_name")
    builder.button(text="✏️ Familiya", callback_data="self_edit_field:last_name")
    builder.button(text="📞 Telefon", callback_data="self_edit_field:phone")
    builder.button(text="❌ Bekor qilish", callback_data="self_edit_cancel")
    builder.adjust(1)
    return builder.as_markup()


@router.message(F.text == "✏️ Profilni tahrirlash")
async def teacher_self_edit_start(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return

    profile = await get_profile_by_user_id(session, db_user.id)
    if not profile:
        await message.answer("❌ Profilingiz topilmadi. Administrator bilan bog'laning.")
        return

    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()
    phone = profile.phone or "Yo'q"

    await state.set_state(TeacherSelfEditStates.choose_field)
    await message.answer(
        f"👤 Joriy ma'lumotlar:\n"
        f"Ism: {profile.first_name}\n"
        f"Familiya: {profile.last_name or 'Yoq'}\n"
        f"Telefon: {phone}\n\n"
        "✏️ Qaysi ma'lumotni o'zgartirmoqchisiz?",
        reply_markup=_build_self_edit_field_keyboard(),
    )


@router.callback_query(TeacherSelfEditStates.choose_field, lambda c: c.data.startswith("self_edit_field:"))
async def teacher_self_edit_field_select(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    try:
        _, field = callback.data.split(":")
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    await state.update_data(self_edit_field=field)

    if field == "first_name":
        await state.set_state(TeacherSelfEditStates.waiting_first_name)
        await callback.message.edit_text(
            "✏️ Yangi ismingizni kiriting (2–50 belgi):\n\n/cancel — bekor qilish"
        )
    elif field == "last_name":
        await state.set_state(TeacherSelfEditStates.waiting_last_name)
        await callback.message.edit_text(
            "✏️ Yangi familiyangizni kiriting (2–50 belgi):\n\n/cancel — bekor qilish"
        )
    elif field == "phone":
        await state.set_state(TeacherSelfEditStates.waiting_phone)
        await callback.message.edit_text(
            "📞 Yangi telefon raqamingizni kiriting (+998XXXXXXXXX):\n\n/cancel — bekor qilish"
        )
    else:
        await callback.answer("❌ Noma'lum maydon.", show_alert=True)
        return

    await callback.answer()


@router.callback_query(
    lambda c: c.data == "self_edit_cancel",
    StateFilter(TeacherSelfEditStates),
)
async def teacher_self_edit_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    db_user=None,
    is_teacher: bool = False,
) -> None:
    await cancel_current_action(callback, state, is_teacher=is_teacher)


@router.message(Command("cancel"))
async def teacher_self_edit_cmd_cancel(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
) -> None:
    await cancel_current_action(message, state, is_teacher=is_teacher)


@router.message(TeacherSelfEditStates.waiting_first_name, F.text)
async def teacher_self_edit_first_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
) -> None:
    if message.text and message.text.strip() == "/cancel":
        await cancel_current_action(message, state, is_teacher=True)
        return

    value = (message.text or "").strip()
    if len(value) < 2 or len(value) > 50:
        await message.answer("❌ Ism 2 dan 50 gacha belgi bo'lishi kerak. Qayta kiriting:\n\n/cancel — bekor qilish")
        return
    if not _SELF_NAME_PATTERN.match(value):
        await message.answer("❌ Ismda faqat harflar, probel, apostrof va tire ishlatish mumkin. Qayta kiriting:\n\n/cancel — bekor qilish")
        return

    profile = await update_teacher_profile(session, db_user.id, first_name=value)
    if not profile:
        await message.answer("❌ Profil topilmadi.")
        await state.clear()
        return

    await state.clear()
    await message.answer(
        f"✅ Saqlandi. Yangi ism: {value}",
        reply_markup=get_teacher_settings_keyboard(),
    )


@router.message(TeacherSelfEditStates.waiting_last_name, F.text)
async def teacher_self_edit_last_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
) -> None:
    if message.text and message.text.strip() == "/cancel":
        await cancel_current_action(message, state, is_teacher=True)
        return

    value = (message.text or "").strip()
    if len(value) < 2 or len(value) > 50:
        await message.answer("❌ Familiya 2 dan 50 gacha belgi bo'lishi kerak. Qayta kiriting:\n\n/cancel — bekor qilish")
        return
    if not _SELF_NAME_PATTERN.match(value):
        await message.answer("❌ Familiyada faqat harflar, probel, apostrof va tire ishlatish mumkin. Qayta kiriting:\n\n/cancel — bekor qilish")
        return

    profile = await update_teacher_profile(session, db_user.id, last_name=value)
    if not profile:
        await message.answer("❌ Profil topilmadi.")
        await state.clear()
        return

    await state.clear()
    await message.answer(
        f"✅ Saqlandi. Yangi familiya: {value}",
        reply_markup=get_teacher_settings_keyboard(),
    )


@router.message(TeacherSelfEditStates.waiting_phone, F.text)
async def teacher_self_edit_phone(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
) -> None:
    if message.text and message.text.strip() == "/cancel":
        await cancel_current_action(message, state, is_teacher=True)
        return

    value = (message.text or "").strip()
    if not _SELF_PHONE_RE.match(value):
        await message.answer(
            "❌ Noto'g'ri format. +998XXXXXXXXX ko'rinishida kiriting:\n\n/cancel — bekor qilish"
        )
        return

    profile = await update_teacher_profile(session, db_user.id, phone=value)
    if not profile:
        await message.answer("❌ Profil topilmadi.")
        await state.clear()
        return

    await state.clear()
    await message.answer(
        f"✅ Saqlandi. Yangi telefon: {value}",
        reply_markup=get_teacher_settings_keyboard(),
    )
