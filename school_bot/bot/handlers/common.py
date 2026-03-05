from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from datetime import datetime

from school_bot.bot.config import Settings
from school_bot.database.models import User, UserRole, Task
from school_bot.bot.states.book_order import BookOrderStates
from school_bot.bot.states.new_task import NewTaskStates

router = Router(name=__name__)


class RemoveTeacherStates:
    waiting_for_selection = "waiting_for_selection"


def get_main_keyboard(is_superuser: bool = False, is_teacher: bool = False) -> ReplyKeyboardMarkup:
    """Asosiy menyu tugmalarini yaratish"""
    builder = ReplyKeyboardBuilder()

    # Barcha uchun umumiy tugmalar
    builder.row(KeyboardButton(text="/start"))
    builder.row(KeyboardButton(text="/help"))

    # Teacher uchun tugmalar
    if is_teacher:
        builder.row(
            KeyboardButton(text="/new_task"),
            KeyboardButton(text="/order_book")
        )

    # Superuser uchun tugmalar
    if is_superuser:
        builder.row(
            KeyboardButton(text="/add_teacher"),
            KeyboardButton(text="/remove_teacher")
        )
        builder.row(
            KeyboardButton(text="/list_teachers"),
            KeyboardButton(text="/stats")
        )
        builder.row(
            KeyboardButton(text="/users")
        )

    # Tugmalarni sozlash
    builder.adjust(2)

    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Komandani tanlang...")


@router.message(Command("start", "help"))
async def cmd_start(
        message: Message,
        is_superuser: bool = False,
        is_teacher: bool = False
) -> None:
    """Start va help komandalari"""
    print(
        f"🔍 DEBUG: cmd_start called by user {message.from_user.id}, is_superuser={is_superuser}, is_teacher={is_teacher}")

    lines: list[str] = [
        "📚 **School Task Poll Bot**",
        "",
        "Quyidagi tugmalardan foydalanishingiz mumkin:",
    ]

    if is_teacher:
        lines += [
            "",
            "👨‍🏫 **Teacher komandalari:**",
            "/new_task - Yangi topshiriq",
            "/order_book - Kitob buyurtma",
        ]

    if is_superuser:
        lines += [
            "",
            "👑 **Superuser komandalari:**",
            "/add_teacher - Teacher qo'shish",
            "/remove_teacher - Teacher o'chirish",
            "/list_teachers - Teacherlar ro'yxati",
            "/stats - Statistika",
            "/users - Foydalanuvchilar",
        ]

    lines += [
        "",
        "📊 Oddiy foydalanuvchilar pollarda qatnashishi mumkin.",
        "",
        "ℹ️ Tugmalarni bosish yoki komandalarni yozish orqali botdan foydalaning."
    ]

    keyboard = get_main_keyboard(is_superuser, is_teacher)
    await message.answer("\n".join(lines), reply_markup=keyboard)


@router.message(F.text == "/start")
async def button_start(message: Message, is_superuser: bool = False, is_teacher: bool = False) -> None:
    print(f"🔍 DEBUG: button_start pressed by user {message.from_user.id}")
    await cmd_start(message, is_superuser, is_teacher)


@router.message(F.text == "/help")
async def button_help(message: Message, is_superuser: bool = False, is_teacher: bool = False) -> None:
    print(f"🔍 DEBUG: button_help pressed by user {message.from_user.id}")
    await cmd_start(message, is_superuser, is_teacher)


@router.message(F.text == "/new_task")
async def button_new_task(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_teacher: bool = False
) -> None:
    print(f"🔍 DEBUG: button_new_task pressed by user {message.from_user.id}, is_teacher={is_teacher}")
    if not is_teacher:
        await message.answer("⛔ Bu tugma faqat teacherlar uchun.")
        return
    from school_bot.bot.handlers.teacher import cmd_new_task
    await cmd_new_task(message, state, is_teacher)


@router.message(F.text == "/order_book")
async def button_order_book(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_teacher: bool = False
) -> None:
    print(f"🔍 DEBUG: button_order_book pressed by user {message.from_user.id}, is_teacher={is_teacher}")
    if not is_teacher:
        await message.answer("⛔ Bu tugma faqat teacherlar uchun.")
        return
    from school_bot.bot.handlers.teacher import cmd_order_book
    await cmd_order_book(message, state, is_teacher)


@router.message(F.text == "/add_teacher")
async def button_add_teacher(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superuser: bool = False
) -> None:
    print(f"🔍 DEBUG: button_add_teacher pressed by user {message.from_user.id}, is_superuser={is_superuser}")
    if not is_superuser:
        await message.answer("⛔ Bu tugma faqat superuserlar uchun.")
        return
    from school_bot.bot.handlers.admin import cmd_add_teacher_start
    await cmd_add_teacher_start(message, state, is_superuser)


@router.message(F.text == "/remove_teacher")
async def button_remove_teacher(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superuser: bool = False
) -> None:
    print(f"🔍 DEBUG: button_remove_teacher pressed by user {message.from_user.id}, is_superuser={is_superuser}")
    if not is_superuser:
        await message.answer("⛔ Bu tugma faqat superuserlar uchun.")
        return

    # Teacherlar ro'yxatini olish
    result = await session.execute(
        select(User).where(User.role == UserRole.teacher).order_by(User.full_name)
    )
    teachers = result.scalars().all()
    print(f"🔍 DEBUG: Found {len(teachers)} teachers")

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
        builder.button(text=button_text, callback_data=f"del_teacher_{teacher.id}")

    builder.adjust(1)

    # RemoveTeacherStates ni dinamik ravishda saqlash
    await state.update_data(awaiting_teacher_selection=True)
    await message.answer(
        "👨‍🏫 O'chirmoqchi bo'lgan o'qituvchingizni tanlang:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(lambda c: c.data.startswith("del_teacher_"))
async def process_remove_teacher_selection(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    """Tanlangan o'qituvchini o'chirish"""
    teacher_id = int(callback.data.replace("del_teacher_", ""))
    print(f"🔍 DEBUG: Removing teacher with ID: {teacher_id}")

    # Teacherni bazadan olish
    result = await session.execute(
        select(User).where(User.id == teacher_id)
    )
    teacher = result.scalar_one_or_none()

    if not teacher:
        print(f"🔍 DEBUG: Teacher not found with ID: {teacher_id}")
        await callback.message.edit_text("❌ O'qituvchi topilmadi.")
        await callback.answer()
        return

    # Teacher ismini saqlab qolish
    if teacher.full_name:
        teacher_name = teacher.full_name
    else:
        teacher_name = f"ID: {teacher.telegram_id}"

    print(f"🔍 DEBUG: Removing teacher: {teacher_name}")

    # Teacherni o'chirish (role ni None qilish)
    teacher.role = None
    await session.commit()

    await callback.message.edit_text(
        f"✅ O'qituvchi olib tashlandi: {teacher_name}\n"
        f"📊 Endi u oddiy foydalanuvchi."
    )

    await state.clear()
    await callback.answer()


@router.message(F.text == "/list_teachers")
async def button_list_teachers(
        message: Message,
        session: AsyncSession,
        db_user,
        is_superuser: bool = False
) -> None:
    print(f"🔍 DEBUG: button_list_teachers pressed by user {message.from_user.id}, is_superuser={is_superuser}")
    if not is_superuser:
        await message.answer("⛔ Bu tugma faqat superuserlar uchun.")
        return

    result = await session.execute(
        select(User).where(User.role == UserRole.teacher).order_by(User.created_at)
    )
    teachers = result.scalars().all()

    if not teachers:
        result_message = "📭 Hozircha hech qanday o'qituvchi yo'q."
    else:
        lines = ["👨‍🏫 **Barcha o'qituvchilar:**", ""]
        for i, teacher in enumerate(teachers, 1):
            if teacher.full_name:
                full_name = teacher.full_name
            else:
                full_name = "❌ Ism yo'q (Telegram profilda ism kiritilmagan)"
            lines.append(f"{i}. 🆔 {teacher.telegram_id}")
            lines.append(f"   👤 {full_name}")
            lines.append(
                f"   📅 Qo'shilgan: {teacher.created_at.strftime('%d.%m.%Y') if teacher.created_at else 'Noma\'lum'}")
            lines.append("")
        result_message = "\n".join(lines)

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


@router.message(F.text == "/stats")
async def button_stats(
        message: Message,
        session: AsyncSession,
        db_user,
        is_superuser: bool = False
) -> None:
    print(f"🔍 DEBUG: button_stats pressed by user {message.from_user.id}, is_superuser={is_superuser}")
    if not is_superuser:
        await message.answer("⛔ Bu tugma faqat superuserlar uchun.")
        return

    # Umumiy statistika
    users_count = await session.scalar(select(func.count()).select_from(User))
    teachers_count = await session.scalar(select(func.count()).where(User.role == UserRole.teacher))
    superusers_count = await session.scalar(select(func.count()).where(User.role == UserRole.superuser))
    regular_users = await session.scalar(select(func.count()).where(User.role.is_(None)))
    tasks_count = await session.scalar(select(func.count()).select_from(Task))

    # Eng faol teacherlar
    active_teachers = await session.execute(
        select(User.telegram_id, User.full_name, func.count(Task.id).label("task_count"))
        .join(Task, User.id == Task.teacher_id)
        .where(User.role == UserRole.teacher)
        .group_by(User.id)
        .order_by(func.count(Task.id).desc())
        .limit(5)
    )
    active_teachers = active_teachers.all()

    # Statistikani shakllantirish
    lines = [
        "📊 **Bot statistikasi**",
        "=" * 30,
        "",
        "👥 **Foydalanuvchilar:**",
        f"   • Jami: {users_count} ta",
        f"   • Superuser: {superusers_count} ta",
        f"   • Teacher: {teachers_count} ta",
        f"   • Oddiy user: {regular_users} ta",
        "",
        f"📝 **Topshiriqlar:** {tasks_count} ta",
        ""
    ]

    if active_teachers:
        lines.append("⭐ **Eng faol teacherlar:**")
        for i, teacher in enumerate(active_teachers, 1):
            teacher_name = teacher.full_name or f"Foydalanuvchi {teacher.telegram_id}"
            lines.append(f"   {i}. {teacher_name} - {teacher.task_count} ta")
    else:
        lines.append("⭐ **Eng faol teacherlar:**")
        lines.append("   Hozircha hech qanday topshiriq yo'q")

    lines.extend(["", f"📅 Oxirgi yangilanish: {datetime.now().strftime('%d.%m.%Y %H:%M')}"])

    result_message = "\n".join(lines)

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


@router.message(F.text == "/users")
async def button_users(
        message: Message,
        session: AsyncSession,
        db_user,
        is_superuser: bool = False
) -> None:
    print(f"🔍 DEBUG: button_users pressed by user {message.from_user.id}, is_superuser={is_superuser}")
    if not is_superuser:
        await message.answer("⛔ Bu tugma faqat superuserlar uchun.")
        return

    # Barcha userlarni olish
    result = await session.execute(
        select(User).order_by(User.created_at.desc())
    )
    users = result.scalars().all()

    if not users:
        result_message = "📭 Hozircha hech qanday foydalanuvchi yo'q."
    else:
        lines = ["👥 **Barcha foydalanuvchilar:**", f"Jami: {len(users)} ta", ""]
        for i, user in enumerate(users[:20], 1):  # Oxirgi 20 ta
            if user.role == UserRole.superuser:
                role_emoji = "👑 Superuser"
            elif user.role == UserRole.teacher:
                role_emoji = "👨‍🏫 Teacher"
            else:
                role_emoji = "👤 Oddiy user"
            name = user.full_name or "Ism yo'q"
            created = user.created_at.strftime('%d.%m.%Y') if user.created_at else 'Noma\'lum'
            lines.append(f"{i}. 🆔 {user.telegram_id}")
            lines.append(f"   👤 {name}")
            lines.append(f"   📌 {role_emoji}")
            lines.append(f"   📅 Qo'shilgan: {created}")
            lines.append("")
        if len(users) > 20:
            lines.append(f"... va yana {len(users) - 20} ta foydalanuvchi")
        result_message = "\n".join(lines)

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


@router.message(Command("cancel"))
async def cmd_cancel(
        message: Message,
        state: FSMContext,
        is_superuser: bool = False,
        is_teacher: bool = False
) -> None:
    print(f"🔍 DEBUG: cmd_cancel called by user {message.from_user.id}")
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Hech qanday jarayon yo'q.")
        return

    await state.clear()
    keyboard = get_main_keyboard(is_superuser, is_teacher)
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.message()
async def handle_unknown_message(
        message: Message,
        state: FSMContext,
        is_superuser: bool = False,
        is_teacher: bool = False
) -> None:
    """Noma'lum xabarlar uchun handler - FAQAT FSM holati bo'lmaganda ishlaydi"""
    current_state = await state.get_state()

    # Agar FSM holati bo'lsa, bu xabarni ignore qil (boshqa handlerlar ishlaydi)
    if current_state is not None:
        print(f"🔍 DEBUG: Unknown message ignored - FSM state active: {current_state}")
        return  # MUHIM: return qilish kerak, boshqa handler ishlashi uchun

    # Faqat tugmalar ro'yxatiga kirmagan xabarlar uchun
    if message.text and not message.text.startswith('/'):
        print(f"🔍 DEBUG: Unknown message: {message.text}")
        keyboard = get_main_keyboard(is_superuser, is_teacher)
        await message.answer(
            "❌ Noto'g'ri buyruq.\n"
            "Iltimos, quyidagi tugmalardan foydalaning yoki /start bosing.",
            reply_markup=keyboard
        )
