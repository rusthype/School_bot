from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from datetime import datetime

from school_bot.bot.handlers.common import get_main_keyboard, get_skip_cancel_keyboard
from school_bot.bot.states.book_states import BookAddStates, BookEditStates, BookDeleteStates
from school_bot.bot.services.book_service import (
    list_categories,
    get_category_by_id,
    list_books_by_category,
    get_book_by_id,
    add_book,
    update_book,
    remove_book,
)
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.utils.telegram import send_chunked_message, safe_edit_or_send

router = Router(name=__name__)
logger = get_logger(__name__)

_CATEGORY_PAGE_SIZE = 12
_COVERS_DIR = Path(__file__).resolve().parents[3] / "covers"
_COVERS_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_PREDEFINED_BOOKS: list[tuple[str, str]] = [
    ("math", "Matematika"),
    ("eng", "Ingliz tili"),
    ("extra", "Qo'shimcha kitoblar"),
]

_PREDEFINED_BOOKS: dict[str, list[tuple[str, str]]] = {
    "1-sinf": _DEFAULT_PREDEFINED_BOOKS,
    "2-sinf": _DEFAULT_PREDEFINED_BOOKS,
    "3-sinf": _DEFAULT_PREDEFINED_BOOKS,
    "4-sinf": _DEFAULT_PREDEFINED_BOOKS,
}


def _get_predefined_books(category_name: str) -> list[tuple[str, str]]:
    return _PREDEFINED_BOOKS.get(category_name, _DEFAULT_PREDEFINED_BOOKS)


def _resolve_predefined_title(category_name: str, key: str) -> str | None:
    for book_key, title in _get_predefined_books(category_name):
        if book_key == key:
            return title
    return None


def _build_predefined_books_keyboard(category_name: str) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for key, title in _get_predefined_books(category_name):
        builder.button(text=title, callback_data=f"addbook_predefined:{key}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="addbook_cancel"))
    return builder


async def _save_cover_file(message: Message, file_id: str) -> str:
    file = await message.bot.get_file(file_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"book_{timestamp}_{file_id}.jpg"
    dest_path = _COVERS_DIR / filename
    await message.bot.download_file(file.file_path, destination=str(dest_path))
    return str(dest_path)


def _build_category_keyboard(categories: list, page: int = 1) -> InlineKeyboardBuilder:
    total_pages = max(1, (len(categories) + _CATEGORY_PAGE_SIZE - 1) // _CATEGORY_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * _CATEGORY_PAGE_SIZE
    end = start + _CATEGORY_PAGE_SIZE
    page_categories = categories[start:end]

    builder = InlineKeyboardBuilder()
    if len(page_categories) <= 4:
        builder.row(
            *[
                InlineKeyboardButton(
                    text=category.name,
                    callback_data=f"list_category:{category.id}",
                )
                for category in page_categories
            ]
        )
    else:
        for i in range(0, len(page_categories), 4):
            row = []
            for category in page_categories[i:i + 4]:
                row.append(
                    InlineKeyboardButton(
                        text=category.name,
                        callback_data=f"list_category:{category.id}",
                    )
                )
            if row:
                builder.row(*row)

    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"list_cat_page:{page - 1}",
                )
            )
        else:
            nav_row.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"list_cat_info:{page}",
                )
            )

        nav_row.append(
            InlineKeyboardButton(
                text=f"📍 {page}/{total_pages}",
                callback_data=f"list_cat_info:{page}",
            )
        )

        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"list_cat_page:{page + 1}",
                )
            )
        else:
            nav_row.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"list_cat_info:{page}",
                )
            )

        builder.row(*nav_row)
    builder.row(
        InlineKeyboardButton(text="🔙 Ortga", callback_data="list_back_to_categories"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="list_cancel"),
    )
    return builder


def _truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return f"{text[:max_len - 1]}…"


async def _send_book_cover_previews(message: Message, books: list, limit: int = 5) -> None:
    sent = 0
    for book in books:
        if sent >= limit:
            break
        cover_path = book.cover_image
        if not cover_path:
            continue
        file_path = Path(cover_path)
        if not file_path.exists():
            continue
        caption = f"📖 {book.title}"
        try:
            await message.answer_photo(photo=FSInputFile(file_path), caption=caption)
            sent += 1
        except Exception:
            logger.warning("Failed to send cover preview", exc_info=True)
            continue


async def _show_predefined_book_picker(
    message: Message,
    state: FSMContext,
    category_id: uuid.UUID,
    category_name: str,
) -> None:
    await state.update_data(category_id=category_id, category_name=category_name)
    await state.set_state(BookAddStates.select_predefined_book)
    await message.answer("📚 Kitob tanlash uchun tugmani bosing:", reply_markup=ReplyKeyboardRemove())
    keyboard = _build_predefined_books_keyboard(category_name).as_markup()
    await message.answer(
        f"📚 {category_name} uchun kitobni tanlang:",
        reply_markup=keyboard,
    )


async def _show_category_selection(message: Message, categories: list, page: int = 1) -> None:
    keyboard = _build_category_keyboard(categories, page=page).as_markup()
    await message.answer("📚 <b>Kategoriyani tanlang:</b>", reply_markup=keyboard)


async def _show_edit_categories(message: Message, categories: list, edit: bool = False) -> None:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"editbook_cat:{category.id}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="editbook_cancel"))
    text = "📚 Qaysi kategoriyadagi kitobni tahrirlamoqchisiz?"
    if edit:
        await safe_edit_or_send(message, text, reply_markup=builder.as_markup())
    else:
        await send_chunked_message(message, text, reply_markup=builder.as_markup())


async def _show_edit_books(
    message: Message,
    session: AsyncSession,
    category_id: uuid.UUID,
    category_name: str,
    edit: bool = True,
) -> None:
    books = await list_books_by_category(session, category_id)
    if not books:
        text = f"📚 <b>{category_name} kategoriyasida kitob yo'q.</b>"
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="editbook_back_to_cats"))
        builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="editbook_cancel"))
        if edit:
            await message.edit_text(text, reply_markup=builder.as_markup())
        else:
            await message.answer(text, reply_markup=builder.as_markup())
        return

    lines = [f"📚 <b>{category_name} kategoriyasidagi kitoblar:</b>", ""]
    builder = InlineKeyboardBuilder()
    for idx, book in enumerate(books, 1):
        lines.append(f"{idx}. 📖 {book.title}")
        builder.button(
            text=f"✏️ {book.title[:24]}",
            callback_data=f"editbook_select:{book.id}",
        )
        builder.button(
            text=f"🗑️ {book.title[:24]}",
            callback_data=f"editbook_remove:{book.id}",
        )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="editbook_back_to_cats"))
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="editbook_cancel"))

    text = "\n".join(lines).strip()
    if edit:
        await safe_edit_or_send(message, text, reply_markup=builder.as_markup())
    else:
        await send_chunked_message(message, text, reply_markup=builder.as_markup())


async def _show_edit_fields(message: Message, book_title: str) -> None:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="1️⃣ Kategoriyasi", callback_data="editbook_field:category"),
        InlineKeyboardButton(text="2️⃣ Rasmi", callback_data="editbook_field:cover"),
    )
    builder.row(
        InlineKeyboardButton(text="3️⃣ Barcha ma'lumotlar", callback_data="editbook_field:all"),
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Ortga", callback_data="editbook_back_to_books"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="editbook_cancel"),
    )

    await message.edit_text(
        f"📖 <b>{book_title}</b> kitobini tahrirlash\n\nNimani o'zgartirmoqchisiz?",
        reply_markup=builder.as_markup(),
    )


async def _show_delete_categories(message: Message, categories: list, edit: bool = False) -> None:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"deletebook_cat:{category.id}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="deletebook_cancel"))
    text = "📚 Qaysi kategoriyadagi kitobni o'chirmoqchisiz?"
    if edit:
        await safe_edit_or_send(message, text, reply_markup=builder.as_markup())
    else:
        await send_chunked_message(message, text, reply_markup=builder.as_markup())


async def _show_delete_books(
    message: Message,
    session: AsyncSession,
    category_id: uuid.UUID,
    category_name: str,
    edit: bool = True,
) -> None:
    books = await list_books_by_category(session, category_id)
    if not books:
        text = f"📚 <b>{category_name} kategoriyasida kitob yo'q.</b>"
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="deletebook_back_to_cats"))
        builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="deletebook_cancel"))
        if edit:
            await message.edit_text(text, reply_markup=builder.as_markup())
        else:
            await message.answer(text, reply_markup=builder.as_markup())
        return

    lines = [f"📚 <b>{category_name} kategoriyasidagi kitoblar:</b>", ""]
    builder = InlineKeyboardBuilder()
    for idx, book in enumerate(books, 1):
        lines.append(f"{idx}. 📖 {book.title}")
        builder.button(
            text=f"🗑️ {book.title[:24]}",
            callback_data=f"deletebook_confirm:{book.id}",
        )
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="deletebook_back_to_cats"))
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="deletebook_cancel"))

    text = "\n".join(lines).strip()
    if edit:
        await safe_edit_or_send(message, text, reply_markup=builder.as_markup())
    else:
        await send_chunked_message(message, text, reply_markup=builder.as_markup())


@router.message(Command("add_book"))
async def cmd_add_book(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Avval kategoriya qo'shing: /add_category")
        return

    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.name, callback_data=f"book_add_cat:{category.id}")
    builder.adjust(2)

    await state.set_state(BookAddStates.select_category)
    await message.answer("📚 Kitob uchun kategoriyani tanlang:", reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("book_add_cat:"))
async def add_book_select_category(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        category_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    category = await get_category_by_id(session, category_id)
    if not category:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return

    logger.info(
        "Book add category selected",
        extra={
            "user_id": callback.from_user.id,
            "chat_id": callback.message.chat.id,
            "category_id": category_id,
            "command": "add_book",
        },
    )
    await callback.message.delete()
    await _show_predefined_book_picker(
        message=callback.message,
        state=state,
        category_id=category_id,
        category_name=category.name,
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("addbook_predefined:"))
async def add_book_select_predefined(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    category_name = data.get("category_name") or "Noma'lum"
    try:
        key = callback.data.split(":", 1)[1]
    except IndexError:
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    title = _resolve_predefined_title(category_name, key)
    if not title:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return

    await state.update_data(title=title)
    await state.set_state(BookAddStates.cover)
    await callback.message.delete()
    await callback.message.answer(
        f"📖 Tanlangan kitob: {title}\n\n"
        "🖼️ Kitob rasmini yuboring (ixtiyoriy):\n"
        "⏭️ O'tkazib yuborish tugmasini bosing\n"
        "❌ Bekor qilish tugmasini bosing",
        reply_markup=get_skip_cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "addbook_cancel")
async def add_book_cancel_predefined(
    callback: CallbackQuery,
    state: FSMContext,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )
    await callback.message.edit_text("❌ Jarayon bekor qilindi.")
    await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.message(Command("list_books"))
async def cmd_list_books_start(
    message: Message,
    session: AsyncSession,
    command: CommandObject | None,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    categories = await list_categories(session)
    if not categories:
        await message.answer(
            "📭 Hozircha hech qanday kategoriya yo'q.\n"
            "Yangi kategoriya qo'shish uchun /add_category"
        )
        return

    if command and command.args and command.args.strip().isdigit():
        category_id = int(command.args.strip())
        await _render_category_books(message, session, category_id)
        return

    await _show_category_selection(message, categories, page=1)


async def _render_category_books(
    message: Message,
    session: AsyncSession,
    category_id: uuid.UUID,
    as_edit: bool = False,
) -> None:
    category = await get_category_by_id(session, category_id)
    if not category:
        if as_edit:
            await message.edit_text("❌ Kategoriya topilmadi.")
        else:
            await message.answer("❌ Kategoriya topilmadi.")
        return

    books = await list_books_by_category(session, category_id)
    if not books:
        builder = InlineKeyboardBuilder()
        builder.button(text="📖 Kitob qo'shish", callback_data=f"add_book_from_cat:{category_id}")
        builder.button(text="🔙 Ortga", callback_data="list_back_to_categories")
        builder.button(text="❌ Bekor qilish", callback_data="list_cancel")
        builder.adjust(2)
        if as_edit:
            await message.edit_text(
                f"📚 <b>{category.name} kategoriyasida kitob yo'q</b>",
                reply_markup=builder.as_markup(),
            )
        else:
            await message.answer(
                f"📚 <b>{category.name} kategoriyasida kitob yo'q</b>",
                reply_markup=builder.as_markup(),
            )
        return

    lines = [f"📚 <b>{category.name} kategoriyasidagi kitoblar</b> ({len(books)} ta)", ""]
    for i, book in enumerate(books, 1):
        lines.append(f"{i}. 📖 {book.title}")
        if book.author:
            lines.append(f"   ✍️ {book.author}")
        if book.description:
            lines.append(f"   📝 {_truncate(book.description)}")
        lines.append(f"   🆔 ID: {book.id}")
        lines.append("")

    builder = InlineKeyboardBuilder()
    builder.button(text="📖 Kitob qo'shish", callback_data=f"add_book_from_cat:{category_id}")
    builder.button(text="✏️ Tahrirlash", callback_data=f"list_edit_hint:{category_id}")
    builder.button(text="🗑️ O'chirish", callback_data=f"list_remove_hint:{category_id}")
    builder.button(text="🔙 Ortga", callback_data="list_back_to_categories")
    builder.button(text="❌ Bekor qilish", callback_data="list_cancel")
    builder.adjust(2)
    if as_edit:
        await message.edit_text("\n".join(lines).strip(), reply_markup=builder.as_markup())
    else:
        await send_chunked_message(message, "\n".join(lines).strip(), reply_markup=builder.as_markup())

    await _send_book_cover_previews(message, books, limit=5)






@router.message(BookAddStates.cover, Command("skip"))
async def add_book_cover_skip(message: Message, state: FSMContext) -> None:
    await state.update_data(cover_image=None)
    await state.set_state(BookAddStates.confirm)
    await show_add_book_confirm(message, state)


@router.message(BookAddStates.cover)
async def add_book_cover(message: Message, state: FSMContext) -> None:
    if message.text:
        text = message.text.strip()
        if text in ("/cancel", "❌ Bekor qilish"):
            return
        if text in ("/skip", "⏭️ O'tkazib yuborish"):
            await add_book_cover_skip(message, state)
            return

    cover_file_id: str | None = None
    if message.photo:
        cover_file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        cover_file_id = message.document.file_id

    if not cover_file_id:
        await message.answer(
            "❌ Iltimos, rasm yuboring yoki ⏭️ O'tkazib yuborish tugmasini bosing."
        )
        return

    await message.answer("⏳ Rasm yuklanmoqda...")
    try:
        cover_path = await _save_cover_file(message, cover_file_id)
    except Exception as exc:
        logger.error("Failed to save book cover", exc_info=True)
        await message.answer("Qaytadan urinib ko'ring.")
        return

    await state.update_data(cover_image=cover_path)
    data = await state.get_data()
    category_name = data.get("category_name", "Noma'lum")
    title = data.get("title", "Noma'lum")

    confirm_text = [
        "📋 Kitob ma'lumotlari:",
        f"📚 Kategoriya: {category_name}",
        f"📖 Nomi: {title}",
        "🖼️ Rasm: ✅ Ha",
        "",
        "✅ Kitobni qo'shish?",
        "❌ Bekor qilish",
    ]

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="book_add_confirm")
    builder.button(text="❌ Bekor qilish", callback_data="book_add_cancel")
    builder.adjust(2)

    await state.set_state(BookAddStates.confirm)
    await message.answer("\n".join(confirm_text), reply_markup=builder.as_markup())


@router.message(BookAddStates.cover, Command("cancel"))
@router.message(BookAddStates.cover, F.text == "❌ Bekor qilish")
async def add_book_cover_cancel(
    message: Message,
    state: FSMContext,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.callback_query(lambda c: c.data.startswith("list_category:"))
async def list_books_by_category_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    try:
        category_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    await _render_category_books(callback.message, session, category_id, as_edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("list_cat_page:"))
async def list_categories_page(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri sahifa.", show_alert=True)
        return

    categories = await list_categories(session)
    if not categories:
        await callback.answer("📭 Kategoriyalar yo'q.", show_alert=True)
        return

    keyboard = _build_category_keyboard(categories, page=page).as_markup()
    await callback.message.edit_text("📚 <b>Kategoriyani tanlang:</b>", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data == "list_back_to_categories")
async def list_back_to_categories(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    categories = await list_categories(session)
    if not categories:
        await callback.answer("📭 Kategoriyalar yo'q.", show_alert=True)
        return

    keyboard = _build_category_keyboard(categories, page=1).as_markup()
    await callback.message.edit_text("📚 <b>Kategoriyani tanlang:</b>", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("list_cat_info:"))
async def list_cat_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(lambda c: c.data == "list_cancel")
async def list_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    await state.clear()
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher, is_librarian=is_librarian)
    await callback.message.edit_text("❌ Bekor qilindi.")
    await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("add_book_from_cat:"))
async def add_book_from_category(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        category_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    category = await get_category_by_id(session, category_id)
    if not category:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return

    await _show_predefined_book_picker(
        message=callback.message,
        state=state,
        category_id=category_id,
        category_name=category.name,
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("list_edit_hint:"))
async def list_edit_hint(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        category_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return
    category = await get_category_by_id(session, category_id)
    if not category:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return
    await state.update_data(edit_category_id=category.id, edit_category_name=category.name)
    await state.set_state(BookEditStates.select_book)
    await _show_edit_books(callback.message, session, category.id, category.name, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("list_remove_hint:"))
async def list_remove_hint(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        category_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return
    category = await get_category_by_id(session, category_id)
    if not category:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return
    await state.update_data(delete_category_id=category.id, delete_category_name=category.name)
    await state.set_state(BookDeleteStates.select_book)
    await _show_delete_books(callback.message, session, category.id, category.name, edit=True)
    await callback.answer()


async def show_add_book_confirm(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    title = data.get("title")
    cover_image = data.get("cover_image")
    category_name = data.get("category_name") or "Noma'lum"

    lines = [
        "📋 Kitob ma'lumotlari:",
        f"📚 Kategoriya: {category_name}",
        f"📖 Nomi: {title}",
    ]
    cover_text = "✅ Ha" if cover_image else "❌ Yo'q"
    lines.append(f"🖼️ Rasm: {cover_text}")

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="book_add_confirm")
    builder.button(text="❌ Bekor qilish", callback_data="book_add_cancel")
    builder.adjust(2)

    await send_chunked_message(message, "\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data == "book_add_confirm")
async def confirm_add_book(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    data = await state.get_data()
    category_id = data.get("category_id")
    title = data.get("title")
    cover_image = data.get("cover_image")

    if not category_id or not title:
        await callback.answer("❌ Ma'lumotlar yetarli emas.", show_alert=True)
        return

    book = await add_book(session, int(category_id), title, None, None, cover_image)
    category_name = data.get("category_name", "Noma'lum")
    await callback.message.edit_text(
        f"✅ Kitob qo'shildi.\n"
        f"📖 Nomi: {book.title}\n"
        f"📚 Kategoriya: {category_name}"
    )
    await state.clear()
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )
    await callback.message.answer("📋 Asosiy menyu:", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data == "book_add_cancel")
async def cancel_add_book(
    callback: CallbackQuery,
    state: FSMContext,
    is_superadmin: bool = False,
    is_teacher: bool = False,
    is_librarian: bool = False,
) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Jarayon bekor qilindi.")
    keyboard = get_main_keyboard(
        is_superadmin=is_superadmin,
        is_teacher=is_teacher,
        is_librarian=is_librarian,
    )
    await callback.message.answer("📋 Asosiy menyu:", reply_markup=keyboard)
    await callback.answer()


@router.message(Command("remove_book"))
async def cmd_remove_book(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Hozircha hech qanday kategoriya yo'q.")
        return
    await state.set_state(BookDeleteStates.select_category)
    await _show_delete_categories(message, categories, edit=False)


@router.callback_query(lambda c: c.data.startswith("deletebook_cat:"))
async def delete_book_select_category(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        category_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    category = await get_category_by_id(session, category_id)
    if not category:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return

    await state.update_data(delete_category_id=category.id, delete_category_name=category.name)
    await state.set_state(BookDeleteStates.select_book)
    await _show_delete_books(callback.message, session, category.id, category.name, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data == "deletebook_back_to_cats")
async def delete_book_back_to_categories(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    categories = await list_categories(session)
    if not categories:
        await callback.answer("📭 Hozircha hech qanday kategoriya yo'q.", show_alert=True)
        return
    await state.set_state(BookDeleteStates.select_category)
    await _show_delete_categories(callback.message, categories, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("deletebook_confirm:"))
async def delete_book_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    book = await get_book_by_id(session, book_id)
    if not book:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return

    await state.update_data(delete_book_id=book.id, delete_book_title=book.title)
    await state.set_state(BookDeleteStates.confirm)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data="deletebook_yes"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data="deletebook_no"),
    )
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="deletebook_cancel"))

    await callback.message.edit_text(
        f"📖 <b>{book.title}</b> kitobini o'chirmoqchimisiz?",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "deletebook_yes")
async def delete_book_execute(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    book_id = data.get("delete_book_id")
    book_title = data.get("delete_book_title") or "Noma'lum"

    if not book_id:
        await callback.answer("Qaytadan urinib ko'ring.", show_alert=True)
        return

    book = await get_book_by_id(session, int(book_id))
    if book:
        await remove_book(session, book)
        await callback.message.edit_text(f"✅ Kitob o'chirildi: {book_title}")
    else:
        await callback.message.edit_text("❌ Kitob topilmadi.")

    category_id = data.get("delete_category_id")
    category_name = data.get("delete_category_name") or "Noma'lum"
    await state.set_state(BookDeleteStates.select_book)
    if category_id:
        await _show_delete_books(callback.message, session, int(category_id), category_name, edit=False)
    await callback.answer()


@router.callback_query(lambda c: c.data == "deletebook_no")
async def delete_book_cancel_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    category_id = data.get("delete_category_id")
    category_name = data.get("delete_category_name") or "Noma'lum"
    await state.set_state(BookDeleteStates.select_book)
    if category_id:
        await _show_delete_books(callback.message, session, int(category_id), category_name, edit=False)
    await callback.answer("Bekor qilindi.")


@router.message(Command("edit_book"))
async def cmd_edit_book(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Hozircha hech qanday kategoriya yo'q.")
        return
    await state.set_state(BookEditStates.select_category)
    await _show_edit_categories(message, categories, edit=False)


@router.callback_query(lambda c: c.data.startswith("editbook_cat:"))
async def edit_book_select_category(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        category_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    category = await get_category_by_id(session, category_id)
    if not category:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return

    await state.update_data(edit_category_id=category.id, edit_category_name=category.name)
    await state.set_state(BookEditStates.select_book)
    await _show_edit_books(callback.message, session, category.id, category.name, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data == "editbook_back_to_cats")
async def edit_book_back_to_categories(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    categories = await list_categories(session)
    if not categories:
        await callback.answer("📭 Kategoriyalar yo'q.", show_alert=True)
        return
    await state.set_state(BookEditStates.select_category)
    await _show_edit_categories(callback.message, categories, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("editbook_select:"))
async def edit_book_select_book(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    book = await get_book_by_id(session, book_id)
    if not book:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return

    await state.update_data(edit_book_id=book.id, edit_book_title=book.title)
    await state.set_state(BookEditStates.edit_field)
    await _show_edit_fields(callback.message, book.title)
    await callback.answer()


@router.callback_query(lambda c: c.data == "editbook_back_to_books")
async def edit_book_back_to_books(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    category_id = data.get("edit_category_id")
    category_name = data.get("edit_category_name") or "Noma'lum"
    if not category_id:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return
    await state.set_state(BookEditStates.select_book)
    await _show_edit_books(callback.message, session, int(category_id), category_name, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("editbook_field:"))
async def edit_book_select_field(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    field = callback.data.split(":", 1)[1]
    await state.update_data(edit_field=field)

    if field == "category":
        categories = await list_categories(session)
        if not categories:
            await callback.answer("📭 Hozircha hech qanday kategoriya yo'q.", show_alert=True)
            return
        builder = InlineKeyboardBuilder()
        for category in categories:
            builder.button(text=category.name, callback_data=f"editbook_newcat:{category.id}")
        builder.adjust(2)
        builder.row(
            InlineKeyboardButton(text="🔙 Ortga", callback_data="editbook_back_to_fields"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="editbook_cancel"),
        )
        await state.set_state(BookEditStates.select_new_category)
        await callback.message.edit_text("📚 Yangi kategoriyani tanlang:", reply_markup=builder.as_markup())
        await callback.answer()
        return

    if field == "all":
        data = await state.get_data()
        book_id = data.get("edit_book_id")
        if not book_id:
            await callback.answer("❌ Kitob topilmadi.", show_alert=True)
            return
        await state.update_data(book_id=book_id, new_title=None, new_author=None, new_description=None, cover_image_set=False)
        await state.set_state(BookEditStates.title)
        await callback.message.delete()
        await callback.message.answer(
            "✏️ Yangi kitob nomini kiriting yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
            reply_markup=get_skip_cancel_keyboard(),
        )
        await callback.answer()
        return

    if field == "cover":
        await state.set_state(BookEditStates.edit_value)
        await callback.message.delete()
        await callback.message.answer(
            "🖼️ Yangi rasm yuboring (ixtiyoriy).\n"
            "⏭️ O'tkazib yuborish tugmasini bosing.",
            reply_markup=get_skip_cancel_keyboard(),
        )
        await callback.answer()
        return

    await callback.message.edit_text("❌ Noto'g'ri tanlov.")
    await callback.answer()


@router.callback_query(lambda c: c.data == "editbook_back_to_fields")
async def edit_book_back_to_fields(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    book_id = data.get("edit_book_id")
    if not book_id:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return
    book = await get_book_by_id(session, int(book_id))
    if not book:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return
    await state.set_state(BookEditStates.edit_field)
    await _show_edit_fields(callback.message, book.title)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("editbook_newcat:"))
async def edit_book_update_category(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        new_category_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    data = await state.get_data()
    book_id = data.get("edit_book_id")
    if not book_id:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return

    book = await get_book_by_id(session, int(book_id))
    if not book:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return

    new_category = await get_category_by_id(session, new_category_id)
    if not new_category:
        await callback.answer("❌ Kategoriya topilmadi.", show_alert=True)
        return

    old_category = await get_category_by_id(session, book.category_id) if book.category_id else None
    old_name = old_category.name if old_category else "Noma'lum"

    book.category_id = new_category.id
    await session.commit()
    await session.refresh(book)

    await callback.message.edit_text(
        "✅ Kitob kategoriyasi o'zgartirildi:\n"
        f"📖 Nomi: {book.title}\n"
        f"📚 Eski kategoriya: {old_name}\n"
        f"📚 Yangi kategoriya: {new_category.name}"
    )

    await state.update_data(
        edit_category_id=new_category.id,
        edit_category_name=new_category.name,
    )
    await state.set_state(BookEditStates.select_book)
    await _show_edit_books(callback.message, session, new_category.id, new_category.name, edit=False)
    await callback.answer()


@router.message(BookEditStates.edit_value, F.text)
async def edit_book_value_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.text and message.text.strip() == "❌ Bekor qilish":
        return
    data = await state.get_data()
    field = data.get("edit_field")
    book_id = data.get("edit_book_id")
    if not book_id:
        await message.answer("❌ Kitob topilmadi.")
        await state.clear()
        return

    if field == "cover":
        text = (message.text or "").strip()
        if text in ("⏭️ O'tkazib yuborish", "/skip"):
            await message.answer("ℹ️ Rasm o'zgartirilmadi.")
            category_id = data.get("edit_category_id")
            category_name = data.get("edit_category_name") or "Noma'lum"
            await state.set_state(BookEditStates.select_book)
            if category_id:
                await _show_edit_books(message, session, int(category_id), category_name, edit=False)
            return

        await message.answer("❌ Iltimos, rasm yuboring yoki ⏭️ O'tkazib yuborish tugmasini bosing.")
        return

    await message.answer("❌ Noto'g'ri tanlov.")


@router.message(BookEditStates.edit_value, F.photo)
async def edit_book_value_photo(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    field = data.get("edit_field")
    if field != "cover":
        return

    book_id = data.get("edit_book_id")
    if not book_id:
        await message.answer("❌ Kitob topilmadi.")
        await state.clear()
        return

    try:
        cover_path = await _save_cover_file(message, message.photo[-1].file_id)
    except Exception:
        logger.error("Failed to save edited cover", exc_info=True)
        await message.answer("Qaytadan urinib ko'ring.")
        return

    book = await get_book_by_id(session, int(book_id))
    if not book:
        await message.answer("❌ Kitob topilmadi.")
        await state.clear()
        return

    await update_book(session, book, cover_image=cover_path)
    await message.answer("✅ Kitob yangilandi.")

    category_id = data.get("edit_category_id")
    category_name = data.get("edit_category_name") or "Noma'lum"
    await state.set_state(BookEditStates.select_book)
    if category_id:
        await _show_edit_books(message, session, int(category_id), category_name, edit=False)


@router.callback_query(lambda c: c.data.startswith("editbook_remove:"))
async def edit_book_remove(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    try:
        book_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    book = await get_book_by_id(session, book_id)
    if not book:
        await callback.answer("❌ Kitob topilmadi.", show_alert=True)
        return

    await remove_book(session, book)

    data = await state.get_data()
    category_id = data.get("edit_category_id")
    category_name = data.get("edit_category_name") or "Noma'lum"
    if category_id:
        await _show_edit_books(callback.message, session, int(category_id), category_name, edit=True)
    await callback.answer("✅ Kitob o'chirildi.")


@router.message(BookEditStates.title, Command("skip"))
@router.message(BookEditStates.title, F.text == "⏭️ O'tkazib yuborish")
async def edit_book_title_skip(message: Message, state: FSMContext) -> None:
    await state.update_data(new_title=None)
    await state.set_state(BookEditStates.author)
    await message.answer(
        "✍️ Yangi muallifni kiriting yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.title)
async def edit_book_title(message: Message, state: FSMContext) -> None:
    await state.update_data(new_title=(message.text or "").strip() or None)
    await state.set_state(BookEditStates.author)
    await message.answer(
        "✍️ Yangi muallifni kiriting yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.author, Command("skip"))
@router.message(BookEditStates.author, F.text == "⏭️ O'tkazib yuborish")
async def edit_book_author_skip(message: Message, state: FSMContext) -> None:
    await state.update_data(new_author=None)
    await state.set_state(BookEditStates.description)
    await message.answer(
        "📝 Yangi tavsifni kiriting yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.author)
async def edit_book_author(message: Message, state: FSMContext) -> None:
    await state.update_data(new_author=(message.text or "").strip() or None)
    await state.set_state(BookEditStates.description)
    await message.answer(
        "📝 Yangi tavsifni kiriting yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.description, Command("skip"))
@router.message(BookEditStates.description, F.text == "⏭️ O'tkazib yuborish")
async def edit_book_description_skip(message: Message, state: FSMContext) -> None:
    await state.update_data(new_description=None)
    await state.set_state(BookEditStates.cover)
    await message.answer(
        "🖼️ Yangi rasm yuboring (ixtiyoriy) yoki ⏭️ O'tkazib yuborish tugmasini bosing.\n"
        "❌ Bekor qilish tugmasini bosing.",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.description)
async def edit_book_description(message: Message, state: FSMContext) -> None:
    await state.update_data(new_description=(message.text or "").strip() or None)
    await state.set_state(BookEditStates.cover)
    await message.answer(
        "🖼️ Yangi rasm yuboring (ixtiyoriy) yoki ⏭️ O'tkazib yuborish tugmasini bosing.\n"
        "❌ Bekor qilish tugmasini bosing.",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.cover, Command("skip"))
@router.message(BookEditStates.cover, F.text == "⏭️ O'tkazib yuborish")
async def edit_book_cover_skip(message: Message, state: FSMContext) -> None:
    await state.update_data(cover_image_set=False)
    await state.set_state(BookEditStates.availability)
    await message.answer(
        "📦 Mavjudligini kiriting (ha/yo'q) yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.cover, F.photo)
async def edit_book_cover(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    cover_path = await _save_cover_file(message, photo.file_id)
    await state.update_data(new_cover_image=cover_path, cover_image_set=True)
    await state.set_state(BookEditStates.availability)
    await message.answer(
        "📦 Mavjudligini kiriting (ha/yo'q) yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(BookEditStates.cover, F.text & ~F.text.startswith("/"))
async def edit_book_cover_invalid(message: Message) -> None:
    await message.answer(
        "❌ Iltimos, rasm yuboring yoki ⏭️ O'tkazib yuborish tugmasini bosing."
    )


@router.message(BookEditStates.availability, Command("skip"))
@router.message(BookEditStates.availability, F.text == "⏭️ O'tkazib yuborish")
async def edit_book_availability_skip(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    data = await state.get_data()
    book_id = data.get("book_id")
    cover_image = data.get("new_cover_image") if data.get("cover_image_set") else None
    if not book_id:
        await message.answer("❌ Kitob topilmadi.")
        await state.clear()
        return

    book = await get_book_by_id(session, int(book_id))
    if not book:
        await message.answer("❌ Kitob topilmadi.")
        await state.clear()
        return

    await update_book(
        session,
        book,
        title=data.get("new_title"),
        author=data.get("new_author"),
        description=data.get("new_description"),
        cover_image=cover_image,
        is_available=None,
    )
    await state.clear()
    await message.answer("✅ Kitob yangilandi.")


@router.message(BookEditStates.availability)
async def edit_book_availability(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    text = (message.text or "").strip().lower()
    if text not in ("ha", "yo'q", "yoq", "yes", "no"):
        await message.answer("❌ Iltimos, ha yoki yo'q deb javob bering.")
        return

    data = await state.get_data()
    book_id = data.get("book_id")
    cover_image = data.get("new_cover_image") if data.get("cover_image_set") else None
    if not book_id:
        await message.answer("❌ Kitob topilmadi.")
        await state.clear()
        return

    book = await get_book_by_id(session, int(book_id))
    if not book:
        await message.answer("❌ Kitob topilmadi.")
        await state.clear()
        return

    is_available = text in ("ha", "yes")
    await update_book(
        session,
        book,
        title=data.get("new_title"),
        author=data.get("new_author"),
        description=data.get("new_description"),
        cover_image=cover_image,
        is_available=is_available,
    )
    await state.clear()
    await message.answer("✅ Kitob yangilandi.")
