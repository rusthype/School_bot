import re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from datetime import datetime

from school_bot.database.models import User, UserRole, Task
from school_bot.bot.states.book_order import BookOrderStates
from school_bot.bot.states.new_task import NewTaskStates
from school_bot.bot.states.registration import RegistrationStates
from school_bot.bot.services.profile_service import upsert_profile, can_register_again
from school_bot.bot.services.profile_service import revoke_teacher
from school_bot.bot.services.approval_service import notify_superusers_new_registration

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
    if is_teacher or is_superuser:
        builder.row(
            KeyboardButton(text="/new_task"),
            KeyboardButton(text="/order_book")
        )

    # Superuser uchun tugmalar
    if is_superuser:
        builder.row(
            KeyboardButton(text="/remove_teacher"),
            KeyboardButton(text="/list_teachers")
        )
        builder.row(
            KeyboardButton(text="/stats"),
            KeyboardButton(text="/users")
        )
        builder.row(
            KeyboardButton(text="/groups"),
            KeyboardButton(text="/add_group")
        )
        builder.row(
            KeyboardButton(text="/edit_group"),
            KeyboardButton(text="/remove_group")
        )

    # Tugmalarni sozlash
    builder.adjust(2)

    return builder.as_markup(resize_keyboard=True, input_field_placeholder="Komandani tanlang...")


@router.message(Command("start", "help"))
async def cmd_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        profile,
        is_superuser: bool = False,
        is_teacher: bool = False
) -> None:
    """Start va help komandalari"""
    print(
        f"🔍 DEBUG: cmd_start called by user {message.from_user.id}, is_superuser={is_superuser}, is_teacher={is_teacher}")

    if not (is_superuser or is_teacher):
        if profile and not profile.is_approved:
            if can_register_again(profile):
                await state.clear()
                await state.set_state(RegistrationStates.full_name)
                await message.answer(
                    "🔄 Yangi ro'yxatdan o'tish so'rovini yuborish uchun to'liq ismingizni qayta kiriting:\n\n"
                    "❌ Bekor qilish uchun /cancel bosing"
                )
                return

            await message.answer(
                "⏳ Ro'yxatdan o'tishingiz tasdiqlanishi kutilmoqda. Administrator tasdig'ini kuting."
            )
            return

        if profile is None:
            await state.clear()
            await state.set_state(RegistrationStates.full_name)
            await message.answer(
                "👤 Iltimos, to'liq ismingizni kiriting (ism va familiya):\n\n"
                "❌ Bekor qilish uchun /cancel bosing"
            )
            return

    lines: list[str] = [
        "📚 **Maktab topshiriqlari bot**",
        "",
        "Quyidagi tugmalardan foydalanishingiz mumkin:",
    ]

    if is_teacher or is_superuser:
        lines += [
            "",
            "👨‍🏫 **O'qituvchi buyruqlari:**",
            "/new_task - Yangi topshiriq",
            "/order_book - Kitob buyurtma qilish",
        ]

    if is_superuser:
        lines += [
            "",
            "👑 **Superfoydalanuvchi buyruqlari:**",
            "/remove_teacher - O'qituvchini o'chirish",
            "/list_teachers - O'qituvchilar ro'yxati",
            "/stats - Statistika",
            "/users - Foydalanuvchilar",
            "/groups - Guruhlar ro'yxati",
            "/add_group - Guruh qo'shish",
            "/edit_group - Guruhni tahrirlash",
            "/remove_group - Guruhni o'chirish",
        ]

    lines += [
        "",
        "📊 Oddiy foydalanuvchilar so'rovnomalarda qatnashishi mumkin.",
        "",
        "ℹ️ Tugmalarni bosish yoki komandalarni yozish orqali botdan foydalaning."
    ]

    keyboard = get_main_keyboard(is_superuser, is_teacher)
    await message.answer("\n".join(lines), reply_markup=keyboard)


@router.message(F.text == "/start")
async def button_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        profile,
        is_superuser: bool = False,
        is_teacher: bool = False
) -> None:
    print(f"🔍 DEBUG: button_start pressed by user {message.from_user.id}")
    await cmd_start(message, state, session, db_user, profile, is_superuser, is_teacher)


@router.message(F.text == "/help")
async def button_help(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        profile,
        is_superuser: bool = False,
        is_teacher: bool = False
) -> None:
    print(f"🔍 DEBUG: button_help pressed by user {message.from_user.id}")
    await cmd_start(message, state, session, db_user, profile, is_superuser, is_teacher)


@router.message(F.text == "/new_task")
async def button_new_task(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        profile,
        is_teacher: bool = False,
        is_superuser: bool = False
) -> None:
    print(f"🔍 DEBUG: button_new_task pressed by user {message.from_user.id}, is_teacher={is_teacher}")
    if not (is_teacher or is_superuser):
        await message.answer("⛔ Bu tugma faqat o'qituvchilar uchun.")
        return
    from school_bot.bot.handlers.teacher import cmd_new_task
    await cmd_new_task(message, state, session, profile, is_teacher or is_superuser, is_superuser)


@router.message(F.text == "/order_book")
async def button_order_book(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_teacher: bool = False,
        is_superuser: bool = False
) -> None:
    print(f"🔍 DEBUG: button_order_book pressed by user {message.from_user.id}, is_teacher={is_teacher}")
    if not (is_teacher or is_superuser):
        await message.answer("⛔ Bu tugma faqat o'qituvchilar uchun.")
        return
    from school_bot.bot.handlers.teacher import cmd_order_book
    await cmd_order_book(message, state, is_teacher or is_superuser, is_superuser)


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
        await message.answer("⛔ Bu tugma faqat superfoydalanuvchilar uchun.")
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
        await message.answer("⛔ Bu tugma faqat superfoydalanuvchilar uchun.")
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

    # Teacherni o'chirish (profile va role ni yangilash)
    await revoke_teacher(session, teacher.id)

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
        await message.answer("⛔ Bu tugma faqat superfoydalanuvchilar uchun.")
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
        await message.answer("⛔ Bu tugma faqat superfoydalanuvchilar uchun.")
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
        f"   • Superfoydalanuvchi: {superusers_count} ta",
        f"   • O'qituvchi: {teachers_count} ta",
        f"   • Oddiy foydalanuvchi: {regular_users} ta",
        "",
        f"📝 **Topshiriqlar:** {tasks_count} ta",
        ""
    ]

    if active_teachers:
        lines.append("⭐ **Eng faol o'qituvchilar:**")
        for i, teacher in enumerate(active_teachers, 1):
            teacher_name = teacher.full_name or f"Foydalanuvchi {teacher.telegram_id}"
            lines.append(f"   {i}. {teacher_name} - {teacher.task_count} ta")
    else:
        lines.append("⭐ **Eng faol o'qituvchilar:**")
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
        await message.answer("⛔ Bu tugma faqat superfoydalanuvchilar uchun.")
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
                role_emoji = "👑 Superfoydalanuvchi"
            elif user.role == UserRole.teacher:
                role_emoji = "👨‍🏫 O'qituvchi"
            else:
                role_emoji = "👤 Oddiy foydalanuvchi"
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


def _split_full_name(full_name: str) -> tuple[str | None, str | None]:
    parts = [p for p in (full_name or "").split() if p]
    if len(parts) < 2:
        return None, None
    return parts[0], " ".join(parts[1:])


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


@router.message(RegistrationStates.full_name, F.text)
async def registration_full_name(message: Message, state: FSMContext) -> None:
    first_name, last_name = _split_full_name(message.text or "")
    if not first_name or not last_name:
        await message.answer("❌ Iltimos, ism va familiyangizni kiriting. Masalan: Aziz Rahimov")
        return

    await state.update_data(first_name=first_name, last_name=last_name)
    await state.set_state(RegistrationStates.phone)
    await message.answer("📱 Telefon raqamingizni quyidagi formatda kiriting: +998901234567")


@router.message(RegistrationStates.full_name)
async def registration_full_name_invalid(message: Message) -> None:
    await message.answer("❌ Iltimos, to'liq ismingizni matn ko'rinishida yuboring. Masalan: Aziz Rahimov")


@router.message(RegistrationStates.phone, F.text)
async def registration_phone(message: Message, state: FSMContext) -> None:
    phone_input = (message.text or "").strip()
    normalized_phone = _normalize_phone_number(phone_input)
    if not normalized_phone:
        await message.answer(
            "❌ Noto'g'ri telefon raqam formati. Iltimos, quyidagi formatlardan birini kiriting: "
            "+998901234567 yoki 998901234567"
        )
        return

    await state.update_data(phone=normalized_phone)
    data = await state.get_data()
    username = f"@{message.from_user.username}" if message.from_user.username else "(foydalanuvchi nomi yo'q)"

    confirm_lines = [
        "📋 **Ro'yxatdan o'tish ma'lumotlari:**",
        f"👤 Ism: {data['first_name']} {data['last_name']}",
        f"🔹 Foydalanuvchi nomi: {username}",
        f"📱 Telefon: {normalized_phone}",
        "",
        "✅ Ro'yxatdan o'tishni tasdiqlaysizmi?",
    ]

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="reg_confirm")
    builder.button(text="❌ Bekor qilish", callback_data="reg_cancel")
    builder.adjust(2)

    await state.set_state(RegistrationStates.confirm)
    await message.answer("\n".join(confirm_lines), reply_markup=builder.as_markup())


@router.message(RegistrationStates.phone)
async def registration_phone_invalid(message: Message) -> None:
    await message.answer(
        "❌ Noto'g'ri telefon raqam formati. Iltimos, quyidagi formatlardan birini kiriting: "
        "+998901234567 yoki 998901234567"
    )


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
    )

    await state.clear()
    if profile.is_approved:
        await callback.message.edit_text("✅ Profilingiz allaqachon tasdiqlangan.")
        await callback.answer()
        return

    await callback.message.edit_text("✅ Ro'yxatdan o'tish so'rovingiz administratorga yuborildi.")
    await notify_superusers_new_registration(session=session, bot=callback.bot, profile=profile)
    await callback.answer()


@router.callback_query(lambda c: c.data == "reg_cancel")
async def registration_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Ro'yxatdan o'tish bekor qilindi. Qayta boshlash uchun /start bosing.")
    await callback.answer()


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
    if not is_superuser and not is_teacher:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="/start")]],
            resize_keyboard=True,
            input_field_placeholder="Komandani tanlang..."
        )
    else:
        keyboard = get_main_keyboard(is_superuser, is_teacher)
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.message()
async def handle_unknown_message(
        message: Message,
        state: FSMContext,
        profile,
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
        if profile is None and not is_superuser and not is_teacher:
            keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="/start")]],
                resize_keyboard=True,
                input_field_placeholder="Komandani tanlang..."
            )
        else:
            keyboard = get_main_keyboard(is_superuser, is_teacher)
        await message.answer(
            "❌ Noto'g'ri buyruq.\n"
            "Iltimos, quyidagi tugmalardan foydalaning yoki /start bosing.",
            reply_markup=keyboard
        )
