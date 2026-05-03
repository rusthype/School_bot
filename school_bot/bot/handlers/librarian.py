from __future__ import annotations

from datetime import datetime

from aiogram import Router, F
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from school_bot.bot.services.book_order_service import (
    list_book_orders,
    list_order_items,
    get_order_stats,
    get_book_order_by_id,
    set_delivery_date,
    mark_processing,
    confirm_order,
    reject_order,
    mark_delivered,
    get_status_text,
)
from school_bot.bot.services.book_service import get_book_by_id
from school_bot.bot.services.order_notifications import (
    notify_teacher_status_change,
    notify_teacher_delivery_date_set,
)
from school_bot.bot.handlers.common import get_main_keyboard
from school_bot.database.models import BookOrder, BookOrderItem, User, UserRole
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.utils.telegram import send_chunked_message

router = Router(name=__name__)
logger = get_logger(__name__)


class DeliveryDateStates(StatesGroup):
    waiting_date = State()


class RejectOrderStates(StatesGroup):
    waiting_for_reason = State()


def _can_access(is_superadmin: bool, is_librarian: bool) -> bool:
    return is_superadmin or is_librarian


def _priority_icon(priority: str | None) -> str:
    mapping = {
        "normal": "🟢",
        "urgent": "🟡",
        "express": "🔴",
    }
    return mapping.get(priority or "normal", "🟢")


# notify_teacher_status_change and notify_teacher_delivery_date_set are
# imported from school_bot.bot.services.order_notifications


async def _build_order_lines(session: AsyncSession, order_id: uuid.UUID) -> list[str]:
    items = await list_order_items(session, order_id)
    lines: list[str] = []
    for item in items:
        book = await get_book_by_id(session, item.book_id)
        if book:
            lines.append(f"• {book.title} - {item.quantity} dona")
    return lines


@router.message(Command("orders"))
async def cmd_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    orders = await list_book_orders(session=session, limit=20, status=None)
    if not orders:
        await message.answer("📭 Hozircha buyurtmalar yo'q.")
        return

    lines = ["📚 **Buyurtmalar ro'yxati** (oxirgi 20 ta):", ""]
    for order in orders:
        teacher = await session.get(User, order.teacher_id)
        if teacher:
            teacher_name = teacher.full_name or (teacher.username and f"@{teacher.username}") or str(teacher.telegram_id)
        else:
            teacher_name = f"ID: {order.teacher_id}"
        status = get_status_text(order.status)
        created = order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "Noma'lum"
        item_lines = await _build_order_lines(session, order.id)
        lines.append(
            f"🆔 {order.id} | {status}\n"
            f"👨‍🏫 {teacher_name}\n"
            + "\n".join(item_lines)
            + f"\n📅 {created}"
        )
        lines.append("")

    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=False, is_librarian=is_librarian)
    await send_chunked_message(message, "\n".join(lines).strip(), reply_markup=keyboard)


@router.message(Command("librarian_orders"))
async def cmd_librarian_orders_menu(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    stats = await get_order_stats(session=session)
    lines = [
        "📋 **Buyurtmalar paneli**",
        f"⏳ Kutilayotgan: {stats['pending']} ta",
        f"🔄 Jarayonda: {stats.get('processing', 0)} ta",
        f"✅ Tasdiqlangan: {stats['confirmed']} ta",
        f"📦 Yetkazilgan: {stats['delivered']} ta",
        f"❌ Rad etilgan: {stats['rejected']} ta",
    ]
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=False, is_librarian=is_librarian)
    await send_chunked_message(message, "\n".join(lines), reply_markup=keyboard)


@router.message(Command("pending_orders"))
async def cmd_pending_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    result = await session.execute(
        select(BookOrder)
        .where(BookOrder.status == "pending")
        .order_by(BookOrder.created_at.desc())
        .limit(20)
    )
    orders = result.scalars().all()
    if not orders:
        await message.answer("📭 Kutilayotgan buyurtmalar yo'q.")
        return

    for order in orders:
        teacher = await session.get(User, order.teacher_id)
        teacher_name = teacher.full_name or (teacher.username and f"@{teacher.username}") or str(teacher.telegram_id) if teacher else f"ID: {order.teacher_id}"
        created = order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "Noma'lum"
        item_lines = await _build_order_lines(session, order.id)

        text = (
            f"📚 Kutilayotgan buyurtma:\n"
            f"{_priority_icon(order.priority)} Ustuvorlik\n"
            f"🆔 Buyurtma ID: {order.id}\n"
            f"👨‍🏫 O'qituvchi: {teacher_name}\n"
            + "\n".join(item_lines)
            + f"\n📅 {created}"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Jarayonga o'tkazish", callback_data=f"order_processing:{order.id}")
        builder.button(text="📅 Yetkazish sanasi", callback_data=f"order_set_date:{order.id}")
        builder.button(text="✅ Tasdiqlash", callback_data=f"order_confirm:{order.id}")
        builder.button(text="📫 Yetkazib berildi", callback_data=f"order_deliver:{order.id}")
        builder.button(text="❌ Rad etish", callback_data=f"order_reject:{order.id}")
        builder.adjust(2, 2, 1)

        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("order_set_date:"))
async def set_delivery_date_start(
    callback: CallbackQuery,
    state: FSMContext,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    try:
        order_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    await state.set_state(DeliveryDateStates.waiting_date)
    await state.update_data(order_id=order_id)
    await callback.message.answer("📅 Yetkazish sanasini kiriting (KK.OO.YYYY):\nMasalan: 15.03.2026")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("order_processing:"))
async def processing_order_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    try:
        order_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    order = await get_book_order_by_id(session, order_id)
    if not order:
        await callback.answer("❌ Buyurtma topilmadi.", show_alert=True)
        return

    if order.status not in {"pending", "confirmed"}:
        await callback.answer("❌ Buyurtma jarayonga o'tkazib bo'lmaydi.", show_alert=True)
        return

    result = await mark_processing(session, order, db_user.id)

    teacher = await session.get(User, result.order.teacher_id)
    if teacher:
        await notify_teacher_status_change(
            callback.bot,
            teacher.telegram_id,
            result.order.id,
            result.old_status,
            result.new_status,
            result.comment,
        )

    await callback.message.edit_text(
        f"🔄 Buyurtma jarayonga o'tkazildi.\n"
        f"🆔 Buyurtma ID: {result.order.id}\n"
        f"📊 Holat: {get_status_text(result.order.status)}"
    )
    await callback.answer()


@router.message(DeliveryDateStates.waiting_date, F.text)
async def set_delivery_date_submit(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Ruxsat yo'q.")
        return

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await message.answer("❌ Buyurtma topilmadi.")
        await state.clear()
        return

    date_str = (message.text or "").strip()
    try:
        delivery = datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        await message.answer("❌ Sana formati noto'g'ri. Masalan: 15.03.2026")
        return

    order = await get_book_order_by_id(session, int(order_id))
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        await state.clear()
        return

    updated_order = await set_delivery_date(session, order, delivery, db_user.id)
    await state.clear()
    await message.answer(f"✅ Yetkazish sanasi belgilandi: {delivery.strftime('%d.%m.%Y')}")

    teacher = await session.get(User, updated_order.teacher_id)
    if teacher and updated_order.delivery_date:
        await notify_teacher_delivery_date_set(
            message.bot,
            teacher.telegram_id,
            updated_order.id,
            updated_order.delivery_date,
        )


@router.callback_query(lambda c: c.data.startswith("order_confirm:"))
async def confirm_order_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    try:
        order_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    order = await get_book_order_by_id(session, order_id)
    if not order:
        await callback.answer("❌ Buyurtma topilmadi.", show_alert=True)
        return

    if not order.delivery_date:
        await callback.answer("Avval yetkazish sanasini kiriting.", show_alert=True)
        return

    result = await confirm_order(session, order, db_user.id)

    teacher = await session.get(User, result.order.teacher_id)
    if teacher:
        await notify_teacher_status_change(
            callback.bot,
            teacher.telegram_id,
            result.order.id,
            result.old_status,
            result.new_status,
            result.comment,
        )

    await callback.message.answer("✅ Buyurtma tasdiqlandi.")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("order_reject:"))
async def reject_order_callback(
    callback: CallbackQuery,
    state: FSMContext,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    """Step 1 of 2: prompt the librarian for a rejection reason."""
    if not _can_access(is_superadmin, is_librarian):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    try:
        order_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    await state.set_state(RejectOrderStates.waiting_for_reason)
    await state.update_data(reject_order_id=order_id)
    await callback.message.answer(
        "❓ Rad etish sababi (matn yuboring yoki /skip):\n"
        "/cancel — rad etishni bekor qilish"
    )
    await callback.answer()


@router.message(RejectOrderStates.waiting_for_reason, F.text)
async def reject_order_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    """Step 2 of 2: receive reason text (or /skip / /cancel) and finalize rejection."""
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Ruxsat yo'q.")
        await state.clear()
        return

    text = (message.text or "").strip()

    if text == "/cancel":
        await state.clear()
        await message.answer("ℹ️ Rad etish bekor qilindi.")
        return

    data = await state.get_data()
    order_id = data.get("reject_order_id")
    if not order_id:
        await message.answer("❌ So'rov muddati tugadi.")
        await state.clear()
        return

    order = await get_book_order_by_id(session, int(order_id))
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        await state.clear()
        return

    comment: str | None = None if text == "/skip" else text
    result = await reject_order(session, order, db_user.id, comment=comment)
    await state.clear()

    confirm_text = f"✅ Buyurtma #{result.order.id} rad etildi"
    if comment:
        confirm_text += f"\n💬 Sabab: {comment}"
    await message.answer(confirm_text)

    teacher = await session.get(User, result.order.teacher_id)
    if teacher:
        await notify_teacher_status_change(
            message.bot,
            teacher.telegram_id,
            result.order.id,
            result.old_status,
            result.new_status,
            result.comment,
        )


@router.callback_query(lambda c: c.data.startswith("order_deliver:"))
async def deliver_order_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return

    try:
        order_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    order = await get_book_order_by_id(session, order_id)
    if not order:
        await callback.answer("❌ Buyurtma topilmadi.", show_alert=True)
        return

    if order.status not in {"confirmed", "processing"}:
        await callback.answer("❌ Buyurtma avval tasdiqlanishi kerak.", show_alert=True)
        return

    result = await mark_delivered(session, order, db_user.id)

    items = await _build_order_lines(session, result.order.id)
    delivered_at = result.order.delivered_at.strftime("%d.%m.%Y %H:%M") if result.order.delivered_at else "Noma'lum"
    deliverer = db_user.full_name or db_user.telegram_id

    await callback.message.edit_text(
        "📫 **BUYURTMA YETKAZILDI**\n\n"
        f"🆔 Buyurtma ID: {result.order.id}\n"
        "📖 Kitoblar:\n"
        + "\n".join(items)
        + f"\n\n📅 Yetkazilgan sana: {delivered_at}\n"
        f"✅ Yetkazib bergan: {deliverer}"
    )

    teacher = await session.get(User, result.order.teacher_id)
    if teacher:
        await notify_teacher_status_change(
            callback.bot,
            teacher.telegram_id,
            result.order.id,
            result.old_status,
            result.new_status,
            result.comment,
        )

    await callback.answer("✅ Buyurtma yetkazilgan deb belgilandi.")


@router.message(Command("set_delivery"))
async def cmd_set_delivery(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    if not command.args:
        await message.answer("Ishlatilishi: /set_delivery [order_id] [KK.OO.YYYY]")
        return

    parts = command.args.split()
    if not parts:
        await message.answer("Ishlatilishi: /set_delivery [order_id] [KK.OO.YYYY]")
        return

    if len(parts) == 1:
        try:
            order_id_val = int(parts[0])
        except ValueError:
            await message.answer("❌ Order ID noto'g'ri.")
            return
        await state.set_state(DeliveryDateStates.waiting_date)
        await state.update_data(order_id=str(order_id_val))
        await message.answer("📅 Yetkazish sanasini kiriting (KK.OO.YYYY):")
        return
    if len(parts) >= 2:
        order_id_str, date_str = parts[0], parts[1]
        try:
            order_id_val = int(order_id_str)
        except ValueError:
            await message.answer("❌ Order ID noto'g'ri.")
            return
        try:
            delivery = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            await message.answer("❌ Sana formati noto'g'ri. Masalan: 15.03.2026")
            return

        order = await get_book_order_by_id(session, order_id_val)
        if not order:
            await message.answer("❌ Buyurtma topilmadi.")
            return

        updated_order = await set_delivery_date(session, order, delivery, db_user.id)
        await message.answer(f"✅ Yetkazish sanasi belgilandi: {delivery.strftime('%d.%m.%Y')}")

        teacher = await session.get(User, updated_order.teacher_id)
        if teacher and updated_order.delivery_date:
            await notify_teacher_delivery_date_set(
                message.bot,
                teacher.telegram_id,
                updated_order.id,
                updated_order.delivery_date,
            )
        return


@router.message(Command("confirm_order"))
async def cmd_confirm_order(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    if not command.args or not command.args.strip():
        await message.answer("Ishlatilishi: /confirm_order [order_id]")
        return

    try:
        order_id = int(command.args.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri order ID.")
        return
    order = await get_book_order_by_id(session, order_id)
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        return

    if not order.delivery_date:
        await message.answer("Avval yetkazish sanasini kiriting: /set_delivery [order_id]")
        return

    result = await confirm_order(session, order, db_user.id)
    teacher = await session.get(User, result.order.teacher_id)
    if teacher:
        await notify_teacher_status_change(
            message.bot,
            teacher.telegram_id,
            result.order.id,
            result.old_status,
            result.new_status,
            result.comment,
        )

    await message.answer("✅ Buyurtma tasdiqlandi.")


@router.message(Command("order_stats"))
async def cmd_order_stats(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    stats = await get_order_stats(session=session)
    lines = [
        "📊 **Buyurtmalar statistikasi**",
        f"Jami: {stats['total']} ta",
        f"⏳ Kutilayotgan: {stats['pending']} ta",
        f"🔄 Jarayonda: {stats.get('processing', 0)} ta",
        f"✅ Tasdiqlangan: {stats['confirmed']} ta",
        f"🚚 Yetkazilgan: {stats['delivered']} ta",
        f"❌ Rad etilgan: {stats['rejected']} ta",
        "",
        f"📅 Yangilanish: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    ]

    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=False, is_librarian=is_librarian)
    await send_chunked_message(message, "\n".join(lines), reply_markup=keyboard)


@router.message(Command("mark_done"))
async def cmd_mark_done(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    if not command.args or not command.args.strip():
        await message.answer("Ishlatilishi: /mark_done [order_id]")
        return

    try:
        order_id = int(command.args.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri order ID.")
        return
    order = await get_book_order_by_id(session, order_id)
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        return

    if order.status not in {"confirmed", "processing"}:
        await message.answer("❌ Buyurtma avval tasdiqlanishi kerak.")
        return

    result = await mark_delivered(session, order, db_user.id)
    await message.answer(f"✅ Buyurtma yetkazildi: {result.order.id}")

    teacher = await session.get(User, result.order.teacher_id)
    if teacher:
        await notify_teacher_status_change(
            message.bot,
            teacher.telegram_id,
            result.order.id,
            result.old_status,
            result.new_status,
            result.comment,
        )


@router.message(F.text == "📚 Buyurtmalar ro'yxati")
async def button_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    await cmd_orders(message, session, is_superadmin, is_librarian)


@router.message(F.text == "📚 Kutilayotgan buyurtmalar")
async def button_pending_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    await cmd_pending_orders(message, session, is_superadmin, is_librarian)


@router.message(F.text == "📦 Barcha buyurtmalar")
async def button_all_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    await cmd_orders(message, session, is_superadmin, is_librarian)


@router.message(F.text == "📦 Yetkazilgan buyurtmalar")
async def button_delivered_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    result = await session.execute(
        select(BookOrder)
        .where(BookOrder.status == "delivered")
        .order_by(BookOrder.delivered_at.desc().nullslast(), BookOrder.created_at.desc())
        .limit(50)
        .options(selectinload(BookOrder.items).selectinload(BookOrderItem.book), selectinload(BookOrder.teacher))
    )
    orders = result.scalars().all()

    if not orders:
        await message.answer("📭 Yetkazilgan buyurtmalar yo'q.")
        return

    for order in orders:
        teacher = order.teacher
        teacher_name = teacher.full_name or (teacher.username and f"@{teacher.username}") or str(teacher.telegram_id) if teacher else f"ID: {order.teacher_id}"
        delivered_at = order.delivered_at.strftime("%d.%m.%Y %H:%M") if order.delivered_at else "Noma'lum"
        item_lines = await _build_order_lines(session, order.id)
        await message.answer(
            "📫 **Yetkazilgan buyurtma**\n"
            f"🆔 Buyurtma ID: {order.id}\n"
            f"👨‍🏫 O'qituvchi: {teacher_name}\n"
            + "\n".join(item_lines)
            + f"\n📅 Yetkazilgan: {delivered_at}"
        )


@router.message(F.text == "✅ Tasdiqlangan buyurtmalar")
async def button_confirmed_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    result = await session.execute(
        select(BookOrder)
        .where(BookOrder.status == "confirmed")
        .order_by(BookOrder.delivery_date.nullslast(), BookOrder.created_at.desc())
        .options(selectinload(BookOrder.items).selectinload(BookOrderItem.book), selectinload(BookOrder.teacher))
    )
    orders = result.scalars().all()

    if not orders:
        await message.answer("📭 Tasdiqlangan buyurtmalar yo'q.")
        return

    for order in orders:
        teacher = order.teacher
        teacher_name = teacher.full_name or (teacher.username and f"@{teacher.username}") or str(teacher.telegram_id) if teacher else f"ID: {order.teacher_id}"
        item_lines = await _build_order_lines(session, order.id)
        created = order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "Noma'lum"
        delivery_date = order.delivery_date.strftime("%d.%m.%Y") if order.delivery_date else "Noma'lum"

        builder = InlineKeyboardBuilder()
        builder.button(text="📅 Yetkazish sanasi", callback_data=f"order_set_date:{order.id}")
        builder.button(text="📫 Yetkazib berildi", callback_data=f"order_deliver:{order.id}")
        builder.button(text="❌ Rad etish", callback_data=f"order_reject:{order.id}")
        builder.adjust(3)

        await message.answer(
            "✅ **Tasdiqlangan buyurtma**\n"
            f"{_priority_icon(order.priority)} Ustuvorlik\n"
            f"🆔 Buyurtma ID: {order.id}\n"
            f"👨‍🏫 O'qituvchi: {teacher_name}\n"
            "📖 Kitoblar:\n"
            + "\n".join(item_lines)
            + f"\n📌 Status: {get_status_text(order.status)}\n"
            + f"\n📅 Buyurtma: {created}\n"
            + f"📅 Yetkazish sanasi: {delivery_date}",
            reply_markup=builder.as_markup(),
        )


@router.message(F.text == "🔄 Jarayondagi buyurtmalar")
async def button_processing_orders(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    if not _can_access(is_superadmin, is_librarian):
        await message.answer("⛔ Bu komanda faqat kutubxonachilar uchun.")
        return

    result = await session.execute(
        select(BookOrder)
        .where(BookOrder.status == "processing")
        .order_by(BookOrder.delivery_date.nullslast(), BookOrder.created_at.desc())
        .options(selectinload(BookOrder.items).selectinload(BookOrderItem.book), selectinload(BookOrder.teacher))
    )
    orders = result.scalars().all()

    if not orders:
        await message.answer("📭 Jarayondagi buyurtmalar yo'q.")
        return

    for order in orders:
        teacher = order.teacher
        teacher_name = teacher.full_name or (teacher.username and f"@{teacher.username}") or str(teacher.telegram_id) if teacher else f"ID: {order.teacher_id}"
        item_lines = await _build_order_lines(session, order.id)
        created = order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "Noma'lum"
        delivery_date = order.delivery_date.strftime("%d.%m.%Y") if order.delivery_date else "Noma'lum"

        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Tasdiqlash", callback_data=f"order_confirm:{order.id}")
        builder.button(text="📫 Yetkazib berildi", callback_data=f"order_deliver:{order.id}")
        builder.button(text="❌ Rad etish", callback_data=f"order_reject:{order.id}")
        builder.adjust(3)

        await message.answer(
            "🔄 **Jarayondagi buyurtma**\n"
            f"{_priority_icon(order.priority)} Ustuvorlik\n"
            f"🆔 Buyurtma ID: {order.id}\n"
            f"👨‍🏫 O'qituvchi: {teacher_name}\n"
            "📖 Kitoblar:\n"
            + "\n".join(item_lines)
            + f"\n📌 Status: {get_status_text(order.status)}\n"
            + f"\n📅 Buyurtma: {created}\n"
            + f"📅 Yetkazish sanasi: {delivery_date}",
            reply_markup=builder.as_markup(),
        )


@router.message(F.text == "📊 Buyurtma statistikasi")
async def button_order_stats(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_librarian: bool = False,
) -> None:
    await cmd_order_stats(message, session, is_superadmin, is_librarian)
