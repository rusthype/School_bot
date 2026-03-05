from __future__ import annotations

import re
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from school_bot.bot.services.user_service import remove_teacher_role, set_teacher_role
from school_bot.database.models import User, UserRole, Task
from school_bot.bot.handlers.common import get_main_keyboard

router = Router(name=__name__)


class AddTeacherStates(StatesGroup):
    waiting_for_input = State()


class RemoveTeacherStates(StatesGroup):
    waiting_for_selection = State()


def _parse_telegram_input(text: str) -> tuple[str, str | int] | None:
    """
    Telegram username yoki ID ni parse qilish
    Returns: (type, value)  # type: "id" yoki "username"
    """
    if not text:
        return None

    text = text.strip()

    # 1. Agar @ bilan boshlangan bo'lsa - bu username
    if text.startswith('@'):
        username = text[1:]  # @ belgisini olib tashlash
        # Username harflar, raqamlar va _ dan iborat bo'lishi mumkin (5-32 belgi)
        if re.match(r'^[a-zA-Z0-9_]{5,32}$', username):
            return ("username", username)
        else:
            return None

    # 2. Agar butunlay raqam bo'lsa - bu ID
    if text.isdigit():
        return ("id", int(text))

    # 3. Agar harflar va raqamlar bo'lsa (lekin @ belgisiz) - bu username
    # Username tarkibida kamida bitta harf bo'lishi kerak
    if re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', text):
        return ("username", text)

    # 4. Agar raqam va @ belgisi aralashgan bo'lsa - xato
    return None


# Umumiy cancel handler - har qanday admin state dan chiqish uchun
@router.message(Command("cancel"), StateFilter(AddTeacherStates, RemoveTeacherStates))
async def cmd_cancel_admin(message: Message, state: FSMContext, is_superuser: bool = False) -> None:
    """Admin state laridan chiqish"""
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=True, is_teacher=False)
    await message.answer(
        "✅ Jarayon bekor qilindi.\n"
        "Boshqa komandalardan foydalanishingiz mumkin.",
        reply_markup=keyboard
    )


# ============== ADD TEACHER ==============
@router.message(Command("add_teacher"))
async def cmd_add_teacher_start(
        message: Message,
        state: FSMContext,
        is_superuser: bool = False,
) -> None:
    print(f"🔍 DEBUG: add_teacher_start called by user {message.from_user.id}, is_superuser={is_superuser}")

    if not is_superuser:
        await message.answer("⛔ Siz o'qituvchilarni boshqarish huquqiga ega emassiz.")
        return

    await state.set_state(AddTeacherStates.waiting_for_input)
    current_state = await state.get_state()
    print(f"🔍 DEBUG: State set to {current_state}")

    await message.answer(
        "👤 O'qituvchi qilmoqchi bo'lgan foydalanuvchining Telegram ID sini yoki Username ni yuboring:\n"
        "Masalan: 123456789 yoki @username yoki username\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(StateFilter(AddTeacherStates.waiting_for_input))
async def cmd_add_teacher_process(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    """ID yoki username ni qayta ishlash"""

    print(f"🔍 DEBUG: add_teacher_process called with message: '{message.text}'")
    print(f"🔍 DEBUG: Message type: {message.content_type}")
    current_state = await state.get_state()
    print(f"🔍 DEBUG: Current state: {current_state}")

    # Xabarni tekshirish
    if not message.text:
        print("🔍 DEBUG: No text in message")
        await message.answer("❌ Iltimos, matn yuboring.")
        return

    parsed = _parse_telegram_input(message.text)
    print(f"🔍 DEBUG: Parsed result: {parsed}")

    if parsed is None:
        await message.answer(
            "❌ Noto'g'ri format. Iltimos, Telegram ID yoki Username yuboring:\n"
            "Masalan: 123456789 (ID) yoki @username yoki username (Username)\n\n"
            "❌ Bekor qilish uchun /cancel bosing"
        )
        return

    input_type, value = parsed
    result_message = ""

    if input_type == "id":
        tg_id = value
        print(f"🔍 DEBUG: Adding teacher with ID: {tg_id}")
        changed, user = await set_teacher_role(session=session, telegram_id=tg_id)
        if changed:
            result_message = f"✅ O'qituvchi qo'shildi: {user.telegram_id}"
            print(f"🔍 DEBUG: Teacher added successfully: {user.telegram_id}")
        else:
            result_message = f"ℹ️ Bu foydalanuvchi allaqachon o'qituvchi: {user.telegram_id}"
            print(f"🔍 DEBUG: User already teacher: {user.telegram_id}")

    else:  # username
        username = value
        print(f"🔍 DEBUG: Adding teacher with username: @{username}")
        try:
            # Username dan @ ni qo'shish
            chat = await message.bot.get_chat(f"@{username}")
            tg_id = chat.id
            user_full_name = chat.full_name if hasattr(chat, 'full_name') else username
            print(f"🔍 DEBUG: Found chat ID: {tg_id}, Name: {user_full_name}")

            # Foydalanuvchini bazaga qo'shish/ Yangilash
            from school_bot.bot.services.user_service import get_or_create_user
            user = await get_or_create_user(
                session=session,
                telegram_id=tg_id,
                full_name=user_full_name
            )

            # Teacher rolini berish
            changed, user = await set_teacher_role(session=session, telegram_id=tg_id)
            if changed:
                result_message = f"✅ O'qituvchi qo'shildi: @{username} (ID: {user.telegram_id})"
                print(f"🔍 DEBUG: Teacher added successfully: @{username}")
            else:
                result_message = f"ℹ️ Bu foydalanuvchi allaqachon o'qituvchi: @{username}"
                print(f"🔍 DEBUG: User already teacher: @{username}")
        except Exception as e:
            print(f"🔍 DEBUG: Error finding username: {e}")
            result_message = f"❌ @{username} topilmadi yoki bot bilan gaplashmagan. Sabab: {str(e)}"

    await state.clear()
    print(f"🔍 DEBUG: State cleared")

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


# ============== REMOVE TEACHER ==============
@router.message(Command("remove_teacher"))
async def cmd_remove_teacher_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
) -> None:
    """O'qituvchini o'chirish - tanlash usuli"""

    print(f"🔍 DEBUG: remove_teacher_start called by user {message.from_user.id}")

    # db_user orqali superuserligini tekshirish
    is_superuser = (db_user.role == UserRole.superuser)
    print(f"🔍 DEBUG: is_superuser from db_user: {is_superuser}, role: {db_user.role}")

    if not is_superuser:
        await message.answer("⛔ Siz o'qituvchilarni boshqarish huquqiga ega emassiz.")
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

    await state.set_state(RemoveTeacherStates.waiting_for_selection)
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


# ============== LIST TEACHERS ==============
@router.message(Command("list_teachers"))
async def cmd_list_teachers(
        message: Message,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superuserlar uchun.")
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


# ============== USERS ==============
@router.message(Command("users"))
async def cmd_users(
        message: Message,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    """Barcha foydalanuvchilar ro'yxati (faqat superuser)"""
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superuserlar uchun.")
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


# ============== STATS ==============
@router.message(Command("stats"))
async def cmd_stats(
        message: Message,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    """Bot statistikasi (faqat superuser uchun)"""
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superuserlar uchun.")
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


# ============== REMOVE SUPERUSER ==============
@router.message(Command("remove_superuser"))
async def cmd_remove_superuser(
        message: Message,
        command: CommandObject,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superuserlar uchun.")
        return

    parsed = _parse_telegram_input(command.args)
    if parsed is None or parsed[0] != "id":
        await message.answer("Ishlatilishi: /remove_superuser [telegram_id]")
        return

    tg_id = parsed[1]

    result = await session.execute(
        select(User).where(User.telegram_id == tg_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        result_message = "⚠️ Foydalanuvchi topilmadi."
    elif user.role != UserRole.superuser:
        result_message = f"ℹ️ Bu foydalanuvchi superuser emas: {tg_id}"
    else:
        user.role = None
        await session.commit()
        result_message = f"✅ Superuser olib tashlandi: {tg_id}"

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)
