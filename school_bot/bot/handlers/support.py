from __future__ import annotations

from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.handlers.common import get_cancel_keyboard, get_main_keyboard
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.states.support_states import SupportStates
from school_bot.database.models import Profile, School, SupportTicket, User, UserRole

router = Router(name=__name__)
logger = get_logger(__name__)


def _format_role(role: UserRole | None) -> str:
    if role == UserRole.superadmin:
        return "Superadmin"
    if role == UserRole.teacher:
        return "O'qituvchi"
    if role == UserRole.librarian:
        return "Kutubxonachi"
    return "Foydalanuvchi"


def _format_user_name(user: User, profile: Profile | None) -> str:
    if profile:
        if profile.first_name and profile.last_name:
            return f"{profile.first_name} {profile.last_name}"
        if profile.first_name:
            return profile.first_name
    return user.full_name or "Noma'lum"


@router.message(F.text == "📞 Admin bilan bog'lanish")
@router.message(Command("support"))
async def cmd_contact_admin(
    message: Message,
    state: FSMContext,
    db_user: User,
    profile: Profile | None,
    is_superadmin: bool = False,
) -> None:
    if is_superadmin:
        await message.answer("⛔ Siz admin sifatida o'zingizga murojaat qila olmaysiz.")
        return
    await state.set_state(SupportStates.waiting_for_message)
    await state.set_data({})
    await state.update_data(user_id=db_user.id, support_submitted=False)

    await message.answer(
        "📝 <b>Admin bilan bog'lanish</b>\n\n"
        "Iltimos, murojaatingizni yozib qoldiring. Adminlarimiz tez orada sizga javob beradi.\n\n"
        "Masalan: Kitob buyurtma qilolmayapman, yordam kerak.\n\n"
        "❌ Bekor qilish uchun /cancel bosing",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(SupportStates.waiting_for_message, F.text)
async def process_support_message(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    profile: Profile | None,
) -> None:
    ticket_message = (message.text or "").strip()
    if ticket_message in {"/cancel", "cancel", "❌ Bekor qilish"}:
        await state.clear()
        keyboard = get_main_keyboard(
            is_superadmin=db_user.role == UserRole.superadmin,
            is_teacher=db_user.role == UserRole.teacher,
            is_librarian=db_user.role == UserRole.librarian,
        )
        await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)
        return
    if not ticket_message:
        await message.answer("❌ Murojaat matni bo'sh bo'lishi mumkin emas. Qayta yozing:")
        return

    data = await state.get_data()
    if data.get("support_submitted"):
        return
    await state.update_data(support_submitted=True)

    max_number = await session.scalar(select(func.max(SupportTicket.ticket_number)))
    ticket_number = (max_number or 9999) + 1

    ticket = SupportTicket(
        ticket_number=ticket_number,
        user_id=db_user.id,
        message=ticket_message,
        status="open",
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)

    school_name = None
    groups_text = None
    if profile and profile.school_id:
        school = await session.get(School, profile.school_id)
        if school:
            school_name = school.name
    if profile and profile.assigned_groups:
        groups_text = ", ".join(profile.assigned_groups)

    admin_lines = [
        f"📩 <b>YANGI MUROJAAT</b> (#{ticket_number})",
        "",
        "👤 <b>Foydalanuvchi ma'lumotlari:</b>",
        f"🆔 ID: {db_user.telegram_id}",
        f"👤 Ism: {_format_user_name(db_user, profile)}",
    ]
    if db_user.username:
        admin_lines.append(f"🔹 Username: @{db_user.username}")
    if profile and profile.phone:
        admin_lines.append(f"📱 Telefon: {profile.phone}")
    if school_name:
        admin_lines.append(f"🏫 Maktab: {school_name}")
    if groups_text:
        admin_lines.append(f"📚 Guruhlar: {groups_text}")
    admin_lines.extend(
        [
            f"👥 Rol: {_format_role(db_user.role)}",
            "",
            "📝 <b>Murojaat:</b>",
            ticket_message,
            "",
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            "",
            f"💬 Javob berish uchun: /reply {ticket_number} [matn]",
        ]
    )

    admin_result = await session.execute(
        select(User).where(User.role == UserRole.superadmin)
    )
    admins = admin_result.scalars().all()

    sent_count = 0
    for admin in admins:
        try:
            await message.bot.send_message(chat_id=admin.telegram_id, text="\n".join(admin_lines))
            sent_count += 1
        except Exception:
            logger.error(
                "Support ticket failed to send",
                exc_info=True,
                extra={"admin_id": admin.telegram_id, "ticket_number": ticket_number},
            )

    await message.answer(
        f"✅ Murojaatingiz qabul qilindi! (№{ticket_number})\n"
        "Adminlarimiz tez orada siz bilan bog'lanadi.\n\n"
        "📊 Holat: ⏳ Kutilmoqda"
    )

    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=db_user.role == UserRole.superadmin,
        is_teacher=db_user.role == UserRole.teacher,
        is_librarian=db_user.role == UserRole.librarian,
    )
    await message.answer("🏠 Asosiy menyu", reply_markup=keyboard)


@router.message(SupportStates.waiting_for_message, Command("cancel"))
@router.message(SupportStates.waiting_for_message, F.text == "❌ Bekor qilish")
async def cancel_support(
    message: Message,
    state: FSMContext,
    db_user: User,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=db_user.role == UserRole.superadmin,
        is_teacher=db_user.role == UserRole.teacher,
        is_librarian=db_user.role == UserRole.librarian,
    )
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.message(Command("reply"))
async def cmd_reply_ticket(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat adminlar uchun.")
        return

    if not command.args:
        await message.answer(
            "❌ Iltimos, ticket raqami va javob matnini yozing.\n"
            "Masalan: /reply 12345 Muammo hal qilindi"
        )
        return

    parts = command.args.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Noto'g'ri format. Masalan: /reply 12345 Muammo hal qilindi")
        return

    try:
        ticket_number = int(parts[0])
    except ValueError:
        await message.answer("❌ Ticket raqami noto'g'ri.")
        return

    reply_text = parts[1].strip()
    if not reply_text:
        await message.answer("❌ Javob matni bo'sh bo'lishi mumkin emas.")
        return

    ticket_result = await session.execute(
        select(SupportTicket).where(SupportTicket.ticket_number == ticket_number)
    )
    ticket = ticket_result.scalar_one_or_none()

    if not ticket:
        await message.answer(f"❌ #{ticket_number} raqamli ticket topilmadi.")
        return

    ticket.status = "replied"
    ticket.admin_reply = reply_text
    ticket.replied_by = db_user.id
    ticket.replied_at = datetime.now()
    await session.commit()

    user = await session.get(User, ticket.user_id)
    if not user:
        await message.answer("❌ Foydalanuvchi topilmadi.")
        return

    try:
        await message.bot.send_message(
            chat_id=user.telegram_id,
            text=(
                f"📬 <b>Admin javobi</b> (#{ticket_number})\n\n"
                f"{reply_text}\n\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
            ),
        )
        await message.answer(f"✅ Javob yuborildi. #{ticket_number}")
    except Exception:
        logger.error(
            "Failed to send support reply",
            exc_info=True,
            extra={"ticket_number": ticket_number, "user_id": user.telegram_id},
        )
