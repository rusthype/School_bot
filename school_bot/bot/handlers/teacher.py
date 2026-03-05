from __future__ import annotations
import logging
import os
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from school_bot.bot.config import Settings
from school_bot.bot.services.poll_service import send_task_poll
from school_bot.bot.services.task_service import create_task
from school_bot.bot.states.new_task import NewTaskStates
from school_bot.bot.states.book_order import BookOrderStates
from school_bot.database.models import User, UserRole
from school_bot.bot.handlers.common import get_main_keyboard

router = Router(name=__name__)

# Rasm saqlanadigan papka
PHOTO_DIR = "photos"
os.makedirs(PHOTO_DIR, exist_ok=True)


# ============== NEW TASK ==============
@router.message(Command("new_task"))
async def cmd_new_task(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    if not is_teacher:
        await message.answer("⛔ Only teachers can create new tasks.")
        return

    await state.clear()

    # Guruhlarni ko'rsatish
    settings = Settings()
    groups = settings.groups

    if not groups:
        await message.answer("❌ Hech qanday guruh sozlanmagan. Administrator bilan bog'lanishingiz kerak.")
        return

    builder = InlineKeyboardBuilder()
    for group_name in groups.keys():
        builder.button(text=group_name, callback_data=f"group_{group_name}")
    builder.adjust(1)

    await state.set_state(NewTaskStates.group_selection)
    await message.answer(
        "📋 Qaysi guruhga topshiriq yubormoqchisiz?\n\n"
        "❌ Bekor qilish uchun /cancel bosing",
        reply_markup=builder.as_markup()
    )


@router.callback_query(lambda c: c.data.startswith("group_"))
async def process_group_selection(callback: CallbackQuery, state: FSMContext):
    group_name = callback.data.replace("group_", "")
    settings = Settings()
    groups = settings.groups

    if group_name not in groups:
        await callback.answer("❌ Guruh topilmadi!")
        return

    await state.update_data(
        selected_group=group_name,
        selected_group_id=groups[group_name]
    )

    await state.set_state(NewTaskStates.topic)
    await callback.message.delete()
    await callback.message.answer(
        "📌 Mavzuni kiriting: \n\n❌ Bekor qilish uchun /cancel bosing")
    await callback.answer()


@router.message(NewTaskStates.group_selection, Command("cancel"))
async def cancel_group_selection(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Guruh tanlash bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.message(NewTaskStates.topic, Command("cancel"))
async def cancel_topic(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Mavzu kiritish bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.message(NewTaskStates.topic, F.text)
async def process_topic(message: Message, state: FSMContext) -> None:
    """Mavzu kiritishni qayta ishlash"""
    topic = (message.text or "").strip()
    if not topic:
        await message.answer("Mavzu bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    await state.update_data(topic=topic)
    await state.set_state(NewTaskStates.description)
    await message.answer(
        "🏠 Uyga vazifani kiriting: \n\n❌ Bekor qilish uchun /cancel bosing")


@router.message(NewTaskStates.description, Command("cancel"))
async def cancel_description(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Vazifa kiritish bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.message(NewTaskStates.description, F.text)
async def process_description(message: Message, state: FSMContext) -> None:
    """Vazifa kiritishni qayta ishlash"""
    description = (message.text or "").strip()
    if not description:
        await message.answer("Vazifa bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    await state.update_data(description=description)
    await state.set_state(NewTaskStates.photo)
    await message.answer(
        "📸 Rasm jo'natishingiz mumkin (ixtiyoriy).\n"
        "Agar rasm kerak bo'lmasa /skip ni bosing.\n"
        "Bekor qilish uchun /cancel bosing."
    )


@router.message(NewTaskStates.photo, Command("cancel"))
async def cancel_photo(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Rasm yuklash bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


@router.message(NewTaskStates.photo, F.photo)
async def process_photo(
        message: Message,
        state: FSMContext,
        bot,
        session: AsyncSession,
        db_user
) -> None:
    # Rasmni yuklab olish
    photo = message.photo[-1]  # Eng katta rasm
    file = await bot.get_file(photo.file_id)

    # Rasmni saqlash
    file_name = f"{PHOTO_DIR}/task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{photo.file_id}.jpg"
    await bot.download_file(file.file_path, file_name)

    await state.update_data(photo_path=file_name)

    # Rasm bilan yakunlash
    await finish_task_with_photo(message, state, session, db_user)


@router.message(NewTaskStates.photo, Command("skip"))
async def skip_photo(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user
) -> None:
    await finish_task_without_photo(message, state, session, db_user)


@router.message(NewTaskStates.photo)
async def invalid_photo(message: Message) -> None:
    await message.answer(
        "Iltimos, rasm yuboring yoki /skip bosing.\n"
        "Agar rasm kerak bo'lmasa /skip ni bosing.\n"
        "Bekor qilish uchun /cancel bosing."
    )


async def finish_task_with_photo(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user
) -> None:
    data = await state.get_data()
    topic = data["topic"]
    description = data["description"]
    photo_path = data.get("photo_path")
    group_id = data["selected_group_id"]
    group_name = data["selected_group"]

    # Rasmni yuboramiz
    if photo_path and os.path.exists(photo_path):
        photo = FSInputFile(photo_path)
        await message.bot.send_photo(
            chat_id=group_id,
            photo=photo
        )

    # Poll variantlari
    poll_options = [
        "1️⃣ Juda yaxshi tushundi, mashqlarni mustaqil bajara olmoqda.",
        "2️⃣ Biroz tushundi, lekin yana ko'proq mashq qilish kerak.",
        "3️⃣ Mavzuni tushundi, ammo mashq bajarishda qiynalmoqda.",
        "4️⃣ Umuman tushunmadi."
    ]

    # Poll yuboramiz
    poll_message = await send_task_poll(
        bot=message.bot,
        group_chat_id=group_id,
        topic=topic,
        description=description,
        poll_options=poll_options,
    )

    # Database ga saqlash
    task = await create_task(
        session=session,
        teacher_id=db_user.id,
        topic=topic,
        description=description,
        poll_message_id=poll_message.message_id,
    )

    logging.info(f"Task created: ID={task.id}, Teacher={db_user.telegram_id}, Topic={topic}, Group={group_name}")

    await state.clear()

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=True)
    await message.answer(
        f"✅ Topshiriq muvaffaqiyatli yaratildi!\n\n"
        f"📌 Guruh: {group_name}\n"
        f"📸 Rasm qo'shildi.\n"
        f"📊 Poll guruhga yuborildi.",
        reply_markup=keyboard
    )


async def finish_task_without_photo(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user
) -> None:
    data = await state.get_data()
    topic = data["topic"]
    description = data["description"]
    group_id = data["selected_group_id"]
    group_name = data["selected_group"]

    # Poll variantlari
    poll_options = [
        "1️⃣ Juda yaxshi tushundi, mashqlarni mustaqil bajara olmoqda.",
        "2️⃣ Biroz tushundi, lekin yana ko'proq mashq qilish kerak.",
        "3️⃣ Mavzuni tushundi, ammo mashq bajarishda qiynalmoqda.",
        "4️⃣ Umuman tushunmadi."
    ]

    # Poll yuboramiz
    poll_message = await send_task_poll(
        bot=message.bot,
        group_chat_id=group_id,
        topic=topic,
        description=description,
        poll_options=poll_options,
    )

    # Database ga saqlash
    task = await create_task(
        session=session,
        teacher_id=db_user.id,
        topic=topic,
        description=description,
        poll_message_id=poll_message.message_id,
    )

    logging.info(f"Task created: ID={task.id}, Teacher={db_user.telegram_id}, Topic={topic}, Group={group_name}")

    await state.clear()

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=True)
    await message.answer(
        f"✅ Topshiriq muvaffaqiyatli yaratildi!\n\n"
        f"📌 Guruh: {group_name}\n"
        f"📊 Poll guruhga yuborildi.",
        reply_markup=keyboard
    )


@router.message(NewTaskStates.topic)
async def invalid_topic(message: Message) -> None:
    await message.answer("Iltimos, mavzuni matn ko'rinishida yuboring:")


@router.message(NewTaskStates.description)
async def invalid_description(message: Message) -> None:
    await message.answer("Iltimos, vazifani matn ko'rinishida yuboring:")


# ============== BOOK ORDER ==============
@router.message(Command("order_book"))
async def cmd_order_book(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    if not is_teacher:
        await message.answer("⛔ Bu komanda faqat teacherlar uchun.")
        return

    await state.set_state(BookOrderStates.book_name)
    await message.answer(
        "📚 Kitob nomini kiriting:\n"
        "Masalan: Python asoslari\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(BookOrderStates.book_name, Command("cancel"))
async def cancel_book_name(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Kitob nomi kiritish bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Buyurtma bekor qilindi.", reply_markup=keyboard)


@router.message(BookOrderStates.book_name, F.text)
async def process_book_name(message: Message, state: FSMContext) -> None:
    book_name = (message.text or "").strip()
    if not book_name:
        await message.answer("❌ Kitob nomi bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    await state.update_data(book_name=book_name)
    await state.set_state(BookOrderStates.book_author)
    await message.answer(
        "✍️ Muallifni kiriting (ixtiyoriy):\n"
        "Masalan: Anvar Narzullayev\n\n"
        "O'tkazib yuborish uchun /skip bosing\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(BookOrderStates.book_author, Command("cancel"))
async def cancel_book_author(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Muallif kiritish bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Buyurtma bekor qilindi.", reply_markup=keyboard)


@router.message(BookOrderStates.book_author, F.text)
async def process_book_author(message: Message, state: FSMContext) -> None:
    author = (message.text or "").strip()
    await state.update_data(book_author=author or None)
    await state.set_state(BookOrderStates.book_quantity)
    await message.answer(
        "🔢 Nechta nusxa kerak? (son kiriting):\n"
        "Masalan: 5\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(BookOrderStates.book_author, Command("skip"))
async def skip_book_author(message: Message, state: FSMContext) -> None:
    await state.update_data(book_author=None)
    await state.set_state(BookOrderStates.book_quantity)
    await message.answer(
        "🔢 Nechta nusxa kerak? (son kiriting):\n"
        "Masalan: 5\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(BookOrderStates.book_quantity, Command("cancel"))
async def cancel_book_quantity(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Soni kiritish bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Buyurtma bekor qilindi.", reply_markup=keyboard)


@router.message(BookOrderStates.book_quantity, F.text)
async def process_book_quantity(message: Message, state: FSMContext) -> None:
    try:
        quantity = int(message.text.strip())
        if quantity < 1 or quantity > 100:
            await message.answer("❌ 1 dan 100 gacha son kiriting:")
            return
    except ValueError:
        await message.answer("❌ Iltimos, to'g'ri son kiriting:")
        return

    await state.update_data(book_quantity=quantity)
    await state.set_state(BookOrderStates.book_notes)
    await message.answer(
        "📝 Qo'shimcha ma'lumot (ixtiyoriy):\n"
        "Masalan: 7-sinf uchun, qattiq muqovada\n\n"
        "O'tkazib yuborish uchun /skip bosing\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(BookOrderStates.book_notes, Command("cancel"))
async def cancel_book_notes(message: Message, state: FSMContext, is_teacher: bool = False) -> None:
    """Qo'shimcha ma'lumot kiritish bosqichida cancel"""
    await state.clear()
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await message.answer("✅ Buyurtma bekor qilindi.", reply_markup=keyboard)


@router.message(BookOrderStates.book_notes, F.text)
async def process_book_notes(message: Message, state: FSMContext) -> None:
    notes = (message.text or "").strip()
    await state.update_data(book_notes=notes or None)
    await show_order_confirmation(message, state)


@router.message(BookOrderStates.book_notes, Command("skip"))
async def skip_book_notes(message: Message, state: FSMContext) -> None:
    await state.update_data(book_notes=None)
    await show_order_confirmation(message, state)


async def show_order_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()

    book_name = data["book_name"]
    author = data.get("book_author")
    quantity = data["book_quantity"]
    notes = data.get("book_notes")

    # Tasdiqlash matni
    confirm_text = [
        "📋 **Kitob buyurtma ma'lumotlari:**",
        f"📚 Kitob: {book_name}",
    ]

    if author:
        confirm_text.append(f"✍️ Muallif: {author}")

    confirm_text.append(f"🔢 Soni: {quantity} ta")

    if notes:
        confirm_text.append(f"📝 Qo'shimcha: {notes}")

    confirm_text.extend([
        "",
        "✅ Buyurtmani tasdiqlaysizmi?",
        "❌ Bekor qilish"
    ])

    # Inline keyboard
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Tasdiqlash", callback_data="order_confirm")
    builder.button(text="❌ Bekor qilish", callback_data="order_cancel")
    builder.adjust(2)

    await state.set_state(BookOrderStates.confirm)
    await message.answer(
        "\n".join(confirm_text),
        reply_markup=builder.as_markup()
    )


@router.callback_query(lambda c: c.data == "order_confirm")
async def confirm_order(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user):
    data = await state.get_data()

    book_name = data["book_name"]
    author = data.get("book_author", "Noma'lum")
    quantity = data["book_quantity"]
    notes = data.get("book_notes", "Yo'q")

    # Barcha superuserlarni olish
    result = await session.execute(
        select(User).where(User.role == UserRole.superuser)
    )
    superusers = result.scalars().all()

    # Superuserlarga xabar yuborish
    order_message = (
        f"📚 **YANGI KITOB BUYURTMA**\n\n"
        f"👨‍🏫 O'qituvchi: {db_user.full_name or db_user.telegram_id}\n"
        f"📖 Kitob: {book_name}\n"
        f"✍️ Muallif: {author}\n"
        f"🔢 Soni: {quantity} ta\n"
        f"📝 Qo'shimcha: {notes}\n\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )

    # Har bir superuserga yuborish
    for superuser in superusers:
        try:
            await callback.bot.send_message(
                chat_id=superuser.telegram_id,
                text=order_message
            )
            logging.info(f"Buyurtma superuserga yuborildi: {superuser.telegram_id}")
        except Exception as e:
            logging.error(f"Superuserga yuborishda xatolik {superuser.telegram_id}: {e}")

    await callback.message.edit_text("✅ Buyurtma muvaffaqiyatli yuborildi! Administratorlar xabarni oldi.")
    await state.clear()

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=True)
    await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data == "order_cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext, db_user):
    """Buyurtmani bekor qilish"""
    await state.clear()
    await callback.message.edit_text("❌ Buyurtma bekor qilindi.")

    # Foydalanuvchi teacher ekanligini tekshirish
    is_teacher = (db_user.role == UserRole.teacher) if db_user else True

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superuser=False, is_teacher=is_teacher)
    await callback.message.answer("Asosiy menyu", reply_markup=keyboard)
    await callback.answer()


@router.message(BookOrderStates.book_name)
async def invalid_book_name(message: Message) -> None:
    await message.answer("❌ Iltimos, kitob nomini matn ko'rinishida yuboring.")


@router.message(BookOrderStates.book_quantity)
async def invalid_book_quantity(message: Message) -> None:
    await message.answer("❌ Iltimos, to'g'ri son kiriting.")
