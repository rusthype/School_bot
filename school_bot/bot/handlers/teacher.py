from __future__ import annotations
import html
import os
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, FSInputFile, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, PollAnswer
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.orm import selectinload

from school_bot.bot.services.poll_service import send_task_poll
from school_bot.bot.services.group_service import list_groups, get_group_by_id, get_groups_by_names
from school_bot.bot.services.task_service import create_task
from school_bot.bot.states.new_task import NewTaskStates
from school_bot.database.models import Task, PollVote
from school_bot.bot.handlers.common import get_main_keyboard, get_teacher_votes_keyboard
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.user_service import get_or_create_user

router = Router(name=__name__)
logger = get_logger(__name__)

# Rasm saqlanadigan papka
PHOTO_DIR = "photos"
os.makedirs(PHOTO_DIR, exist_ok=True)

POLL_OPTIONS = [
    "1️⃣ Juda yaxshi tushundi, mashqlarni mustaqil bajara olmoqda.",
    "2️⃣ Biroz tushundi, lekin yana ko'proq mashq qilish kerak.",
    "3️⃣ Mavzuni tushundi, ammo mashq bajarishda qiynalmoqda.",
    "4️⃣ Umuman tushunmadi.",
]


def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tanlang..."
    )


def get_skip_cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⏭️ O'tkazib yuborish"), KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tanlang..."
    )


@router.message(StateFilter(NewTaskStates), Command("cancel"))
@router.message(StateFilter(NewTaskStates), F.text == "❌ Bekor qilish")
async def cancel_new_task(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    """Cancel new task creation from any NewTaskStates step."""
    await state.clear()
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher or is_superadmin)
    await message.answer("✅ Jarayon bekor qilindi.", reply_markup=keyboard)


# ============== NEW TASK ==============
@router.message(Command("new_task"))
async def cmd_new_task(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    profile,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    start_time = time.time()
    if not (is_teacher or is_superadmin):
        await message.answer("⛔ Bu buyruq faqat tasdiqlangan o'qituvchilar uchun.")
        return

    await state.clear()
    logger.info(
        f"Foydalanuvchi /new_task ni ishga tushirdi: {message.from_user.id}",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "new_task"},
    )

    # Guruhlarni ko'rsatish (faqat assigned guruhlar)
    if is_superadmin:
        groups = await list_groups(session)
    elif profile:
        assigned_groups = profile.assigned_groups or []
        groups = await get_groups_by_names(session, assigned_groups)
    else:
        # Legacy teacherlar uchun barcha guruhlar
        groups = await list_groups(session)

    if not groups:
        await message.answer("❌ Hech qanday guruh biriktirilmagan. Administrator bilan bog'lanishingiz kerak.")
        return

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"task_group:{group.id}")
    builder.adjust(1)

    await state.set_state(NewTaskStates.group_selection)
    prompt = await message.answer(
        "📋 Qaysi guruhga topshiriq yubormoqchisiz?\n\n"
        "❌ Bekor qilish uchun /cancel bosing",
        reply_markup=builder.as_markup()
    )
    await state.update_data(last_prompt_message_id=prompt.message_id)
    execution_time = time.time() - start_time
    logger.info(
        f"/new_task bajarildi: {execution_time:.2f}s",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "new_task", "exec_ms": int(execution_time * 1000)},
    )


@router.callback_query(lambda c: c.data.startswith("task_group:"))
async def process_group_selection(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    profile,
    is_superadmin: bool = False,
):
    try:
        group_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer("❌ Guruh topilmadi!")
        return

    group = await get_group_by_id(session, group_id)
    if not group:
        await callback.answer("❌ Guruh topilmadi!")
        return

    if not is_superadmin and profile:
        allowed = set(profile.assigned_groups if profile else [])
        if group.name not in allowed:
            logger.warning(
                f"Guruh ruxsati yo'q: {group.name}",
                extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "new_task"},
            )
            await callback.answer("⛔ Sizga bu guruh biriktirilmagan.")
            return

    await state.update_data(
        selected_group=group.name,
        selected_group_id=group.chat_id,
    )

    await state.set_state(NewTaskStates.topic)
    await callback.message.delete()
    await callback.message.answer(
        "📌 Mavzuni kiriting: \n\n❌ Bekor qilish uchun /cancel bosing",
        reply_markup=get_cancel_keyboard(),
    )
    await callback.answer()


@router.message(NewTaskStates.topic, F.text)
async def process_topic(message: Message, state: FSMContext) -> None:
    """Mavzu kiritishni qayta ishlash"""
    topic = (message.text or "").strip()
    if not topic:
        logger.warning(
            "Bo'sh mavzu kiritildi",
            extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "new_task"},
        )
        await message.answer("Mavzu bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    await state.update_data(topic=topic)
    await state.set_state(NewTaskStates.description)
    await message.answer(
        "🏠 Uyga vazifani kiriting: \n\n❌ Bekor qilish uchun /cancel bosing",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(NewTaskStates.description, F.text)
async def process_description(message: Message, state: FSMContext) -> None:
    """Vazifa kiritishni qayta ishlash"""
    description = (message.text or "").strip()
    if not description:
        logger.warning(
            "Bo'sh vazifa kiritildi",
            extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "new_task"},
        )
        await message.answer("Vazifa bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    await state.update_data(description=description)
    await state.set_state(NewTaskStates.notes)
    await message.answer(
        "📝 Qo'shimcha izoh (ixtiyoriy):\n"
        "Masalan: ertaga muhokama qilamiz, darsda tekshiriladi\n\n"
        "⏭️ O'tkazib yuborish tugmasini bosing\n"
        "❌ Bekor qilish tugmasini bosing.",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(NewTaskStates.notes, Command("skip"))
async def skip_notes(
    message: Message,
    state: FSMContext,
) -> None:
    """Izohni o'tkazib yuborish"""
    await state.update_data(notes=None)
    await state.set_state(NewTaskStates.photo)
    await message.answer(
        "📸 Rasm jo'natishingiz mumkin (ixtiyoriy).\n"
        "⏭️ O'tkazib yuborish tugmasini bosing.\n"
        "❌ Bekor qilish tugmasini bosing.",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(NewTaskStates.notes, F.text == "⏭️ O'tkazib yuborish")
async def skip_notes_button(message: Message, state: FSMContext) -> None:
    await skip_notes(message, state)


@router.message(NewTaskStates.notes, F.text)
async def process_notes(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    text = (message.text or "").strip()
    if text in ("/skip", "⏭️ O'tkazib yuborish"):
        await skip_notes(message, state)
        return

    await state.update_data(notes=text or None)
    await state.set_state(NewTaskStates.photo)
    await message.answer(
        "📸 Rasm jo'natishingiz mumkin (ixtiyoriy).\n"
        "⏭️ O'tkazib yuborish tugmasini bosing.\n"
        "❌ Bekor qilish tugmasini bosing.",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(NewTaskStates.photo, F.photo)
async def process_photo(
        message: Message,
        state: FSMContext,
        bot,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    # Rasmni yuklab olish
    photo = message.photo[-1]  # Eng katta rasm
    file = await bot.get_file(photo.file_id)

    # Rasmni saqlash
    file_name = f"{PHOTO_DIR}/task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{photo.file_id}.jpg"
    await bot.download_file(file.file_path, file_name)

    await state.update_data(photo_path=file_name)

    # Rasm bilan yakunlash
    await finish_task_with_photo(message, state, session, db_user, is_superadmin)


@router.message(NewTaskStates.photo, Command("skip"))
async def skip_photo(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    await finish_task_without_photo(message, state, session, db_user, is_superadmin)


@router.message(NewTaskStates.photo, F.text == "⏭️ O'tkazib yuborish")
async def skip_photo_button(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    await finish_task_without_photo(message, state, session, db_user, is_superadmin)


@router.message(NewTaskStates.photo)
async def invalid_photo(message: Message) -> None:
    if message.text and message.text.strip() in ("/cancel", "❌ /cancel", "❌ Bekor qilish"):
        return
    await message.answer(
        "Iltimos, rasm yuboring yoki ⏭️ O'tkazib yuborish tugmasini bosing.\n"
        "❌ Bekor qilish tugmasini bosing."
    )


async def finish_task_with_photo(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False
) -> None:
    data = await state.get_data()
    topic = data["topic"]
    description = data["description"]
    notes = data.get("notes")
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

    # Poll yuboramiz
    poll_message = await send_task_poll(
        bot=message.bot,
        group_chat_id=group_id,
        topic=topic,
        description=description,
        notes=notes,
        poll_options=POLL_OPTIONS,
    )

    # Database ga saqlash
    task = await create_task(
        session=session,
        teacher_id=db_user.id,
        topic=topic,
        description=description,
        poll_message_id=poll_message.message_id,
        poll_id=poll_message.poll.id if poll_message.poll else None,
    )

    logger.info(
        f"Topshiriq yaratildi: ID={task.id}, Teacher={db_user.telegram_id}, Topic={topic}, Group={group_name}",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "new_task"},
    )

    await state.clear()

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=True)
    await message.answer(
        f"✅ Topshiriq muvaffaqiyatli yaratildi!\n\n"
        f"📌 Guruh: {group_name}\n"
        f"📸 Rasm qo'shildi.\n"
        f"📊 So'rovnoma guruhga yuborildi.",
        reply_markup=keyboard
    )


async def finish_task_without_photo(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False
) -> None:
    data = await state.get_data()
    topic = data["topic"]
    description = data["description"]
    notes = data.get("notes")
    group_id = data["selected_group_id"]
    group_name = data["selected_group"]

    # Poll yuboramiz
    poll_message = await send_task_poll(
        bot=message.bot,
        group_chat_id=group_id,
        topic=topic,
        description=description,
        notes=notes,
        poll_options=POLL_OPTIONS,
    )

    # Database ga saqlash
    task = await create_task(
        session=session,
        teacher_id=db_user.id,
        topic=topic,
        description=description,
        poll_message_id=poll_message.message_id,
        poll_id=poll_message.poll.id if poll_message.poll else None,
    )

    logger.info(
        f"Topshiriq yaratildi: ID={task.id}, Teacher={db_user.telegram_id}, Topic={topic}, Group={group_name}",
        extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "new_task"},
    )

    await state.clear()

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superadmin=is_superadmin, is_teacher=True)
    await message.answer(
        f"✅ Topshiriq muvaffaqiyatli yaratildi!\n\n"
        f"📌 Guruh: {group_name}\n"
        f"📊 So'rovnoma guruhga yuborildi.",
        reply_markup=keyboard
    )


@router.message(NewTaskStates.topic)
async def invalid_topic(message: Message) -> None:
    if message.text and message.text.strip() in ("/cancel", "❌ /cancel", "❌ Bekor qilish"):
        return
    await message.answer("Iltimos, mavzuni matn ko'rinishida yuboring:")


@router.message(NewTaskStates.description)
async def invalid_description(message: Message) -> None:
    if message.text and message.text.strip() in ("/cancel", "❌ /cancel", "❌ Bekor qilish"):
        return
    await message.answer("Iltimos, vazifani matn ko'rinishida yuboring:")


@router.message(NewTaskStates.notes)
async def invalid_notes(message: Message) -> None:
    if message.text and message.text.strip() in ("/cancel", "❌ /cancel", "❌ Bekor qilish"):
        return
    await message.answer(
        "Iltimos, izohni matn ko'rinishida yuboring yoki ⏭️ O'tkazib yuborish tugmasini bosing."
    )


@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer, session: AsyncSession) -> None:
    poll_id = poll_answer.poll_id
    if not poll_id:
        return

    task_res = await session.execute(
        select(Task).where(Task.poll_id == poll_id)
    )
    task = task_res.scalar_one_or_none()
    if not task:
        return

    tg_user = poll_answer.user
    full_name = " ".join(
        part for part in [tg_user.first_name, tg_user.last_name] if part
    ).strip() or None
    user = await get_or_create_user(
        session=session,
        telegram_id=tg_user.id,
        full_name=full_name,
        username=tg_user.username,
    )

    await session.execute(
        delete(PollVote).where(
            PollVote.poll_id == poll_id,
            PollVote.user_id == user.id,
        )
    )

    if not poll_answer.option_ids:
        await session.commit()
        return

    votes: list[PollVote] = []
    for option_id in poll_answer.option_ids:
        option_text = (
            POLL_OPTIONS[option_id]
            if option_id < len(POLL_OPTIONS)
            else f"Variant {option_id + 1}"
        )
        votes.append(
            PollVote(
                poll_message_id=task.poll_message_id,
                poll_id=poll_id,
                task_id=task.id,
                user_id=user.id,
                option_id=option_id,
                option_text=option_text,
            )
        )

    session.add_all(votes)
    await session.commit()


@router.message(Command("poll_voters"))
async def cmd_poll_voters(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    db_user,
    command: CommandObject | None = None,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await message.answer("⛔ Bu komanda faqat o'qituvchilar uchun.")
        return

    args = command.args if command else None
    if not args:
        stmt = select(Task).order_by(Task.created_at.desc()).limit(5)
        if not is_superadmin:
            stmt = stmt.where(Task.teacher_id == db_user.id)
        tasks = (await session.execute(stmt)).scalars().all()

        if not tasks:
            await message.answer("📭 Siz hali hech qanday topshiriq yaratmagansiz.")
            return

        builder = InlineKeyboardBuilder()
        for task in tasks:
            date = task.created_at.strftime("%d.%m")
            topic = task.topic or "Topshiriq"
            short_topic = topic[:20] + ("..." if len(topic) > 20 else "")
            builder.button(
                text=f"{date}: {short_topic}",
                callback_data=f"poll_voters:{task.id}",
            )
        builder.adjust(1)

        await message.answer(
            "📊 Qaysi topshiriq uchun ovozlarni ko'rmoqchisiz?",
            reply_markup=builder.as_markup(),
        )
        return

    try:
        task_id = int(args)
    except ValueError:
        await message.answer("❌ Noto'g'ri format. Iltimos, topshiriq ID sini kiriting.")
        return

    await show_poll_voters(message, session, task_id, db_user.id, is_superadmin)


@router.callback_query(lambda c: c.data.startswith("poll_voters:"))
async def poll_voters_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        await callback.answer("⛔ Bu amal faqat o'qituvchilar uchun.", show_alert=True)
        return

    try:
        task_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("❌ Noto'g'ri ID", show_alert=True)
        return

    await callback.message.delete()
    await show_poll_voters(callback.message, session, task_id, db_user.id, is_superadmin)
    await callback.answer()


def format_poll_voters(task: Task) -> str:
    votes_by_option: dict[int, list[PollVote]] = {}
    for vote in task.poll_votes:
        votes_by_option.setdefault(vote.option_id, []).append(vote)

    safe_topic = html.escape(task.topic or "")
    safe_description = html.escape(task.description or "")
    lines = [
        f"📊 <b>Topshiriq: {safe_topic}</b>",
        f"📅 {task.created_at.strftime('%d.%m.%Y %H:%M')}",
    ]
    if safe_description:
        lines.append(f"📝 {safe_description}")
    lines.extend(
        [
            "",
            f"<b>Jami ovozlar: {len(task.poll_votes)} ta</b>",
        ]
    )

    for option_id in sorted(votes_by_option.keys()):
        votes = sorted(votes_by_option[option_id], key=lambda v: v.voted_at or datetime.min)
        option_text = (
            POLL_OPTIONS[option_id]
            if option_id < len(POLL_OPTIONS)
            else f"Variant {option_id + 1}"
        )
        lines.append("")
        lines.append(f"<b>{html.escape(option_text)}</b> ({len(votes)} ta):")

        for vote in votes[:10]:
            user = vote.user
            name = user.full_name or f"Foydalanuvchi {user.telegram_id}"
            username = f" (@{user.username})" if user.username else ""
            time_str = vote.voted_at.strftime("%H:%M") if vote.voted_at else ""
            lines.append(f"• {html.escape(name)}{html.escape(username)} - {time_str}")

        if len(votes) > 10:
            lines.append(f"... va yana {len(votes) - 10} ta")

    return "\n".join(lines)


async def show_poll_voters(
    message: Message,
    session: AsyncSession,
    task_id: uuid.UUID,
    teacher_id: uuid.UUID,
    is_superadmin: bool = False,
) -> None:
    stmt = (
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.poll_votes).selectinload(PollVote.user))
    )
    if not is_superadmin:
        stmt = stmt.where(Task.teacher_id == teacher_id)

    task = (await session.execute(stmt)).scalar_one_or_none()
    if not task:
        await message.answer("❌ Topshiriq topilmadi yoki sizga tegishli emas.")
        return

    if not task.poll_id:
        await message.answer("📭 Bu topshiriq uchun so'rovnoma yo'q.")
        return

    if not task.poll_votes:
        await message.answer("📭 Bu topshiriq uchun hali ovoz berilmagan.")
        return

    text = format_poll_voters(task)
    await message.answer(text)


# ============== TEACHER VOTES SUBMENU HANDLERS ==============

@router.message(F.text == "📋 Joriy topshiriqlar")
async def teacher_current_tasks_handler(
    message: Message,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return

    stmt = (
        select(Task)
        .where(Task.teacher_id == db_user.id)
        .order_by(Task.created_at.desc())
        .limit(20)
    )
    tasks = (await session.execute(stmt)).scalars().all()

    if not tasks:
        await message.answer(
            "Hozircha topshiriqlar yo'q.",
            reply_markup=get_teacher_votes_keyboard(),
        )
        return

    lines: list[str] = ["📋 <b>Joriy topshiriqlar:</b>", ""]
    for task in tasks:
        desc_preview = (task.description or "")[:80]
        if len(task.description or "") > 80:
            desc_preview += "..."
        date_str = task.created_at.strftime("%d.%m.%Y")
        lines.append(f"#{task.id} — <b>{html.escape(task.topic)}</b>")
        lines.append(f"📝 {html.escape(desc_preview)}")
        lines.append(f"🕒 {date_str}")
        lines.append("")

    from school_bot.bot.utils.telegram import send_chunked_message
    await send_chunked_message(
        message,
        "\n".join(lines).strip(),
        reply_markup=get_teacher_votes_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "📊 Baholar jurnali")
async def teacher_gradebook_handler(
    message: Message,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return

    stmt = (
        select(Task)
        .where(Task.teacher_id == db_user.id)
        .options(selectinload(Task.poll_votes))
        .order_by(Task.created_at.desc())
        .limit(20)
    )
    tasks = (await session.execute(stmt)).scalars().all()

    if not tasks:
        await message.answer(
            "Baholar jurnali bo'sh.",
            reply_markup=get_teacher_votes_keyboard(),
        )
        return

    lines: list[str] = ["📊 <b>Baholar jurnali:</b>", ""]
    for task in tasks:
        votes_by_option: dict[int, int] = {}
        for vote in task.poll_votes:
            votes_by_option[vote.option_id] = votes_by_option.get(vote.option_id, 0) + 1

        lines.append(f"📋 <b>{html.escape(task.topic)}</b>")
        if votes_by_option:
            for option_id in sorted(votes_by_option.keys()):
                option_text = (
                    POLL_OPTIONS[option_id]
                    if option_id < len(POLL_OPTIONS)
                    else f"Variant {option_id + 1}"
                )
                count = votes_by_option[option_id]
                lines.append(f"  {html.escape(option_text)}: {count} ta")
        else:
            lines.append("  — ovozlar yo'q")
        lines.append("")

    from school_bot.bot.utils.telegram import send_chunked_message
    await send_chunked_message(
        message,
        "\n".join(lines).strip(),
        reply_markup=get_teacher_votes_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == "📈 O'rtacha ball")
async def teacher_average_scores_handler(
    message: Message,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
    is_superadmin: bool = False,
) -> None:
    if not (is_teacher or is_superadmin):
        return

    # Fetch all tasks for this teacher to get their IDs
    task_stmt = select(Task.id).where(Task.teacher_id == db_user.id)
    task_ids = (await session.execute(task_stmt)).scalars().all()

    if not task_ids:
        await message.answer(
            "Hali ovozlar yo'q.",
            reply_markup=get_teacher_votes_keyboard(),
        )
        return

    # Count votes per user across all tasks by this teacher
    from school_bot.database.models import User as UserModel
    vote_count_stmt = (
        select(PollVote.user_id, func.count(PollVote.id).label("vote_count"))
        .where(PollVote.task_id.in_(task_ids))
        .group_by(PollVote.user_id)
        .order_by(func.count(PollVote.id).desc())
        .limit(20)
    )
    vote_rows = (await session.execute(vote_count_stmt)).all()

    if not vote_rows:
        await message.answer(
            "Hali ovozlar yo'q.",
            reply_markup=get_teacher_votes_keyboard(),
        )
        return

    # Fetch user names
    user_ids = [row.user_id for row in vote_rows]
    users_stmt = select(UserModel).where(UserModel.id.in_(user_ids))
    users = {u.id: u for u in (await session.execute(users_stmt)).scalars().all()}

    lines: list[str] = ["📈 <b>Faol ishtirokchilar:</b>", ""]
    for rank, row in enumerate(vote_rows, start=1):
        user = users.get(row.user_id)
        name = (user.full_name or f"Foydalanuvchi {user.telegram_id}") if user else f"ID {row.user_id}"
        lines.append(f"{rank}. {html.escape(name)} — {row.vote_count} ta javob")

    from school_bot.bot.utils.telegram import send_chunked_message
    await send_chunked_message(
        message,
        "\n".join(lines),
        reply_markup=get_teacher_votes_keyboard(),
        parse_mode="HTML",
    )
