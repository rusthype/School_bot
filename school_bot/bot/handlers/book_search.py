"""book_search.py — Teacher-facing full-text book search handler.

Entry point: "🔍 Qidirish" inline button on the category-selection screen
(callback_data="book_search_start"). The teacher types a query; the bot
returns a results list with inline buttons that open the standard book
detail card via the existing cart flow (_show_book_detail wrapper).

Flow summary
------------
  category screen
    └─ 🔍 Qidirish  ──▶  BookSearchStates.waiting_for_query
                              │
                              ▼
                        teacher types query
                              │
                    ┌─────────┴──────────┐
               too short             results found / not found
                  │                       │
             keep state            show results list
                                   (state cleared)
                                          │
                                    tap book button
                                          │
                                   book detail card
                                   (reuses cart flow)
"""
from __future__ import annotations

from html import escape

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.handlers.book_order_cart import show_book_detail_from_search
from school_bot.bot.handlers.common import get_main_keyboard
from school_bot.bot.services.book_service import search_available_books, get_available_book_by_id
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.states.book_states import BookSearchStates

router = Router(name=__name__)
logger = get_logger(__name__)

_SHORT_TITLE_LEN = 30


def _short_title(title: str, max_len: int = _SHORT_TITLE_LEN) -> str:
    if len(title) <= max_len:
        return title
    return f"{title[:max_len - 1]}…"


# ---------------------------------------------------------------------------
# 4.2 — Entry point callback: book_search_start
# ---------------------------------------------------------------------------

@router.callback_query(lambda c: c.data == "book_search_start")
async def cb_book_search_start(
    callback: CallbackQuery,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await callback.answer("⛔ Bu bo'lim faqat o'qituvchilar uchun.", show_alert=True)
        return

    await state.set_state(BookSearchStates.waiting_for_query)
    await callback.message.answer(
        "🔍 Kitob nomi yoki muallifini yozing\n\n/cancel — bekor qilish"
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# 4.3 — Message handler: query input while in BookSearchStates.waiting_for_query
# ---------------------------------------------------------------------------

@router.message(BookSearchStates.waiting_for_query, Command("cancel"))
async def search_cancel_command(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher or is_superadmin,
    )
    await message.answer("✅ Qidiruv yakunlandi", reply_markup=keyboard)


@router.message(BookSearchStates.waiting_for_query, F.text)
async def handle_search_query(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    text = message.text or ""

    # Let /cancel fall through to the dedicated handler above; ignore
    # all other slash commands with a gentle reminder.
    if text.startswith("/"):
        await message.answer(
            "❗ Qidiruv so'rovini yuboring yoki /cancel ni bosing."
        )
        return

    query = text.strip()

    if len(query) < 2:
        await message.answer("❌ So'rov juda qisqa (kamida 2 harf)")
        # Keep FSM state — teacher can try again.
        return

    books = await search_available_books(session, query)
    logger.info(
        "Book search executed",
        extra={
            "user_id": message.from_user.id,
            "query": query,
            "results": len(books),
        },
    )

    if not books:
        await message.answer(
            f"❌ Hech narsa topilmadi: \"{escape(query)}\"\n\n"
            "Boshqa so'z bilan qidiring yoki /cancel"
        )
        # Keep state — teacher can try a different query.
        return

    # Build the results message.
    lines = [f"🔍 Topildi: {len(books)} ta kitob\n"]
    for i, book in enumerate(books, start=1):
        title = escape(book.title)
        author = escape(book.author) if book.author else "—"
        category_name = book.category.name if book.category else "—"
        lines.append(
            f"{i}. {title} — {author}\n"
            f"   📚 <i>{escape(category_name)}</i>"
        )

    builder = InlineKeyboardBuilder()
    for i, book in enumerate(books, start=1):
        builder.button(
            text=f"{i}. {_short_title(book.title)}",
            callback_data=f"book_view:{book.id}",
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(text="🔁 Yangi qidiruv", callback_data="book_search_start"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="book_search_cancel"),
    )

    # Clear state — teacher is now viewing results, not typing.
    await state.clear()
    await message.answer(
        "\n".join(lines).strip(),
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# 4.4 — Callback: book_view:{book_id} — open standard book detail card
# ---------------------------------------------------------------------------

@router.callback_query(lambda c: c.data.startswith("book_view:"))
async def cb_book_view(
    callback: CallbackQuery,
    session: AsyncSession,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await callback.answer("⛔ Bu bo'lim faqat o'qituvchilar uchun.", show_alert=True)
        return

    try:
        book_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    book = await get_available_book_by_id(session, book_id)
    if not book:
        await callback.answer("🚫 Bu kitob hozirda mavjud emas.", show_alert=True)
        return

    await show_book_detail_from_search(callback.message, session, book)
    await callback.answer()


# ---------------------------------------------------------------------------
# 4.5 — Callbacks: book_search_cancel (inline button) + /cancel already
#        handled above; this handles the inline "❌ Bekor qilish" button.
# ---------------------------------------------------------------------------

@router.callback_query(lambda c: c.data == "book_search_cancel")
async def cb_book_search_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher or is_superadmin,
    )
    await callback.message.answer("✅ Qidiruv yakunlandi", reply_markup=keyboard)
    await callback.answer()
