from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.handlers.common import get_main_keyboard, get_users_management_keyboard, cancel_current_action
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.utils.telegram import send_chunked_message
from school_bot.bot.services.user_service import get_or_create_user, get_user_by_username
from school_bot.bot.utils.parser import parse_telegram_input
from school_bot.bot.states.admin_states import AddAdminStates, RemoveAdminStates, EditAdminRoleStates
from school_bot.database.models import User, UserRole

router = Router(name=__name__)
logger = get_logger(__name__)

ADMIN_ROLES: dict[str, dict[str, object]] = {
    "superadmin": {"name": "👑 Superadmin", "desc": "To'liq boshqaruv huquqi", "available": True},
    "librarian": {"name": "📚 Kutubxonachi", "desc": "Kitob buyurtmalarini boshqarish", "available": True},
    "teacher_manager": {"name": "👨‍🏫 O'qituvchi boshq.", "desc": "O'qituvchilarni boshqarish (tez kunda)", "available": False},
    "group_manager": {"name": "📋 Guruh boshq.", "desc": "Guruhlarni boshqarish (tez kunda)", "available": False},
    "viewer": {"name": "👁️ Kuzatuvchi", "desc": "Faqat ko'rish huquqi (tez kunda)", "available": False},
}


def _build_role_keyboard() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="👑 Superadmin", callback_data="admin_role:superadmin")
    builder.button(text="📚 Kutubxonachi", callback_data="admin_role:librarian")
    builder.button(text="👨‍🏫 O'qituvchi boshq.", callback_data="admin_role:teacher_manager")
    builder.button(text="📋 Guruh boshq.", callback_data="admin_role:group_manager")
    builder.button(text="👁️ Kuzatuvchi", callback_data="admin_role:viewer")
    builder.button(text="❌ Bekor qilish", callback_data="admin_role:cancel")
    builder.adjust(2)
    return builder


def _format_user_display(user: User) -> str:
    username = f"@{user.username}" if user.username else ""
    name = user.full_name or "Ism yo'q"
    return f"{name} {username}".strip()


def _role_label(role: UserRole | None) -> str:
    if role == UserRole.superadmin:
        return "Superadmin"
    if role == UserRole.librarian:
        return "Kutubxonachi"
    return "Yo'q"


async def _find_user_by_input(
    session: AsyncSession,
    message: Message,
    raw_text: str,
    create_if_missing: bool,
) -> tuple[User | None, str | None, str | None]:
    parsed = parse_telegram_input(raw_text)
    if parsed is None:
        return None, None, "❌ Noto'g'ri format. Telegram ID yoki username yuboring."

    input_type, value = parsed
    if input_type == "id":
        if create_if_missing:
            user = await get_or_create_user(session, telegram_id=value, full_name=None)
        else:
            result = await session.execute(select(User).where(User.telegram_id == value))
            user = result.scalar_one_or_none()
        if not user:
            return None, None, "⚠️ Foydalanuvchi topilmadi."
        return user, _format_user_display(user), None

    username = value
    user = await get_user_by_username(session, username)
    if user:
        return user, _format_user_display(user), None

    if not create_if_missing:
        return None, None, "⚠️ Foydalanuvchi topilmadi."

    try:
        chat = await message.bot.get_chat(f"@{username}")
    except Exception:
        return None, None, f"❌ @{username} topilmadi yoki bot bilan gaplashmagan."

    full_name = getattr(chat, "full_name", None) or username
    user = await get_or_create_user(session, telegram_id=chat.id, full_name=full_name, username=username)
    return user, _format_user_display(user), None


async def _prompt_role_selection(message: Message, user: User, state: FSMContext, mode: str) -> None:
    await state.set_state(AddAdminStates.waiting_for_role)
    await state.update_data(target_user_id=user.id, target_display=_format_user_display(user), mode=mode)
    roles_lines = ["Quyidagi rollardan birini tanlang:", ""]
    for role_key, meta in ADMIN_ROLES.items():
        roles_lines.append(f"{meta['name']} - {meta['desc']}")
    text = (
        f"👤 Foydalanuvchi topildi: {_format_user_display(user)}\n\n"
        + "\n".join(roles_lines)
    )
    await message.answer(text, reply_markup=_build_role_keyboard().as_markup())


@router.message(Command("add_admin"))
async def cmd_add_admin(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    command: CommandObject | None = None,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    if command and command.args:
        raw = command.args.split()[0]
        user, _, error = await _find_user_by_input(session, message, raw, create_if_missing=True)
        if error:
            await message.answer(error)
            return
        if user:
            await _prompt_role_selection(message, user, state, mode="add")
        return

    await state.set_state(AddAdminStates.waiting_for_user)
    await message.answer(
        "👤 Admin qilmoqchi bo'lgan foydalanuvchining Telegram ID sini yoki Username ni yuboring:\n"
        "Masalan: 123456789 yoki @username\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(
    Command("cancel"),
    StateFilter(
        AddAdminStates.waiting_for_user,
        AddAdminStates.waiting_for_role,
        RemoveAdminStates.waiting_for_user,
        EditAdminRoleStates.waiting_for_user,
        EditAdminRoleStates.waiting_for_role,
    ),
)
async def admin_cancel_any_state(
    message: Message,
    state: FSMContext,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    await cancel_current_action(
        message,
        state,
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )


@router.message(AddAdminStates.waiting_for_user, F.text)
async def add_admin_waiting_user(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    raw_text = (message.text or "").strip()
    if raw_text.startswith("/"):
        return
    user, _, error = await _find_user_by_input(session, message, raw_text, create_if_missing=True)
    if error:
        await message.answer(error)
        return
    if user:
        await _prompt_role_selection(message, user, state, mode="add")


@router.message(Command("remove_admin"))
async def cmd_remove_admin(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    command: CommandObject | None = None,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    if command and command.args:
        raw = command.args.split()[0]
        await _process_remove_admin(message, state, session, raw, is_superadmin)
        return
    await state.clear()
    await _show_admin_remove_list(message, session)


@router.message(RemoveAdminStates.waiting_for_user, F.text)
async def remove_admin_waiting_user(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    await _process_remove_admin(message, state, session, message.text or "", is_superadmin)


async def _process_remove_admin(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    raw_text: str,
    is_superadmin: bool,
) -> None:
    user, display, error = await _find_user_by_input(session, message, raw_text, create_if_missing=False)
    if error:
        await message.answer(error)
        return

    if not user:
        await message.answer("⚠️ Foydalanuvchi topilmadi.")
        return

    if user.role not in (UserRole.superadmin, UserRole.librarian):
        await message.answer("ℹ️ Bu foydalanuvchi admin emas.")
        await state.clear()
        return

    user.role = None
    await session.commit()
    await state.clear()
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=False)
    await message.answer(
        "✅ Admin olib tashlandi.\n\n"
        f"👤 Foydalanuvchi: {display}\n"
        f"🆔 ID: {user.telegram_id}",
        reply_markup=keyboard,
    )


async def _show_admin_remove_list(message: Message, session: AsyncSession) -> None:
    result = await session.execute(
        select(User).where(User.role.in_([UserRole.superadmin, UserRole.librarian])).order_by(User.role, User.created_at)
    )
    admins = list(result.scalars().all())
    if not admins:
        await message.answer("📭 Hozircha adminlar yo'q.")
        return

    superadmins = [u for u in admins if u.role == UserRole.superadmin]
    librarians = [u for u in admins if u.role == UserRole.librarian]

    role_info = {
        UserRole.superadmin: {
            "emoji": "👑",
            "title": "Superadmin",
            "deletable": False,
        },
        UserRole.librarian: {
            "emoji": "📚",
            "title": "Kutubxonachi",
            "deletable": True,
        },
    }

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "👑 ADMIN O'CHIRISH",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "O'chirmoqchi bo'lgan adminingizni tanlang:",
        "",
        f"📊 Jami adminlar: {len(admins)} ta ({len(superadmins)} superadmin, {len(librarians)} kutubxonachi)",
    ]

    builder = InlineKeyboardBuilder()
    for admin in admins:
        display_name = f"@{admin.username}" if admin.username else str(admin.telegram_id)
        info = role_info.get(
            admin.role,
            {"emoji": "👤", "title": "Admin", "deletable": True},
        )

        if not info["deletable"]:
            button_text = f"{info['emoji']} {display_name} · {info['title']} (o'chirib bo'lmaydi)"
            builder.button(text=button_text, callback_data="delete_admin:self_blocked")
        else:
            button_text = f"{info['emoji']} {display_name} · {info['title']}"
            builder.button(text=button_text, callback_data=f"delete_admin:select:{admin.id}")
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="🔙 Orqaga", callback_data="delete_admin:back"),
        InlineKeyboardButton(text="🏠 Bosh menyu", callback_data="delete_admin:home"),
    )

    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(F.data == "delete_admin:self_blocked")
async def admin_remove_blocked(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    await callback.answer("⛔ Superadminni o'chirib bo'lmaydi.", show_alert=True)


@router.callback_query(F.data.startswith("delete_admin:select:"))
async def admin_remove_select(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return

    admin_id = int(callback.data.split(":")[2])
    admin = await session.get(User, admin_id)
    if not admin:
        await callback.answer("❌ Admin topilmadi.", show_alert=True)
        return

    if admin.role == UserRole.superadmin:
        await callback.answer("⛔ Superadminni o'chirib bo'lmaydi.", show_alert=True)
        return

    username = f"@{admin.username}" if admin.username else "(username yo'q)"
    created = admin.created_at.strftime("%d.%m.%Y") if admin.created_at else "Noma'lum"

    text = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ TASDIQLASH\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👨‍💼 Admin: {username}\n"
        f"🆔 ID: {admin.telegram_id}\n"
        f"📌 Rol: Admin\n"
        f"📅 Qo'shilgan: {created}\n\n"
        "🔴 DIQQAT!\n"
        "Bu adminni o'chirsangiz:\n"
        "• Barcha ruxsatlari yo'qoladi\n"
        "• Qayta tiklanmaydi\n"
        "• Admin sifatida kira olmaydi\n"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, o'chirish", callback_data=f"delete_admin:confirm:{admin.id}")
    builder.button(text="❌ Bekor qilish", callback_data="delete_admin:cancel")
    builder.adjust(2)

    await callback.message.answer(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("delete_admin:confirm:"))
async def admin_remove_confirm(callback: CallbackQuery, session: AsyncSession, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return

    admin_id = int(callback.data.split(":")[2])
    admin = await session.get(User, admin_id)
    if not admin:
        await callback.answer("❌ Admin topilmadi.", show_alert=True)
        return

    if admin.role != UserRole.librarian:
        await callback.answer("⛔ Faqat adminlarni o'chirish mumkin.", show_alert=True)
        return

    admin.role = None
    await session.commit()

    try:
        await callback.bot.send_message(
            admin.telegram_id,
            "⚠️ Siz adminlikdan olib tashlandingiz.",
        )
    except Exception:
        pass

    await callback.message.answer("✅ Admin muvaffaqiyatli o'chirildi.")
    await _show_admin_remove_list(callback.message, session)
    await callback.answer()


@router.callback_query(F.data == "delete_admin:cancel")
async def admin_remove_cancel(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("✅ Bekor qilindi", show_alert=False)
    await _show_admin_remove_list(callback.message, session)


@router.callback_query(F.data == "delete_admin:back")
async def admin_remove_back(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    await callback.message.answer(
        "👥 Foydalanuvchilar boshqaruvi",
        reply_markup=get_users_management_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "delete_admin:home")
async def admin_remove_home(callback: CallbackQuery, is_superadmin: bool = False) -> None:
    if not is_superadmin:
        await callback.answer()
        return
    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await callback.message.answer("🏠 Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.message(Command("edit_admin_role"))
async def cmd_edit_admin_role(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    command: CommandObject | None = None,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    if command and command.args:
        raw = command.args.split()[0]
        await _process_edit_admin(message, state, session, raw)
        return

    await state.set_state(EditAdminRoleStates.waiting_for_user)
    await message.answer(
        "👤 Admin rolini o'zgartirmoqchi bo'lgan foydalanuvchining Telegram ID sini yoki Username ni yuboring:\n"
        "Masalan: 123456789 yoki @username\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(EditAdminRoleStates.waiting_for_user, F.text)
async def edit_admin_waiting_user(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    await _process_edit_admin(message, state, session, message.text or "")


async def _process_edit_admin(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    raw_text: str,
) -> None:
    user, _, error = await _find_user_by_input(session, message, raw_text, create_if_missing=False)
    if error:
        await message.answer(error)
        return

    if not user:
        await message.answer("⚠️ Foydalanuvchi topilmadi.")
        return

    await state.set_state(EditAdminRoleStates.waiting_for_role)
    await state.update_data(target_user_id=user.id, target_display=_format_user_display(user), mode="edit")

    current_role = _role_label(user.role)
    text = (
        f"👤 Foydalanuvchi topildi: {_format_user_display(user)}\n"
        f"📌 Hozirgi rol: {current_role}\n\n"
        "Quyidagi rollardan birini tanlang:"
    )
    await message.answer(text, reply_markup=_build_role_keyboard().as_markup())


@router.callback_query(lambda c: c.data.startswith("admin_role:"))
async def admin_role_select(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    role_key = callback.data.split(":", 1)[1]
    if role_key == "cancel":
        await state.clear()
        keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher, is_librarian=is_librarian)
        await callback.message.edit_text("❌ Jarayon bekor qilindi.")
        await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
        await callback.answer()
        return

    role_meta = ADMIN_ROLES.get(role_key)
    if not role_meta or not role_meta.get("available"):
        await callback.answer("ℹ️ Bu rol hozircha mavjud emas.", show_alert=True)
        return

    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    if not target_user_id:
        await callback.answer("❌ Foydalanuvchi topilmadi.", show_alert=True)
        return

    user = await session.get(User, target_user_id)
    if not user:
        await callback.answer("❌ Foydalanuvchi topilmadi.", show_alert=True)
        return

    new_role = UserRole.superadmin if role_key == "superadmin" else UserRole.librarian
    if user.role == new_role:
        await callback.answer("ℹ️ Bu rol allaqachon berilgan.", show_alert=True)
        return

    user.role = new_role
    await session.commit()
    await state.clear()

    role_name = ADMIN_ROLES[role_key]["name"]
    role_desc = ADMIN_ROLES[role_key]["desc"]
    action_title = "✅ Admin qo'shildi!" if data.get("mode") == "add" else "✅ Admin roli yangilandi!"
    text = (
        f"{action_title}\n\n"
        f"👤 Foydalanuvchi: {_format_user_display(user)}\n"
        f"🆔 ID: {user.telegram_id}\n"
        f"📌 Rol: {role_name}\n"
        f"📚 Ruxsatlar: {role_desc}"
    )
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher, is_librarian=is_librarian)
    await callback.message.edit_text(text)
    await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.message(Command("list_admins"))
async def cmd_list_admins(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    result = await session.execute(select(User).where(User.role.in_([UserRole.superadmin, UserRole.librarian])))
    admins = list(result.scalars().all())
    if not admins:
        await message.answer("📭 Hozircha adminlar yo'q.")
        return

    lines = ["👥 **Adminlar ro'yxati:**", ""]
    for admin in admins:
        username = f"@{admin.username}" if admin.username else "(foydalanuvchi nomi yo'q)"
        role = "Superadmin" if admin.role == UserRole.superadmin else "Kutubxonachi"
        lines.append(f"• {admin.full_name or admin.telegram_id} {username} - {role}")

    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await message.answer("\n".join(lines), reply_markup=keyboard)
