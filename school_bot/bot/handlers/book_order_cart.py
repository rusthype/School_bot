from __future__ import annotations

import time
from html import escape
from datetime import datetime
from pathlib import Path

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardButton, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from school_bot.bot.handlers.common import get_main_keyboard
from school_bot.bot.utils.telegram import send_chunked_to_chat
from school_bot.bot.services.book_service import (
    list_categories,
    list_books_by_category,
    get_book_by_id,
    get_category_by_id,
)
from school_bot.bot.services.book_order_service import (
    create_book_order,
    list_orders_by_teacher,
    list_order_items,
    get_status_text,
)
from school_bot.bot.states.book_order import BookOrderStates
from school_bot.bot.services.logger_service import get_logger
from school_bot.database.models import User, UserRole, Profile, School

router = Router(name=__name__)
logger = get_logger(__name__)


# ============== LIBRARY ORDER WITH CART ==============

def _short_title(title: str, max_len: int = 18) -> str:
    if len(title) <= max_len:
        return title
    return f"{title[:max_len - 1]}…"


def _format_cart_lines(items: list[tuple[str, int]]) -> list[str]:
    lines: list[str] = []
    total = 0
    for name, qty in items:
        lines.append(f"• {name} - {qty} dona")
        total += qty
    lines.append("")
    lines.append(f"Jami: {total} dona")
    return lines


def _priority_meta(priority: str) -> tuple[str, str]:
    labels = {
        "normal": "🟢 Oddiy (7-10 kun)",
        "urgent": "🟡 Shoshilinch (3-5 kun)",
        "express": "🔴 Tezkor (1-2 kun)",
    }
    short = {
        "normal": "🟢 Oddiy",
        "urgent": "🟡 Shoshilinch",
        "express": "🔴 Tezkor",
    }
    return labels.get(priority, "🟢 Oddiy (7-10 kun)"), short.get(priority, "🟢 Oddiy")


async def _send_cart_cover_previews(
    message: Message,
    session: AsyncSession,
    cart: dict[int, int],
    limit: int = 5,
) -> None:
    sent = 0
    for book_id, qty in cart.items():
        if qty < 1 or sent >= limit:
            continue
        book = await get_book_by_id(session, book_id)
        if not book or not book.cover_image:
            continue
        path = Path(book.cover_image)
        if not path.exists():
            continue
        caption = f"📖 {book.title} - {qty} dona"
        try:
            await message.answer_photo(photo=FSInputFile(path), caption=caption)
            sent += 1
        except Exception:
            logger.warning("Failed to send cart cover preview", exc_info=True)


async def _render_book_list(
    session: AsyncSession,
    category_id: int,
    cart: dict[int, int],
) -> tuple[str, InlineKeyboardBuilder]:
    category = await get_category_by_id(session, category_id)
    books = await list_books_by_category(session, category_id)

    title = category.name if category else "Kategoriya"
    lines = [f"📚 {title} uchun kitoblar:", ""]
    builder = InlineKeyboardBuilder()

    if not books:
        lines.append("📭 Bu kategoriya uchun kitoblar topilmadi.")
    else:
        for book in books:
            qty = cart.get(book.id, 0)
            author = f" ({book.author})" if book.author else ""
            availability = "🚫 " if not book.is_available else ""
            lines.append(f"{availability}📖 {book.title}{author}")
            lines.append(f"   Soni: {qty}")
            lines.append("")

            if not book.is_available:
                continue

            minus_label = f"➖ {_short_title(book.title)}"
            plus_label = f"➕ {_short_title(book.title)}"
            row_buttons = []
            if qty > 0:
                row_buttons.append((minus_label, f"cart_remove:{book.id}"))
            row_buttons.append((plus_label, f"cart_add:{book.id}"))
            for text, data in row_buttons:
                builder.button(text=text, callback_data=data)
            builder.adjust(2)

    builder.button(text="🛒 Savatni ko'rish", callback_data="cart_view")
    builder.button(text="✅ Buyurtma berish", callback_data="cart_checkout")
    builder.button(text="⬅️ Kategoriya", callback_data="cart_back_category")
    builder.button(text="❌ Bekor qilish", callback_data="cart_cancel")
    builder.adjust(2)
    return "\n".join(lines).strip(), builder


async def _safe_edit_text(
    message: Message,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    if message.photo:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def _show_book_detail(
    message: Message,
    session: AsyncSession,
    book,
    category_id: int,
    cart: dict[int, int],
    index: int,
    total: int,
) -> None:
    qty = cart.get(book.id, 0)
    title = escape(book.title)
    caption_lines = [f"📖 <b>{title}</b>"]
    if book.author:
        caption_lines.append(f"✍️ Muallif: {escape(book.author)}")
    availability = "✅ Ha" if book.is_available else "🚫 Yo'q"
    caption_lines.append(f"🔢 Mavjud: {availability}")
    if qty > 0:
        caption_lines.append(f"🛒 Savatda: {qty} dona")
    caption_lines.append(f"📚 {index + 1}/{total}")
    caption = "\n".join(caption_lines)

    builder = InlineKeyboardBuilder()
    if book.is_available:
        if qty > 0:
            builder.button(text="➖", callback_data=f"cart_remove:{book.id}")
        builder.button(text="➕ Savatga qo'shish", callback_data=f"cart_add:{book.id}")
        builder.adjust(2)

    nav_buttons: list[InlineKeyboardButton] = []
    if total > 1 and index > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"book_page:{category_id}:{index - 1}"))
    if total > 1 and index < total - 1:
        nav_buttons.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"book_page:{category_id}:{index + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)

    builder.row(
        InlineKeyboardButton(text="✅ Buyurtma berish", callback_data="cart_checkout")
    )
    builder.row(
        InlineKeyboardButton(text="🛒 Savatni ko'rish", callback_data="cart_view"),
        InlineKeyboardButton(text="⬅️ Kategoriyalar", callback_data="cart_back_category"),
    )
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cart_cancel"))

    if book.cover_image:
        path = Path(book.cover_image)
        if path.exists():
            media = InputMediaPhoto(media=FSInputFile(path), caption=caption, parse_mode="HTML")
            if message.photo:
                try:
                    await message.edit_media(media=media, reply_markup=builder.as_markup())
                    return
                except Exception:
                    pass
            try:
                await message.delete()
            except Exception:
                pass
            await message.answer_photo(
                photo=FSInputFile(path),
                caption=caption,
                reply_markup=builder.as_markup(),
            )
            return

    await _safe_edit_text(message, caption, reply_markup=builder.as_markup(), parse_mode="HTML")


async def _render_cart(
    session: AsyncSession,
    cart: dict[int, int],
) -> tuple[str, InlineKeyboardBuilder]:
    lines = ["🛒 Savatingiz:", ""]
    items: list[tuple[str, int]] = []
    for book_id, qty in cart.items():
        if qty < 1:
            continue
        book = await get_book_by_id(session, book_id)
        if not book:
            continue
        items.append((book.title, qty))

    if not items:
        lines.append("📭 Savat bo'sh.")
    else:
        lines.extend(_format_cart_lines(items))

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Buyurtma berish", callback_data="cart_checkout")
    builder.button(text="❌ Tozalash", callback_data="cart_clear")
    builder.button(text="⬅️ Ortga", callback_data="cart_back")
    builder.button(text="❌ Bekor qilish", callback_data="cart_cancel")
    builder.adjust(2)
    return "\n".join(lines).strip(), builder


@router.message(Command("order_books"))
@router.message(Command("order_book"))
async def cmd_order_books(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    start_time = time.time()
    if not (is_teacher or is_superadmin):
        await message.answer("⛔ Bu komanda faqat o'qituvchilar va superadminlar uchun.")
        return

    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Hozircha kitob kategoriyalari mavjud emas.")
        return

    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"bookcat:{category.id}")
    builder.adjust(2)

    await state.set_state(BookOrderStates.selecting_category)
    await state.update_data(cart={}, category_id=None, view="list")
    await message.answer(
        "📚 Kitob kategoriyasini tanlang:",
        reply_markup=builder.as_markup(),
    )
    execution_time = time.time() - start_time
    logger.info(
        f"/order_books ishga tushdi: {execution_time:.2f}s",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "order_books",
               "exec_ms": int(execution_time * 1000)},
    )


@router.message(BookOrderStates.selecting_category, F.text)
async def book_order_text_in_category(
    message: Message,
    session: AsyncSession,
) -> None:
    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Hozircha kitob kategoriyalari mavjud emas.")
        return

    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"bookcat:{category.id}")
    builder.adjust(2)

    logger.info(
        "Book order text received in category selection",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "order_books"},
    )
    await message.answer(
        "❗️ Kategoriyani tugmalar orqali tanlang.",
        reply_markup=builder.as_markup(),
    )


@router.message(BookOrderStates.shopping_cart, F.text)
async def book_order_text_in_cart(message: Message) -> None:
    logger.info(
        "Book order text received in cart state",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "order_books"},
    )
    await message.answer(
        "❗️ Kitoblarni tugmalar orqali tanlang.\n"
        "Savatni ko'rish uchun 🛒 tugmasidan foydalaning.\n"
        "Bekor qilish uchun /cancel bosing."
    )


@router.message(BookOrderStates.checkout, F.text)
async def book_order_text_in_checkout(message: Message) -> None:
    logger.info(
        "Book order text received in checkout state",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "order_books"},
    )
    await message.answer(
        "❗️ Buyurtmani yuborish uchun tugmani bosing yoki /cancel bilan bekor qiling."
    )

@router.message(Command("my_orders"))
async def cmd_my_orders(
    message: Message,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await message.answer("⛔ Bu komanda faqat o'qituvchilar uchun.")
        return

    orders = await list_orders_by_teacher(session, db_user.id, limit=20)
    if not orders:
        await message.answer("📭 Sizda buyurtmalar yo'q.")
        return

    lines = ["📦 **Mening buyurtmalarim** (oxirgi 20 ta):", ""]
    role_emoji = "👑" if is_superadmin else "👨‍🏫"
    for order in orders:
        item_lines = []
        items = await list_order_items(session, order.id)
        for item in items:
            book = await get_book_by_id(session, item.book_id)
            if book:
                item_lines.append(f"• {book.title} - {item.quantity} dona")
        status = get_status_text(order.status)
        created = order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "Noma'lum"
        delivery = order.delivery_date.strftime("%d.%m.%Y") if order.delivery_date else "Belgilanmagan"
        lines.append(
            f"{role_emoji} Buyurtma #{order.id} | {status}\n"
            + "\n".join(item_lines)
            + f"\n📅 {created}\n📦 Yetkazish: {delivery}"
        )
        lines.append("")

    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=True)
    await message.answer("\n".join(lines).strip(), reply_markup=keyboard)


@router.callback_query(lambda c: c.data.startswith("bookcat:"))
async def select_book_category(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await callback.answer("⛔ Bu bo'lim faqat o'qituvchilar uchun.", show_alert=True)
        return

    try:
        category_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    data = await state.get_data()
    cart = data.get("cart") or {}
    cart = {int(k): int(v) for k, v in cart.items()}

    books = await list_books_by_category(session, category_id)
    if not books:
        await _safe_edit_text(callback.message, "📭 Bu kategoriya uchun kitoblar topilmadi.")
        await callback.answer()
        return

    book_ids = [book.id for book in books]
    await state.update_data(
        category_id=category_id,
        cart=cart,
        view="detail",
        book_ids=book_ids,
        book_index=0,
    )
    await state.set_state(BookOrderStates.shopping_cart)

    await _show_book_detail(callback.message, session, books[0], category_id, cart, 0, len(book_ids))
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("cart_add:"))
async def cart_add_book(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        book_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    cart[book_id] = cart.get(book_id, 0) + 1

    category_id = data.get("category_id")
    if not category_id:
        await callback.answer("❌ Kategoriya tanlanmagan.", show_alert=True)
        return

    await state.update_data(cart=cart)
    view = data.get("view")
    if view == "detail":
        index = int(data.get("book_index") or 0)
        book_ids = data.get("book_ids") or []
        if book_ids and 0 <= index < len(book_ids):
            book = await get_book_by_id(session, int(book_ids[index]))
            if book:
                await _show_book_detail(
                    callback.message,
                    session,
                    book,
                    int(category_id),
                    cart,
                    index,
                    len(book_ids),
                )
                await callback.answer()
                return
    text, keyboard = await _render_book_list(session, int(category_id), cart)
    await _safe_edit_text(callback.message, text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("cart_remove:"))
async def cart_remove_book(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        book_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    if book_id in cart:
        cart[book_id] = max(0, cart[book_id] - 1)
        if cart[book_id] == 0:
            cart.pop(book_id, None)

    category_id = data.get("category_id")
    if not category_id:
        await callback.answer("❌ Kategoriya tanlanmagan.", show_alert=True)
        return

    await state.update_data(cart=cart)
    view = data.get("view")
    if view == "detail":
        index = int(data.get("book_index") or 0)
        book_ids = data.get("book_ids") or []
        if book_ids and 0 <= index < len(book_ids):
            book = await get_book_by_id(session, int(book_ids[index]))
            if book:
                await _show_book_detail(
                    callback.message,
                    session,
                    book,
                    int(category_id),
                    cart,
                    index,
                    len(book_ids),
                )
                await callback.answer()
                return
    text, keyboard = await _render_book_list(session, int(category_id), cart)
    await _safe_edit_text(callback.message, text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(lambda c: c.data == "cart_view")
async def cart_view(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    await state.set_state(BookOrderStates.checkout)
    await state.update_data(view="cart")

    text, keyboard = await _render_cart(session, cart)
    if callback.message.photo:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(text, reply_markup=keyboard.as_markup())
    else:
        await _safe_edit_text(callback.message, text, reply_markup=keyboard.as_markup())
    await _send_cart_cover_previews(callback.message, session, cart)
    await callback.answer()


@router.callback_query(lambda c: c.data == "view_cart")
async def cart_view_alias(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await cart_view(callback, state, session)


@router.message(F.text == "🛒 Savatni ko'rish")
async def cart_view_message(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    await state.set_state(BookOrderStates.checkout)
    await state.update_data(view="cart")
    text, keyboard = await _render_cart(session, cart)
    await message.answer(text, reply_markup=keyboard.as_markup())
    await _send_cart_cover_previews(message, session, cart)


@router.callback_query(lambda c: c.data == "cart_back")
async def cart_back(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    category_id = data.get("category_id")
    if not category_id:
        await callback.answer("❌ Kategoriya tanlanmagan.", show_alert=True)
        return

    await state.set_state(BookOrderStates.shopping_cart)
    await state.update_data(view="detail")
    index = int(data.get("book_index") or 0)
    book_ids = data.get("book_ids") or []
    if book_ids and 0 <= index < len(book_ids):
        book = await get_book_by_id(session, int(book_ids[index]))
        if book:
            await _show_book_detail(callback.message, session, book, int(category_id), cart, index, len(book_ids))
            await callback.answer()
            return
    text, keyboard = await _render_book_list(session, int(category_id), cart)
    await _safe_edit_text(callback.message, text, reply_markup=keyboard.as_markup())
    await callback.answer()


@router.callback_query(lambda c: c.data == "cart_back_category")
async def cart_back_to_categories(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    categories = await list_categories(session)
    if not categories:
        await callback.answer("📭 Kategoriyalar yo'q.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"bookcat:{category.id}")
    builder.adjust(2)
    await state.set_state(BookOrderStates.selecting_category)
    await _safe_edit_text(callback.message, "📚 Kitob kategoriyasini tanlang:", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("book_page:"))
async def book_page_navigate(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        _, category_str, index_str = callback.data.split(":")
        category_id = int(category_str)
        index = int(index_str)
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri sahifa.", show_alert=True)
        return

    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}

    book_ids = data.get("book_ids") or []
    if not book_ids:
        books = await list_books_by_category(session, category_id)
        book_ids = [book.id for book in books]
        await state.update_data(book_ids=book_ids)

    if not book_ids:
        await callback.answer("📭 Bu kategoriya uchun kitoblar topilmadi.", show_alert=True)
        return

    index = max(0, min(index, len(book_ids) - 1))
    book = await get_book_by_id(session, int(book_ids[index]))
    if not book:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return

    await state.update_data(category_id=category_id, view="detail", book_index=index)
    await _show_book_detail(callback.message, session, book, category_id, cart, index, len(book_ids))
    await callback.answer()


@router.callback_query(lambda c: c.data == "cart_clear")
async def cart_clear(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    cart = data.get("cart") or {}
    if not cart:
        await callback.answer("🛒 Savat allaqachon bo'sh")
        return

    await state.update_data(cart={})
    text, keyboard = await _render_cart(session, {})
    await _safe_edit_text(callback.message, text, reply_markup=keyboard.as_markup())
    await callback.answer("✅ Savat tozalandi")


@router.callback_query(lambda c: c.data == "cart_checkout")
async def cart_checkout(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_teacher: bool = False,
) -> None:
    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    items = [(book_id, qty) for book_id, qty in cart.items() if qty > 0]

    if not items:
        await callback.answer("Savat bo'sh.", show_alert=True)
        return
    await state.set_state(BookOrderStates.checkout)
    await state.update_data(priority=None)

    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Oddiy (7-10 kun)", callback_data="priority:normal")
    builder.button(text="🟡 Shoshilinch (3-5 kun)", callback_data="priority:urgent")
    builder.button(text="🔴 Tezkor (1-2 kun)", callback_data="priority:express")
    builder.button(text="❌ Bekor qilish", callback_data="cart_cancel")
    builder.adjust(1)

    await _safe_edit_text(
        callback.message,
        "📦 <b>Buyurtma ustuvorligini tanlang</b>\n\n"
        "🟢 Oddiy - 7-10 kun\n"
        "🟡 Shoshilinch - 3-5 kun\n"
        "🔴 Tezkor - 1-2 kun",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "checkout_start")
async def cart_checkout_alias(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_teacher: bool = False,
) -> None:
    await cart_checkout(callback, state, session, db_user, is_superadmin, is_teacher)


@router.callback_query(lambda c: c.data.startswith("priority:"))
async def cart_set_priority(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    priority = callback.data.split(":")[1]
    await state.update_data(priority=priority)

    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    items = [(book_id, qty) for book_id, qty in cart.items() if qty > 0]
    if not items:
        await callback.answer("Savat bo'sh.", show_alert=True)
        return

    item_lines: list[str] = []
    total = 0
    for book_id, qty in items:
        book = await get_book_by_id(session, book_id)
        if not book:
            continue
        item_lines.append(f"• {book.title} - {qty} dona")
        total += qty

    priority_full, _priority_short = _priority_meta(priority)
    text = (
        "📋 <b>Buyurtma tafsilotlari</b>\n\n"
        + "\n".join(item_lines)
        + f"\n\n📦 Jami: {total} dona\n"
        + f"{priority_full}\n\n"
        "✅ Buyurtmani tasdiqlaysizmi?"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="cart_confirm")
    builder.button(text="🔙 Ortga", callback_data="cart_checkout")
    builder.button(text="❌ Bekor qilish", callback_data="cart_cancel")
    builder.adjust(1)

    await _safe_edit_text(callback.message, text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()


async def _finalize_order(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_teacher: bool = False,
) -> None:
    data = await state.get_data()
    cart = {int(k): int(v) for k, v in (data.get("cart") or {}).items()}
    items = [(book_id, qty) for book_id, qty in cart.items() if qty > 0]
    if not items:
        await callback.answer("Savat bo'sh.", show_alert=True)
        return

    priority = data.get("priority") or "normal"
    priority_full, priority_short = _priority_meta(priority)

    order = await create_book_order(
        session=session,
        teacher_id=db_user.id,
        items=items,
        notes=None,
        priority=priority,
    )

    school_name = "Noma'lum"
    groups_text = ""
    profile_result = await session.execute(
        select(Profile).where(Profile.user_id == db_user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile and profile.school_id:
        school = await session.get(School, profile.school_id)
        if school:
            school_name = school.name
    if profile and profile.assigned_groups:
        groups_text = ", ".join(profile.assigned_groups)

    category_id = data.get("category_id")
    category = await get_category_by_id(session, int(category_id)) if category_id else None

    item_lines: list[str] = []
    for book_id, qty in items:
        book = await get_book_by_id(session, book_id)
        if book:
            item_lines.append(f"   • {book.title} - {qty} dona")

    class_name = category.name if category else "Noma'lum"
    if groups_text:
        class_name = f"{class_name} ({groups_text})"
    if is_superadmin:
        school_name = "Superadmin"
        class_name = "-"

    user_display = f"👨‍🏫 O'qituvchi: {db_user.full_name or db_user.telegram_id}"
    if is_superadmin:
        user_display = f"👑 SUPERADMIN: {db_user.full_name or db_user.telegram_id}"
    if db_user.username:
        user_display += f" (@{db_user.username})"

    order_lines = [
        "📚 YANGI KITOB BUYURTMA",
        f"🆔 Buyurtma ID: {order.id}",
        user_display,
        f"🏫 Maktab: {school_name}",
        f"📚 Sinf: {class_name}",
        f"{priority_short} Ustuvorlik",
    ]
    order_lines.append("📖 Kitoblar:")
    order_lines.extend(item_lines)
    order_lines.append(f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    order_lines.append(f"📊 Holat: {get_status_text(order.status)}")

    order_message = "\n".join(order_lines)

    librarian_result = await session.execute(select(User).where(User.role == UserRole.librarian))
    librarians = librarian_result.scalars().all()
    superadmin_result = await session.execute(select(User).where(User.role == UserRole.superadmin))
    superadmins = superadmin_result.scalars().all()

    admin_ids: set[int] = {admin.telegram_id for admin in superadmins}
    recipient_ids: set[int] = {db_user.telegram_id}
    recipient_ids.update(librarian.telegram_id for librarian in librarians)
    recipient_ids.update(admin_ids)
    action_recipient_ids: set[int] = {librarian.telegram_id for librarian in librarians}

    action_keyboard = InlineKeyboardBuilder()
    action_keyboard.button(text="🔄 Jarayonga o'tkazish", callback_data=f"order_processing:{order.id}")
    action_keyboard.button(text="📅 Yetkazish sanasi", callback_data=f"order_set_date:{order.id}")
    action_keyboard.button(text="✅ Tasdiqlash", callback_data=f"order_confirm:{order.id}")
    action_keyboard.button(text="📫 Yetkazib berildi", callback_data=f"order_deliver:{order.id}")
    action_keyboard.button(text="❌ Rad etish", callback_data=f"order_reject:{order.id}")
    action_keyboard.adjust(2, 2, 1)

    cover_payloads: list[tuple[str, str]] = []
    for book_id, qty in items:
        book = await get_book_by_id(session, book_id)
        if not book or not book.cover_image:
            continue
        cover_path = Path(book.cover_image)
        if not cover_path.exists():
            continue
        cover_payloads.append((str(cover_path), f"📖 {book.title} - {qty} dona"))

    for chat_id in recipient_ids:
        try:
            if chat_id in action_recipient_ids:
                await send_chunked_to_chat(
                    callback.bot,
                    chat_id,
                    order_message,
                    reply_markup=action_keyboard.as_markup(),
                )
            else:
                if chat_id in admin_ids:
                    admin_keyboard = InlineKeyboardBuilder()
                    admin_keyboard.button(
                        text="📋 Ko'rish",
                        callback_data=f"admin_order_view:{order.id}",
                    )
                    await send_chunked_to_chat(
                        callback.bot,
                        chat_id,
                        order_message,
                        reply_markup=admin_keyboard.as_markup(),
                    )
                else:
                    await send_chunked_to_chat(callback.bot, chat_id, order_message)
            for cover_path, caption in cover_payloads:
                await callback.bot.send_photo(
                    chat_id=chat_id,
                    photo=FSInputFile(cover_path),
                    caption=caption,
                )
        except Exception:
            logger.error(
                "Buyurtma yuborishda xatolik",
                exc_info=True,
                extra={"user_id": callback.from_user.id, "chat_id": chat_id, "command": "order_books"},
            )

    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher or is_superadmin,
    )
    await _safe_edit_text(callback.message, "✅ Buyurtma yuborildi!")
    await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data == "cart_confirm")
async def cart_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_teacher: bool = False,
) -> None:
    await _finalize_order(callback, state, session, db_user, is_superadmin, is_teacher)


@router.callback_query(lambda c: c.data == "confirm_order")
async def cart_confirm_alias(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_superadmin: bool = False,
    is_teacher: bool = False,
) -> None:
    await _finalize_order(callback, state, session, db_user, is_superadmin, is_teacher)


@router.callback_query(lambda c: c.data == "cart_cancel")
async def cart_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    is_superadmin: bool = False,
    is_teacher: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher or is_superadmin,
    )
    await _safe_edit_text(callback.message, "❌ Buyurtma bekor qilindi.")
    await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.message(StateFilter(BookOrderStates), F.text == "🏠 Bosh menyu")
async def book_order_back_to_main(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher or is_superadmin)
    await message.answer("Asosiy menyu", reply_markup=keyboard)


@router.message(StateFilter(BookOrderStates), F.text == "❌ /cancel")
@router.message(StateFilter(BookOrderStates), F.text == "❌ Bekor qilish")
async def cancel_book_order_button(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher or is_superadmin)
    await message.answer("✅ Buyurtma bekor qilindi.", reply_markup=keyboard)
