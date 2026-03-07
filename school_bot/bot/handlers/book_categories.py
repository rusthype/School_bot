from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.handlers.common import get_main_keyboard
from school_bot.bot.services.book_service import (
    list_categories,
    add_category,
    get_category_by_id,
    get_category_by_name,
    update_category,
    remove_category,
    count_books_in_category,
    ALLOWED_CATEGORY_NAMES,
)
from school_bot.bot.services.logger_service import get_logger

router = Router(name=__name__)
logger = get_logger(__name__)
_ADD_CATEGORY_RE = re.compile(r"^/add_category(?:@\\w+)?(?:\\s+(.+))?$", re.IGNORECASE)


@router.message(Command("add_category"))
async def cmd_add_category(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    text = (message.text or "").strip()
    raw_args = None
    match = _ADD_CATEGORY_RE.match(text)
    if match:
        raw_args = match.group(1)

    if not raw_args:
        await message.answer(
            "❌ Kategoriya nomini yozing.\n"
            "Ishlatilishi: /add_category [nomi]\n"
            "Masalan: /add_category 1-sinf"
        )
        return

    name = raw_args.strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"'):
        name = name[1:-1].strip()
    if name not in ALLOWED_CATEGORY_NAMES:
        await message.answer("❌ Faqat 1-sinf, 2-sinf, 3-sinf, 4-sinf kategoriyalariga ruxsat berilgan.")
        return
    existing = await get_category_by_name(session, name)
    if existing:
        await message.answer(f"❌ '{name}' kategoriyasi allaqachon mavjud.")
        return

    try:
        category = await add_category(session, name=name)
        await message.answer(f"✅ Kategoriya qo'shildi: {category.name}")
    except Exception as exc:
        logger.error("Error adding category", exc_info=True)
        await message.answer("Qaytadan urinib ko'ring.")


@router.message(Command("list_categories"))
async def cmd_list_categories(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    categories = await list_categories(session)
    if not categories:
        await message.answer("📭 Hozircha kategoriya yo'q.")
        return

    lines = ["📚 **Kategoriyalar ro'yxati:**", ""]
    for category in categories:
        lines.append(f"{category.id}. {category.name}")

    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await message.answer("\n".join(lines), reply_markup=keyboard)


@router.message(Command("edit_category"))
async def cmd_edit_category(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    if not command.args:
        await message.answer("Ishlatilishi: /edit_category [id] [yangi_nom]")
        return

    parts = command.args.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.answer("Ishlatilishi: /edit_category [id] [yangi_nom]")
        return

    category_id = int(parts[0])
    new_name = parts[1].strip()
    category = await get_category_by_id(session, category_id)
    if not category:
        await message.answer("❌ Kategoriya topilmadi.")
        return

    existing = await get_category_by_name(session, new_name)
    if existing and existing.id != category.id:
        await message.answer("ℹ️ Bu nom bilan kategoriya allaqachon mavjud.")
        return

    await update_category(session, category, name=new_name)
    await message.answer(f"✅ Kategoriya yangilandi: {new_name}")


@router.message(Command("remove_category"))
async def cmd_remove_category(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    if not command.args or not command.args.strip().isdigit():
        await message.answer("Ishlatilishi: /remove_category [id]")
        return

    category_id = int(command.args.strip())
    category = await get_category_by_id(session, category_id)
    if not category:
        await message.answer("❌ Kategoriya topilmadi.")
        return

    count = await count_books_in_category(session, category_id)
    if count > 0:
        await message.answer("❌ Kategoriya bo'sh emas. Avval kitoblarni o'chiring.")
        return

    await remove_category(session, category)
    await message.answer("✅ Kategoriya o'chirildi.")
