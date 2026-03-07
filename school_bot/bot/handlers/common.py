import re
import time
from typing import Union

from aiogram import Router, F
from aiogram.filters import Command
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
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from datetime import datetime

from school_bot.database.models import User, UserRole, Task
from school_bot.bot.states.new_task import NewTaskStates
from school_bot.bot.states.registration import RegistrationStates
from school_bot.bot.services.profile_service import upsert_profile, upsert_student_profile, can_register_again
from school_bot.bot.services.profile_service import revoke_teacher
from school_bot.bot.services.approval_service import notify_superadmins_new_registration
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.superadmin_menu_builder import SuperAdminMenuBuilder
from school_bot.bot.services.school_service import list_schools, get_school_by_id, get_school_by_number
from school_bot.bot.services.pagination import SchoolPagination
from school_bot.database.models import Profile, Book

router = Router(name=__name__)
logger = get_logger(__name__)


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


async def cancel_current_action(
    target: Union[Message, CallbackQuery],
    state: FSMContext,
    db_user=None,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    await state.clear()

    if db_user and getattr(db_user, "role", None) == UserRole.superadmin:
        is_superadmin = True
        is_teacher = False
        is_librarian = False

    keyboard = get_main_keyboard(is_superadmin, is_teacher, is_librarian)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.delete()
        except Exception:
            pass
        await target.message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)
        await target.answer()
    else:
        await target.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


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
                    callback_data=f"school_select:{school.number}",
                )
            )
        if row:
            builder.row(*row)

    nav_row = []
    if pagination.has_previous():
        nav_row.append(
            InlineKeyboardButton(
                text="◀️ Oldingi",
                callback_data=f"school_page:{pagination.page - 1}",
            )
        )

    nav_row.append(
        InlineKeyboardButton(
            text=f"📍 {pagination.page}/{pagination.total_pages}",
            callback_data="school_page_info",
        )
    )

    if pagination.has_next():
        nav_row.append(
            InlineKeyboardButton(
                text="▶️ Keyingi",
                callback_data=f"school_page:{pagination.page + 1}",
            )
        )

    if nav_row:
        builder.row(*nav_row)

    builder.row(
        InlineKeyboardButton(
            text="❌ Bekor qilish",
            callback_data="school_cancel",
        )
    )
    return builder.as_markup()


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
    await cmd_start(
        message,
        state,
        session,
        db_user,
        profile,
        is_superadmin,
        is_teacher,
        is_librarian,
        is_group_admin,
        is_student,
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
async def student_tasks_menu(message: Message, is_student: bool = False) -> None:
    if not is_student:
        return
    await message.answer("📘 Topshiriqlar bo'limi tez orada ishga tushadi.", reply_markup=get_student_keyboard())


@router.message(F.text == "📊 Baholar")
async def student_grades_menu(message: Message, is_student: bool = False) -> None:
    if not is_student:
        return
    await message.answer("📊 Baholar bo'limi tez orada ishga tushadi.", reply_markup=get_student_keyboard())


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


@router.message(F.text == "📋 Joriy topshiriqlar")
async def teacher_current_tasks(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📋 Joriy topshiriqlar bo'limi tez orada ishga tushadi.")


@router.message(F.text == "📊 Baholar jurnali")
async def teacher_gradebook(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📊 Baholar jurnali bo'limi tez orada ishga tushadi.")


@router.message(F.text == "📈 O'rtacha ball")
async def teacher_average_scores(message: Message, is_teacher: bool = False, is_superadmin: bool = False) -> None:
    if not (is_teacher or is_superadmin):
        return
    await message.answer("📈 O'rtacha ball hisoboti tez orada ishga tushadi.")


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
    lines = ["📚 Kitoblar bo'limi:", ""]
    for category in categories:
        lines.append(f"• {category.name}")
    await message.answer("\n".join(lines))


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
            [KeyboardButton(text="📚 Kitob buyurtma qilish")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )
    await message.answer("📚 **KITOBLAR BO'LIMI**", reply_markup=keyboard)


@router.message(F.text == "👥 Foydalanuvchilar")
async def admin_users_menu_alias(
    message: Message,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    await button_users(message, session, db_user, is_superadmin=is_superadmin)


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
@router.message(F.text == "💾 Backup")
async def admin_backup(message: Message, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        return
    await message.answer("📥 Backup bo'limi tez orada qo'shiladi.")


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
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👥 Foydalanuvchilar")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )
    await message.answer("👥 **FOYDALANUVCHILAR (O'QUVCHILAR)**", reply_markup=keyboard)


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
    await message.answer("📊 **STATISTIKA BO'LIMI**", reply_markup=keyboard)


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
    await message.answer("📈 **Grafiklar bo'limi**", reply_markup=keyboard)


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
            [KeyboardButton(text="➕ Guruh qo'shish")],
            [KeyboardButton(text="✏️ Guruh tahrirlash")],
            [KeyboardButton(text="🗑️ Guruh o'chirish")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )
    await message.answer("📚 **GURUHLAR BO'LIMI**", reply_markup=keyboard)


@router.message(F.text == "🔙 Orqaga")
async def go_back_to_main(
        message: Message,
        state: FSMContext,
        is_superadmin: bool = False,
        is_teacher: bool = False,
        is_librarian: bool = False,
) -> None:
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher, is_librarian=is_librarian)
    await message.answer("🏠 **Asosiy menyu**", reply_markup=keyboard)


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

        button_text = f"👨‍🏫 {teacher_name}"
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
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    await message.answer(
        "❌ Kategoriya nomini yozing.\n"
        "Ishlatilishi: /add_category [nomi]\n"
        "Masalan: /add_category 1-sinf"
    )


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


@router.message(F.text == "👥 Foydalanuvchilar")
async def button_users(
        message: Message,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi /users buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "users"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return

    result = await session.execute(
        select(User, Profile)
        .join(Profile, Profile.user_id == User.id)
        .where(Profile.profile_type == "student", Profile.is_approved.is_(True))
        .order_by(Profile.registered_at.desc())
    )
    rows = result.all()

    if not rows:
        result_message = "📭 Hozircha hech qanday o'quvchi yo'q."
    else:
        lines = ["👥 **Barcha o'quvchilar:**", f"Jami: {len(rows)} ta", ""]
        for i, (user, profile) in enumerate(rows[:20], 1):
            name = f"{profile.first_name} {profile.last_name or ''}".strip() or user.full_name or "Ism yo'q"
            groups = profile.assigned_groups or []
            group_text = ", ".join(groups) if groups else "Sinf biriktirilmagan"
            created = profile.registered_at.strftime('%d.%m.%Y') if profile.registered_at else 'Noma\'lum'
            lines.append(f"{i}. 🆔 {user.telegram_id}")
            lines.append(f"   👤 {name}")
            lines.append(f"   📚 Sinf: {group_text}")
            lines.append(f"   📅 Ro'yxatdan: {created}")
            lines.append("")
        if len(rows) > 20:
            lines.append(f"... va yana {len(rows) - 20} ta o'quvchi")
        result_message = "\n".join(lines)

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


@router.message(F.text == "📋 Guruhlar ro'yxati")
async def button_groups(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi guruhlar ro'yxatini so'radi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "groups"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_groups
    await cmd_groups(message, session, is_superadmin)


@router.message(F.text == "➕ Guruh qo'shish")
async def button_add_group(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi guruh qo'shishni boshladi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_group"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_add_group_start
    await cmd_add_group_start(message, state, session, is_superadmin)


@router.message(F.text == "✏️ Guruh tahrirlash")
async def button_edit_group(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi guruh tahrirlashni boshladi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "edit_group"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_edit_group_start
    await cmd_edit_group_start(message, state, session, is_superadmin)


@router.message(F.text == "🗑️ Guruh o'chirish")
async def button_remove_group(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False
) -> None:
    logger.info(
        "Foydalanuvchi guruh o'chirishni boshladi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "remove_group"},
    )
    if not is_superadmin:
        await message.answer("⛔ Bu tugma faqat superadminlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_remove_group_start
    await cmd_remove_group_start(message, session, is_superadmin)


def _normalize_phone_number(raw: str) -> str | None:
    if not raw:
        return None

    raw = raw.strip()

    # Flexible validation: optional +, allow separators (spaces, dashes, parentheses)
    uz_full_pattern = re.compile(
        r"^\s*\+?\s*\(?\s*998\)?[\s\-]*\d{2}[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\s*$"
    )
    uz_local_pattern = re.compile(
        r"^\s*\(?\s*\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\s*$"
    )

    if not (uz_full_pattern.match(raw) or uz_local_pattern.match(raw)):
        return None

    digits = re.sub(r"\D", "", raw)

    if digits.startswith("998") and len(digits) == 12:
        return f"+{digits}"

    if len(digits) == 9 and digits.startswith("9"):
        return f"+998{digits}"

    return None


STUDENT_CLASSES = [
    "1-A", "1-B",
    "2-A", "2-B",
    "3-A", "3-B",
    "4-A", "4-B",
    "5-A", "5-B",
    "6-A", "6-B",
    "7-A", "7-B",
    "8-A", "8-B",
    "9-A", "9-B",
    "10-A", "10-B",
    "11-A", "11-B",
]


def build_student_class_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for class_name in STUDENT_CLASSES:
        builder.button(text=class_name, callback_data=f"class_select:{class_name}")
    builder.adjust(3)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="reg_cancel"))
    return builder.as_markup()


@router.message(RegistrationStates.welcome, F.text == "✅ Ro'yxatdan o'tish")
async def registration_welcome_accept(message: Message, state: FSMContext) -> None:
    await state.update_data(reg_type="teacher")
    await state.set_state(RegistrationStates.first_name)
    await message.answer("👤 Ismingizni kiriting:", reply_markup=get_cancel_keyboard())


@router.message(RegistrationStates.welcome, F.text == "❌ Bekor qilish")
async def registration_welcome_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "❌ Jarayon bekor qilindi. Qayta boshlash uchun /start bosing.",
        reply_markup=get_main_keyboard(False, False),
    )


@router.message(RegistrationStates.welcome)
async def registration_welcome_invalid(message: Message) -> None:
    await message.answer(
        "Iltimos, ro'yxatdan o'tishni boshlash uchun tugmani bosing.",
        reply_markup=get_registration_start_keyboard(),
    )


@router.message(RegistrationStates.first_name, F.text)
async def registration_first_name(message: Message, state: FSMContext) -> None:
    first_name = (message.text or "").strip()
    if not first_name:
        await message.answer("❌ Ism bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    await state.update_data(first_name=first_name)
    logger.info(
        "Foydalanuvchi ismini kiritdi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "register"},
    )
    await state.set_state(RegistrationStates.last_name)
    await message.answer("👤 Familiyangizni kiriting:", reply_markup=get_cancel_keyboard())


@router.message(RegistrationStates.first_name)
async def registration_first_name_invalid(message: Message) -> None:
    await message.answer("❌ Iltimos, ismingizni matn ko'rinishida yuboring.", reply_markup=get_cancel_keyboard())


@router.message(RegistrationStates.last_name, F.text)
async def registration_last_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    last_name = (message.text or "").strip()
    if not last_name:
        await message.answer("❌ Familiya bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    await state.update_data(last_name=last_name)
    logger.info(
        "Foydalanuvchi familiyasini kiritdi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "register"},
    )
    data = await state.get_data()
    reg_type = data.get("reg_type", "teacher")
    if reg_type == "student":
        await state.set_state(RegistrationStates.phone)
        await message.answer(
            "📱 Telefon raqamingizni yuboring:",
            reply_markup=get_contact_cancel_keyboard(),
        )
        return

    schools = await list_schools(session)
    if not schools:
        await message.answer("❌ Maktablar ro'yxati topilmadi. Administrator bilan bog'laning.")
        await state.clear()
        return

    await state.set_state(RegistrationStates.school)
    await state.update_data(school_page=1)
    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = build_registration_school_keyboard(schools, page=1, per_page=10)
    await message.answer(
        f"🏫 Maktabingizni tanlang (1/{total_pages} sahifa):",
        reply_markup=keyboard,
    )


@router.message(RegistrationStates.last_name)
async def registration_last_name_invalid(message: Message) -> None:
    await message.answer("❌ Iltimos, familiyangizni matn ko'rinishida yuboring.", reply_markup=get_cancel_keyboard())


@router.callback_query(lambda c: c.data.startswith("school_select:"))
async def registration_school_select_inline(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        school_number = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri maktab.", show_alert=True)
        return

    school = await get_school_by_number(session, school_number)
    if not school:
        await callback.answer("❌ Maktab topilmadi.", show_alert=True)
        return

    await state.update_data(school_id=school.id, school_name=school.name)
    await state.set_state(RegistrationStates.phone)
    await callback.message.delete()
    await callback.message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=get_contact_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("school_page:"))
async def registration_school_page_inline(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri sahifa.", show_alert=True)
        return

    schools = await list_schools(session)
    if not schools:
        await callback.answer("❌ Maktablar topilmadi.", show_alert=True)
        return

    total_pages = max(1, (len(schools) + 9) // 10)
    page = max(1, min(page, total_pages))
    await state.update_data(school_page=page)
    keyboard = build_registration_school_keyboard(schools, page=page, per_page=10)
    await callback.message.edit_text(
        f"🏫 Maktabingizni tanlang ({page}/{total_pages} sahifa):",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "school_page_info")
async def registration_school_page_info_inline(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(lambda c: c.data == "school_cancel")
async def registration_school_cancel_inline(callback: CallbackQuery, state: FSMContext) -> None:
    await cancel_current_action(callback, state)


@router.message(RegistrationStates.school)
async def registration_school_invalid(message: Message, session: AsyncSession) -> None:
    schools = await list_schools(session)
    if not schools:
        await message.answer("❌ Maktablar ro'yxati topilmadi. Administrator bilan bog'laning.")
        return
    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = build_registration_school_keyboard(schools, page=1, per_page=10)
    await message.answer(
        f"❌ Iltimos, maktabni tugmalar orqali tanlang.\n\n"
        f"🏫 Maktabingizni tanlang (1/{total_pages} sahifa):",
        reply_markup=keyboard,
    )


@router.message(RegistrationStates.phone, F.contact)
async def registration_phone_contact(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    contact = message.contact
    if contact.user_id and contact.user_id != message.from_user.id:
        await message.answer(
            "❌ Iltimos, o'zingizning telefon raqamingizni yuboring.",
            reply_markup=get_contact_cancel_keyboard(),
        )
        return

    normalized_phone = _normalize_phone_number(contact.phone_number)
    if not normalized_phone:
        logger.warning(
            "Foydalanuvchi noto'g'ri telefon raqami yubordi",
            extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "register"},
        )
        await message.answer(
            "❌ Noto'g'ri telefon raqam formati. Iltimos, quyidagi formatlardan birini kiriting: "
            "+998901234567 yoki 998901234567",
            reply_markup=get_contact_cancel_keyboard(),
        )
        return

    await state.update_data(phone=normalized_phone)
    logger.info(
        "Foydalanuvchi telefon raqamini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "register"},
    )
    data = await state.get_data()
    if data.get("reg_type") == "student":
        await state.set_state(RegistrationStates.class_group)
        await message.answer(
            "Sinfingizni tanlang:",
            reply_markup=build_student_class_keyboard(),
        )
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "(foydalanuvchi nomi yo'q)"

    confirm_lines = [
        "📋 **Ma'lumotlaringiz:**",
        f"👤 Ism: {data['first_name']}",
        f"👤 Familiya: {data['last_name']}",
        f"🏫 Maktab: {data.get('school_name', 'Tanlanmagan')}",
        f"🔹 Foydalanuvchi nomi: {username}",
        f"📱 Telefon: {normalized_phone}",
        "",
        "✅ Tasdiqlash",
        "❌ Qayta kiritish",
    ]

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="reg_confirm")
    builder.button(text="❌ Qayta kiritish", callback_data="reg_retry")
    builder.adjust(2)

    await state.set_state(RegistrationStates.confirm)
    await message.answer("✅ Ma'lumotlar qabul qilindi.", reply_markup=ReplyKeyboardRemove())
    await message.answer("\n".join(confirm_lines), reply_markup=builder.as_markup())


@router.message(RegistrationStates.phone, F.text)
async def registration_phone_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    phone_input = (message.text or "").strip()
    normalized_phone = _normalize_phone_number(phone_input)
    if not normalized_phone:
        logger.warning(
            "Foydalanuvchi noto'g'ri telefon raqami kiritdi",
            extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "register"},
        )
        await message.answer(
            "❌ Noto'g'ri telefon raqam formati. Iltimos, quyidagi formatlardan birini kiriting: "
            "+998901234567 yoki 998901234567",
            reply_markup=get_contact_cancel_keyboard(),
        )
        return

    await state.update_data(phone=normalized_phone)
    data = await state.get_data()
    if data.get("reg_type") == "student":
        await state.set_state(RegistrationStates.class_group)
        await message.answer(
            "Sinfingizni tanlang:",
            reply_markup=build_student_class_keyboard(),
        )
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "(foydalanuvchi nomi yo'q)"

    confirm_lines = [
        "📋 **Ma'lumotlaringiz:**",
        f"👤 Ism: {data['first_name']}",
        f"👤 Familiya: {data['last_name']}",
        f"🏫 Maktab: {data.get('school_name', 'Tanlanmagan')}",
        f"🔹 Foydalanuvchi nomi: {username}",
        f"📱 Telefon: {normalized_phone}",
        "",
        "✅ Tasdiqlash",
        "❌ Qayta kiritish",
    ]

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="reg_confirm")
    builder.button(text="❌ Qayta kiritish", callback_data="reg_retry")
    builder.adjust(2)

    await state.set_state(RegistrationStates.confirm)
    await message.answer("✅ Ma'lumotlar qabul qilindi.", reply_markup=ReplyKeyboardRemove())
    await message.answer("\n".join(confirm_lines), reply_markup=builder.as_markup())


@router.message(RegistrationStates.phone)
async def registration_phone_invalid(message: Message) -> None:
    await message.answer(
        "❌ Telefon raqamingizni yuboring (kontakt tugmasi yoki matn orqali).",
        reply_markup=get_contact_cancel_keyboard(),
    )


@router.callback_query(lambda c: c.data.startswith("class_select:"))
async def registration_class_select(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user,
) -> None:
    class_name = callback.data.split(":", 1)[1] if callback.data else ""
    if not class_name:
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    data = await state.get_data()
    if data.get("reg_type") != "student":
        await callback.answer()
        return

    await upsert_student_profile(
        session=session,
        user_id=db_user.id,
        first_name=data.get("first_name", ""),
        last_name=data.get("last_name"),
        phone=data.get("phone", ""),
        class_name=class_name,
        school_id=data.get("school_id"),
    )

    await state.clear()
    await state.update_data(menu_active=True)
    await callback.message.edit_text(
        "✅ Ro'yxatdan o'tish muvaffaqiyatli yakunlandi!\n"
        "Endi siz botdan to'liq foydalanishingiz mumkin."
    )
    await callback.message.answer("Menyudan tanlang:", reply_markup=get_student_keyboard())
    await callback.answer()


@router.callback_query(lambda c: c.data == "reg_confirm")
async def registration_confirm(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        db_user,
) -> None:
    data = await state.get_data()
    profile = await upsert_profile(
        session=session,
        user_id=db_user.id,
        first_name=data["first_name"],
        last_name=data["last_name"],
        phone=data["phone"],
        school_id=data.get("school_id"),
        profile_type=data.get("reg_type", "teacher"),
    )

    await state.clear()
    if profile.is_approved:
        logger.info(
            "Foydalanuvchi allaqachon tasdiqlangan profilga ega",
            extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "register"},
        )
        await callback.message.edit_text("✅ Profilingiz allaqachon tasdiqlangan.")
        await callback.answer()
        return

    await callback.message.edit_text("✅ Ro'yxatdan o'tish so'rovingiz administratorga yuborildi.")
    logger.info(
        "Ro'yxatdan o'tish so'rovi yuborildi",
        extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "register"},
    )
    await notify_superadmins_new_registration(session=session, bot=callback.bot, profile=profile)
    await callback.answer()


@router.callback_query(lambda c: c.data == "reg_retry")
async def registration_retry(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(reg_type="teacher")
    await state.set_state(RegistrationStates.first_name)
    logger.info(
        "Foydalanuvchi ro'yxatdan o'tishni qayta boshladi",
        extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "register"},
    )
    await callback.message.edit_text("👤 Ismingizni kiriting:")
    await callback.answer()


@router.message(Command("cancel"))
async def cmd_cancel(
        message: Message,
        state: FSMContext,
    db_user=None,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    logger.info(
        "Foydalanuvchi /cancel buyrug'ini yubordi",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "cancel"},
    )
    await cancel_current_action(
        message,
        state,
        db_user=db_user,
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )


@router.message(F.text == "❌ /cancel")
async def button_cancel(
        message: Message,
        state: FSMContext,
        db_user=None,
        is_superadmin: bool = False,
        is_teacher: bool = False,
        is_librarian: bool = False,
) -> None:
    await cancel_current_action(
        message,
        state,
        db_user=db_user,
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )


@router.message(F.text == "❌ Bekor qilish")
async def button_cancel_text(
        message: Message,
        state: FSMContext,
        db_user=None,
        is_superadmin: bool = False,
        is_teacher: bool = False,
        is_librarian: bool = False,
) -> None:
    await cancel_current_action(
        message,
        state,
        db_user=db_user,
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )


def _is_cancel_callback(data: str | None) -> bool:
    if not data:
        return False
    return data == "cancel" or data.endswith("_cancel") or data.endswith(":cancel")


@router.callback_query(lambda c: _is_cancel_callback(c.data))
async def inline_cancel_handler(
        callback: CallbackQuery,
        state: FSMContext,
        db_user=None,
        is_superadmin: bool = False,
        is_teacher: bool = False,
        is_librarian: bool = False,
) -> None:
    await cancel_current_action(
        callback,
        state,
        db_user=db_user,
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )


@router.message(Command("skip"))
async def cmd_skip(message: Message, state: FSMContext) -> None:
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("⚠️ Hozir hech narsani o'tkazib yuborib bo'lmaydi.")
        return

    await message.answer(
        "⚠️ Bu bosqichda o'tkazib yuborish mumkin emas. Iltimos, ko'rsatmalarga rioya qiling."
    )


@router.message(F.text)
async def handle_unknown_message(
        message: Message,
        state: FSMContext,
        profile,
        is_superadmin: bool = False,
        is_teacher: bool = False,
        is_librarian: bool = False,
) -> None:
    """Noma'lum xabarlar uchun handler - FAQAT FSM holati bo'lmaganda ishlaydi"""
    current_state = await state.get_state()

    # Agar FSM holati bo'lsa, bu xabarni ignore qil (boshqa handlerlar ishlaydi)
    if current_state is not None:
        return  # MUHIM: return qilish kerak, boshqa handler ishlashi uchun

    # Guruhlarda faqat buyruq/mention/reply bo'lsa javob beramiz
    if message.chat.type in ("group", "supergroup"):
        text = message.text or ""
        if not text.startswith("/"):
            bot_user = await message.bot.get_me()
            bot_username = (bot_user.username or "").strip()
            mention = bool(bot_username and f"@{bot_username}" in text)
            reply_to_bot = bool(
                message.reply_to_message
                and message.reply_to_message.from_user
                and message.reply_to_message.from_user.is_bot
                and message.reply_to_message.from_user.id == bot_user.id
            )
            if not mention and not reply_to_bot:
                return

    # No fallback response: silently ignore unknown messages
    return
