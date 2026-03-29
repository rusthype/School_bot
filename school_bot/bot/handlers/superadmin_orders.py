from __future__ import annotations
from datetime import datetime, timezone
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.order_status import ORDER_STATUS, get_status_text, get_priority_text
from school_bot.database.models import BookOrder, OrderStatusHistory, User, BookOrderItem, School, Profile
router = Router(name=__name__)
logger = get_logger(__name__)
class OrderStatusChange(StatesGroup):
    waiting_for_comment = State()
def _is_superadmin(is_superadmin: bool) -> bool:
    return is_superadmin
async def _build_items_text(order: BookOrder) -> str:
    lines = []
    for item in order.items:
        if item.book:
            title = item.book.title
        else:
            title = f"ID: {item.book_id}"
        lines.append(f"  • {title} - {item.quantity} dona")
    return "\n".join(lines)
@router.message(Command("admin_orders"))
async def admin_orders_command(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not _is_superadmin(is_superadmin):
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return
    await _show_orders_list(message, session)
@router.callback_query(lambda c: c.data == "admin_orders")
async def admin_orders_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not _is_superadmin(is_superadmin):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await _show_orders_list(callback.message, session, edit=True)
    await callback.answer()
async def _show_orders_list(target: Message, session: AsyncSession, edit: bool = False) -> None:
    result = await session.execute(
        select(BookOrder)
        .order_by(desc(BookOrder.created_at))
        .limit(20)
        .options(selectinload(BookOrder.teacher))
    )
    orders = result.scalars().all()
    if not orders:
        text = "📭 Hali hech qanday buyurtma yo'q."
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
        if edit:
            await target.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
        return
    lines = ["📋 <b>Barcha buyurtmalar</b>", ""]
    keyboard = InlineKeyboardBuilder()
    for order in orders:
        teacher = order.teacher
        teacher_name = teacher.full_name or (teacher.username and f"@{teacher.username}") or str(teacher.telegram_id) if teacher else f"ID: {order.teacher_id}"
        created_at_str = order.created_at.strftime('%d.%m.%Y %H:%M') if order.created_at else "Noma'lum"
        lines.append(
            f"🆔 #{order.id}\n"
            f"👤 {teacher_name}\n"
            f"{get_priority_text(order.priority)}\n"
            f"{get_status_text(order.status)}\n"
            f"📅 {created_at_str}\n"
            "──────────────────"
        )
        keyboard.button(text=f"#{order.id}", callback_data=f"admin_order_view:{order.id}")
    keyboard.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel"))
    keyboard.adjust(2)
    text = "\n".join(lines)
    if edit:
        await target.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
@router.callback_query(lambda c: c.data.startswith("admin_order_view:"))
async def admin_view_order(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not _is_superadmin(is_superadmin):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        order_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    result = await session.execute(
        select(BookOrder)
        .where(BookOrder.id == order_id)
        .options(
            selectinload(BookOrder.items).selectinload(BookOrderItem.book),
            selectinload(BookOrder.teacher),
            selectinload(BookOrder.status_history),
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        await callback.answer("❌ Buyurtma topilmadi.", show_alert=True)
        return
    teacher = order.teacher
    teacher_name = teacher.full_name or (teacher.username and f"@{teacher.username}") or str(teacher.telegram_id) if teacher else f"ID: {order.teacher_id}"
    items_text = await _build_items_text(order)
    status_text = get_status_text(order.status)
    priority_text = get_priority_text(order.priority)
    history = order.status_history or []
    last_update = history[-1] if history else None
    last_line = ""
    if last_update:
        last_line = f"🔄 Oxirgi o'zgarish: {last_update.changed_at.strftime('%d.%m.%Y %H:%M')}"
        if last_update.comment:
            last_line += f"\n💬 Izoh: {last_update.comment}"
    created_at_str = order.created_at.strftime('%d.%m.%Y %H:%M') if order.created_at else "Noma'lum"
    text = (
        f"📋 <b>Buyurtma #{order.id}</b>\n\n"
        f"👤 O'qituvchi: {teacher_name}\n"
        f"📊 Ustuvorlik: {priority_text}\n"
        f"📌 Status: {status_text}\n\n"
        f"📚 Kitoblar:\n{items_text}\n\n"
        f"📅 Yaratilgan: {created_at_str}\n"
        f"{last_line}"
    )
    keyboard = InlineKeyboardBuilder()
    current_info = ORDER_STATUS.get(order.status, ORDER_STATUS["pending"])
    for next_status in current_info.get("next_status", []):
        info = ORDER_STATUS.get(next_status)
        if not info:
            continue
        keyboard.button(
            text=f"{info['emoji']} {info['name']}",
            callback_data=f"admin_change_status:{order.id}:{next_status}",
        )
    keyboard.adjust(2)
    keyboard.row(
        InlineKeyboardButton(text="📜 Tarix", callback_data=f"admin_order_history:{order.id}"),
        InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_orders"),
    )
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("admin_change_status:"))
async def admin_change_status(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not _is_superadmin(is_superadmin):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        _, order_id_str, new_status = callback.data.split(":")
        order_id = int(order_id_str)
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    order = await session.get(BookOrder, order_id)
    if not order:
        await callback.answer("❌ Buyurtma topilmadi.", show_alert=True)
        return
    await state.update_data(order_id=order_id, old_status=order.status, new_status=new_status)
    await state.set_state(OrderStatusChange.waiting_for_comment)
    await callback.message.edit_text(
        "💬 <b>Status o'zgartirish</b>\n\n"
        f"Eski status: {get_status_text(order.status)}\n"
        f"Yangi status: {get_status_text(new_status)}\n\n"
        "Izoh qoldirishingiz mumkin (ixtiyoriy). /skip yozing yoki bo'sh qoldiring.",
        reply_markup=InlineKeyboardBuilder().row(
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"admin_order_view:{order_id}")
        ).as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()
@router.message(OrderStatusChange.waiting_for_comment)
async def admin_status_comment(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    is_superadmin: bool = False,
) -> None:
    if not _is_superadmin(is_superadmin):
        await message.answer("⛔ Ruxsat yo'q.")
        await state.clear()
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    old_status = data.get("old_status")
    new_status = data.get("new_status")
    if not order_id or not new_status:
        await message.answer("❌ So'rov muddati tugadi.")
        await state.clear()
        return
    comment = (message.text or "").strip()
    if comment == "/skip":
        comment = None
    order = await session.get(BookOrder, int(order_id))
    if not order:
        await message.answer("❌ Buyurtma topilmadi.")
        await state.clear()
        return
    order.status = new_status
    order.updated_at = datetime.now(timezone.utc)
    order.updated_by = db_user.id
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=old_status or "",
            new_status=new_status,
            changed_by=db_user.id,
            comment=comment,
        )
    )
    await session.commit()
    teacher = await session.get(User, order.teacher_id)
    if teacher:
        await _notify_teacher_status_change(
            message.bot,
            teacher.telegram_id,
            order.id,
            old_status or "",
            new_status,
            comment,
        )
    await message.answer(
        f"✅ Status o'zgartirildi: {get_status_text(old_status)} ➡️ {get_status_text(new_status)}"
    )
    await state.clear()
async def _notify_teacher_status_change(
    bot,
    teacher_chat_id: int,
    order_id: int,
    old_status: str,
    new_status: str,
    comment: str | None = None,
) -> None:
    text = (
        "🔄 <b>Buyurtma statusi o'zgartirildi</b>\n\n"
        f"🆔 Buyurtma #{order_id}\n"
        f"Eski status: {get_status_text(old_status)}\n"
        f"Yangi status: {get_status_text(new_status)}\n"
    )
    if comment:
        text += f"\n💬 Izoh: {comment}"
    await bot.send_message(chat_id=teacher_chat_id, text=text, parse_mode="HTML")
@router.callback_query(lambda c: c.data.startswith("admin_order_history:"))
async def admin_order_history(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not _is_superadmin(is_superadmin):
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        order_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    result = await session.execute(
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == order_id)
        .order_by(desc(OrderStatusHistory.changed_at))
        .options(selectinload(OrderStatusHistory.user))
    )
    history = result.scalars().all()
    if not history:
        await callback.message.edit_text(
            "📭 Status tarixi yo'q.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"admin_order_view:{order_id}")
            ).as_markup(),
        )
        await callback.answer()
        return
    lines = [f"📜 <b>Buyurtma #{order_id} tarixi</b>", ""]
    for record in history:
        user = record.user
        user_name = user.full_name or (user.username and f"@{user.username}") or str(user.telegram_id) if user else "Noma'lum"
        lines.append(f"🕐 {record.changed_at.strftime('%d.%m.%Y %H:%M')}")
        lines.append(f"👤 {user_name}")
        lines.append(f"{get_status_text(record.old_status)} ➡️ {get_status_text(record.new_status)}")
        if record.comment:
            lines.append(f"💬 Izoh: {record.comment}")
        lines.append("──────────────────")
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"admin_order_view:{order_id}"))
    await callback.message.edit_text("\n".join(lines), reply_markup=keyboard.as_markup(), parse_mode="HTML")
    await callback.answer()
def _paginate(items: list, page: int, page_size: int) -> tuple[list, int, int]:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], page, total_pages
@router.callback_query(lambda c: c.data == "noop")
async def noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()