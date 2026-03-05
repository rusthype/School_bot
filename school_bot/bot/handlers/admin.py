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
from school_bot.bot.services.group_service import (
    list_groups,
    add_group,
    get_group_by_id,
    get_group_by_name,
    get_group_by_chat_id,
    update_group,
    remove_group,
)
from school_bot.bot.services.profile_service import get_profile_by_id, approve_profile, reject_profile, revoke_teacher
from school_bot.bot.services.approval_service import (
    build_approval_keyboard,
    get_selected_group_ids,
    toggle_selected_group,
    clear_selections_for_profile,
)
from school_bot.bot.states.group_management import GroupManagementStates
from school_bot.database.models import User, UserRole, Task
from school_bot.bot.handlers.common import get_main_keyboard

router = Router(name=__name__)


class AddTeacherStates(StatesGroup):
    waiting_for_input = State()


class RemoveTeacherStates(StatesGroup):
    waiting_for_selection = State()


class RejectTeacherStates(StatesGroup):
    waiting_reason = State()


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
@router.message(Command("cancel"),
                StateFilter(AddTeacherStates, RemoveTeacherStates, GroupManagementStates, RejectTeacherStates))
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


@router.callback_query(lambda c: c.data.startswith("approve_toggle:"))
async def approval_toggle_group(
        callback: CallbackQuery,
        session: AsyncSession,
        db_user,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await callback.answer("⛔ Tasdiqlash faqat superfoydalanuvchilar uchun.", show_alert=True)
        return

    try:
        _, profile_id_str, group_id_str = callback.data.split(":")
        profile_id = int(profile_id_str)
        group_id = int(group_id_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    groups = await list_groups(session)
    selected = toggle_selected_group(db_user.id, profile_id, group_id)
    keyboard = build_approval_keyboard(profile_id, groups, selected)

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("approve_confirm:"))
async def approval_confirm(
        callback: CallbackQuery,
        session: AsyncSession,
        db_user,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await callback.answer("⛔ Tasdiqlash faqat superfoydalanuvchilar uchun.", show_alert=True)
        return

    try:
        profile_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.message.edit_text("❌ Ro'yxatdan o'tish profili topilmadi.")
        await callback.answer()
        return
    if profile.is_approved:
        await callback.answer("ℹ️ Bu o'qituvchi allaqachon tasdiqlangan.", show_alert=True)
        return

    selected_ids = get_selected_group_ids(db_user.id, profile_id)
    if not selected_ids:
        await callback.answer("Kamida bitta guruhni tanlang.", show_alert=True)
        return

    groups = await list_groups(session)
    selected_groups = [g for g in groups if g.id in selected_ids]
    if not selected_groups:
        await callback.answer("Tanlangan guruhlar topilmadi.", show_alert=True)
        return
    assigned_names = [g.name for g in selected_groups]

    await approve_profile(session, profile, db_user.id, assigned_names)
    clear_selections_for_profile(profile_id)

    user = await session.get(User, profile.user_id)
    if user:
        assigned_str = ", ".join(assigned_names)
        await callback.bot.send_message(
            chat_id=user.telegram_id,
            text=f"🎉 Tabriklaymiz! Ro'yxatdan o'tishingiz tasdiqlandi. Sizga biriktirilgan guruhlar: {assigned_str}",
        )

    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()
    await callback.message.edit_text(f"✅ Tasdiqlandi: {full_name}\nGuruhlar: {', '.join(assigned_names)}")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("approve_reject:"))
async def approval_reject_start(
        callback: CallbackQuery,
        state: FSMContext,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await callback.answer("⛔ Rad etish faqat superfoydalanuvchilar uchun.", show_alert=True)
        return

    try:
        profile_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    await state.set_state(RejectTeacherStates.waiting_reason)
    await state.update_data(
        profile_id=profile_id,
        admin_chat_id=callback.message.chat.id,
        admin_message_id=callback.message.message_id,
    )
    await callback.message.answer("❌ Rad etish sababini yuboring yoki sababsiz rad etish uchun /skip bosing.")
    await callback.answer()


async def _perform_reject(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        reason: str | None,
) -> None:
    data = await state.get_data()
    profile_id = data.get("profile_id")
    admin_chat_id = data.get("admin_chat_id")
    admin_message_id = data.get("admin_message_id")

    profile = await get_profile_by_id(session, int(profile_id)) if profile_id else None
    if not profile:
        await message.answer("❌ Ro'yxatdan o'tish profili topilmadi.")
        await state.clear()
        return

    user = await session.get(User, profile.user_id)
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()

    await reject_profile(session, profile)
    clear_selections_for_profile(profile.id)

    if user:
        reason_text = f"Sabab: {reason}" if reason else "Sabab: ko'rsatilmagan"
        await message.bot.send_message(
            chat_id=user.telegram_id,
            text=f"❌ Ro'yxatdan o'tish so'rovingiz rad etildi. {reason_text}",
        )

    if admin_chat_id and admin_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=admin_message_id,
                text=f"❌ Rad etildi: {full_name}",
            )
        except Exception:
            pass

    await state.clear()
    await message.answer("✅ Rad etish yakunlandi.")


@router.message(RejectTeacherStates.waiting_reason, Command("skip"))
async def approval_reject_skip(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    await _perform_reject(message, state, session, reason=None)


@router.message(RejectTeacherStates.waiting_reason, F.text)
async def approval_reject_reason(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    reason = (message.text or "").strip()
    await _perform_reject(message, state, session, reason=reason or None)


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

    await message.answer(
        "ℹ️ Bu buyruq eskirgan. O'qituvchilar /start orqali ro'yxatdan o'tib, tasdiqni kutishlari kerak."
    )
    return

    await state.set_state(AddTeacherStates.waiting_for_input)
    current_state = await state.get_state()
    print(f"🔍 DEBUG: State set to {current_state}")

    await message.answer(
        "👤 O'qituvchi qilmoqchi bo'lgan foydalanuvchining Telegram ID sini yoki foydalanuvchi nomini yuboring:\n"
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
            "❌ Noto'g'ri format. Iltimos, Telegram ID yoki foydalanuvchi nomini yuboring:\n"
            "Masalan: 123456789 (ID) yoki @username yoki username (foydalanuvchi nomi)\n\n"
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


# ============== GROUP MANAGEMENT ==============
@router.message(Command("groups"))
async def cmd_groups(
        message: Message,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
        return

    groups = await list_groups(session)
    if not groups:
        await message.answer("📭 Hozircha hech qanday guruh yo'q. /add_group bilan qo'shing.")
        return

    lines = ["📚 **Mavjud guruhlar:**", ""]
    for group in groups:
        lines.append(f"• {group.name} — {group.chat_id}")
    await message.answer("\n".join(lines))


@router.message(Command("add_group"))
async def cmd_add_group_start(
        message: Message,
        state: FSMContext,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
        return

    await state.set_state(GroupManagementStates.add_name)
    await message.answer("🆕 Guruh nomini kiriting (masalan: 7-A):\n\n❌ Bekor qilish uchun /cancel bosing")


@router.message(GroupManagementStates.add_name, F.text)
async def cmd_add_group_name(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Guruh nomi bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    existing = await get_group_by_name(session, name)
    if existing:
        await message.answer("⚠️ Bu nomdagi guruh allaqachon mavjud. Boshqa nom kiriting:")
        return

    await state.update_data(group_name=name)
    await state.set_state(GroupManagementStates.add_chat_id)
    await message.answer("🔗 Guruh chat ID sini kiriting (masalan: -1001234567890):")


@router.message(GroupManagementStates.add_chat_id, F.text)
async def cmd_add_group_chat_id(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    try:
        chat_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri chat ID. Qayta kiriting:")
        return

    existing = await get_group_by_chat_id(session, chat_id)
    if existing:
        await message.answer("⚠️ Bu chat ID allaqachon boshqa guruhga biriktirilgan.")
        await state.clear()
        return

    data = await state.get_data()
    group = await add_group(session, name=data["group_name"], chat_id=chat_id)
    await state.clear()

    await message.answer(f"✅ Guruh qo'shildi: {group.name} ({group.chat_id})")


@router.message(Command("edit_group"))
async def cmd_edit_group_start(
        message: Message,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
        return

    groups = await list_groups(session)
    if not groups:
        await message.answer("📭 Guruh topilmadi. /add_group bilan qo'shing.")
        return

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"group_edit:{group.id}")
    builder.adjust(1)

    await message.answer("✏️ Qaysi guruhni tahrirlaysiz?", reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("group_edit:"))
async def cmd_edit_group_select(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await callback.answer("⛔ Faqat superfoydalanuvchilar uchun.", show_alert=True)
        return

    try:
        group_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri guruh.", show_alert=True)
        return

    group = await get_group_by_id(session, group_id)
    if not group:
        await callback.answer("❌ Guruh topilmadi.", show_alert=True)
        return

    await state.set_state(GroupManagementStates.edit_name)
    await state.update_data(group_id=group.id)
    await callback.message.answer(
        f"✏️ Yangi guruh nomini kiriting (hozirgi: {group.name}) yoki /skip bosing:"
    )
    await callback.answer()


@router.message(GroupManagementStates.edit_name, Command("skip"))
async def cmd_edit_group_skip_name(message: Message, state: FSMContext) -> None:
    await state.update_data(new_name=None)
    await state.set_state(GroupManagementStates.edit_chat_id)
    await message.answer("🔗 Yangi chat ID kiriting yoki /skip bosing:")


@router.message(GroupManagementStates.edit_name, F.text)
async def cmd_edit_group_name(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Guruh nomi bo'sh bo'lishi mumkin emas.")
        return

    existing = await get_group_by_name(session, name)
    if existing:
        await message.answer("⚠️ Bu nomdagi guruh allaqachon mavjud.")
        return

    await state.update_data(new_name=name)
    await state.set_state(GroupManagementStates.edit_chat_id)
    await message.answer("🔗 Yangi chat ID kiriting yoki /skip bosing:")


@router.message(GroupManagementStates.edit_chat_id, Command("skip"))
async def cmd_edit_group_skip_chat_id(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    data = await state.get_data()
    group = await get_group_by_id(session, data["group_id"])
    if not group:
        await message.answer("❌ Guruh topilmadi.")
        await state.clear()
        return

    if data.get("new_name") is None:
        await message.answer("ℹ️ O'zgarish yo'q.")
        await state.clear()
        return

    await update_group(session, group, name=data.get("new_name"))
    await state.clear()
    await message.answer(f"✅ Guruh yangilandi: {group.name}")


@router.message(GroupManagementStates.edit_chat_id, F.text)
async def cmd_edit_group_chat_id(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    data = await state.get_data()
    group = await get_group_by_id(session, data["group_id"])
    if not group:
        await message.answer("❌ Guruh topilmadi.")
        await state.clear()
        return

    try:
        chat_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri chat ID. Qayta kiriting:")
        return

    existing = await get_group_by_chat_id(session, chat_id)
    if existing and existing.id != group.id:
        await message.answer("⚠️ Bu chat ID boshqa guruhga biriktirilgan.")
        return

    await update_group(session, group, name=data.get("new_name"), chat_id=chat_id)
    await state.clear()
    await message.answer(f"✅ Guruh yangilandi: {group.name} ({group.chat_id})")


@router.message(Command("remove_group"))
async def cmd_remove_group_start(
        message: Message,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
        return

    groups = await list_groups(session)
    if not groups:
        await message.answer("📭 Guruh topilmadi.")
        return

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=f"🗑️ {group.name}", callback_data=f"group_remove:{group.id}")
    builder.adjust(1)

    await message.answer("🗑️ Qaysi guruhni o'chirasiz?", reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("group_remove:"))
async def cmd_remove_group_confirm(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await callback.answer("⛔ Faqat superfoydalanuvchilar uchun.", show_alert=True)
        return

    try:
        group_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri guruh.", show_alert=True)
        return

    group = await get_group_by_id(session, group_id)
    if not group:
        await callback.answer("❌ Guruh topilmadi.", show_alert=True)
        return

    await remove_group(session, group)
    await callback.message.edit_text(f"✅ Guruh o'chirildi: {group.name}")
    await callback.answer()


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

    # Teacherni o'chirish (profile va role ni yangilash)
    await revoke_teacher(session, teacher.id)

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
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
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
    """Barcha foydalanuvchilar ro'yxati (faqat superfoydalanuvchi)"""
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
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
    """Bot statistikasi (faqat superfoydalanuvchi uchun)"""
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
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
        f"   • Oddiy user: {regular_users} ta",
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


# ============== REMOVE SUPERUSER ==============
@router.message(Command("remove_superuser"))
async def cmd_remove_superuser(
        message: Message,
        command: CommandObject,
        session: AsyncSession,
        is_superuser: bool = False,
) -> None:
    if not is_superuser:
        await message.answer("⛔ Bu komanda faqat superfoydalanuvchilar uchun.")
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
        result_message = f"ℹ️ Bu foydalanuvchi superfoydalanuvchi emas: {tg_id}"
    else:
        user.role = None
        await session.commit()
        result_message = f"✅ Superfoydalanuvchi olib tashlandi: {tg_id}"

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)
