from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter, BaseFilter
from aiogram.fsm.context import FSMContext

from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from school_bot.bot.services.user_service import (
    remove_teacher_role,
    set_teacher_role,
    get_or_create_user,
    get_user_by_username,
)
from school_bot.bot.services.group_service import (
    list_groups,
    add_group,
    get_group_by_id,
    get_group_by_name,
    get_group_by_chat_id,
    update_group,
    remove_group,
    set_invite_link,
    list_groups_by_school,
    list_pending_groups,
)
from school_bot.bot.services.school_service import list_schools, get_school_by_id
from school_bot.bot.services.profile_service import (
    get_profile_by_id,
    get_profile_by_user_id,
    approve_profile,
    reject_profile,
    revoke_teacher,
    update_teacher_profile,
    update_teacher_user,
    update_teacher_groups,
)
from school_bot.bot.services.approval_service import (
    build_approval_keyboard,
    build_school_keyboard,
    get_selected_group_ids,
    toggle_selected_group,
    clear_selections_for_profile,
    set_selected_school,
    get_selected_school,
)
from school_bot.bot.utils.parser import parse_telegram_input
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.pagination import SchoolPagination
from school_bot.bot.states.group_management import GroupManagementStates
from school_bot.bot.states.admin_states import (
    AddTeacherManualStates,
    AddTeacherByIdStates,
    RemoveTeacherStates,
    RejectTeacherStates,
    TeacherEditStates,
)
from school_bot.database.models import User, UserRole, Task, Profile, School, PollVote
from school_bot.bot.handlers.group_join import _build_group_join_school_keyboard
from school_bot.bot.handlers.common import (
    get_main_keyboard,
    get_users_management_keyboard,
    cancel_current_action,
    get_skip_cancel_keyboard,
    show_groups_menu,
)

MAX_TG_MESSAGE = 4000
_STALE_APPROVAL_MSG = "Bu so'rov allaqachon ko'rib chiqilgan yoki muddati o'tgan."
POLLS_PAGE_SIZE = 20


def _split_message(text: str, limit: int = MAX_TG_MESSAGE):
    chunks = []
    current = []
    size = 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if size + line_len > limit and current:
            chunks.append("\n".join(current))
            current = [line]
            size = line_len
        else:
            current.append(line)
            size += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


async def _send_chunked_message(message: Message, text: str) -> None:
    for chunk in _split_message(text):
        await message.answer(chunk)


PAGE_SIZE_TEACHERS = 10
PAGE_SIZE_GROUPS = 15


def _build_page_keyboard(prefix: str, page: int, total_pages: int) -> InlineKeyboardMarkup | None:
    if total_pages <= 1:
        return None
    builder = InlineKeyboardBuilder()
    if page > 1:
        builder.button(text="◀️ Oldingi", callback_data=f"{prefix}:{page - 1}")
    builder.button(text=f"📍 {page}/{total_pages}", callback_data=f"{prefix}_info:{page}")
    if page < total_pages:
        builder.button(text="▶️ Keyingi", callback_data=f"{prefix}:{page + 1}")
    builder.adjust(3)
    return builder.as_markup()


async def _send_teachers_page(target: Message, session: AsyncSession, page: int, edit: bool = False) -> None:
    total = await session.scalar(
        select(func.count()).select_from(User).where(
            User.role == UserRole.teacher,
        )
    )
    total = total or 0
    total_pages = max(1, (total + PAGE_SIZE_TEACHERS - 1) // PAGE_SIZE_TEACHERS)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE_TEACHERS

    result = await session.execute(
        select(User, Profile, School)
        .join(Profile, User.id == Profile.bot_user_id)
        .outerjoin(School, Profile.school_id == School.id)
        .where(User.role == UserRole.teacher)
        .order_by(User.created_at)
        .offset(offset)
        .limit(PAGE_SIZE_TEACHERS)
    )
    teachers_data = result.all()

    teacher_buttons: list[tuple[str, int]] = []
    if not teachers_data:
        result_message = "📭 Hozircha hech qanday o'qituvchi yo'q."
    else:
        lines = [f"👨‍🏫 O'qituvchilar ({page}/{total_pages})", f"Jami: {total} ta", ""]
        for i, (teacher, profile, school) in enumerate(teachers_data, offset + 1):
            full_name = f"{profile.first_name} {profile.last_name or ''}".strip() or teacher.full_name or "Ism yo'q"
            username = f"@{teacher.username}" if teacher.username else "Yo'q"
            phone = profile.phone if profile.phone else "Yo'q"
            school_name = school.name if school else "Biriktirilmagan"
            groups = profile.assigned_groups or []
            groups_text = ", ".join(groups) if groups else "Yo'q"
            lines.append(f"{i}. 🆔 {teacher.telegram_id}")
            lines.append(f"   👤 {full_name}")
            lines.append(f"   🔹 Username: {username}")
            lines.append(f"   📱 Telefon: {phone}")
            lines.append(f"   🏫 Maktab: {school_name}")
            lines.append(f"   📚 Guruhlar: {groups_text}")
            lines.append("")
            teacher_buttons.append((full_name, teacher.id))
        result_message = "\n".join(lines).strip()

    builder = InlineKeyboardBuilder()
    for label, uid in teacher_buttons:
        builder.row(InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"td:{uid}"))  # max: 3+1+19 = 23 bytes
    page_keyboard = _build_page_keyboard("teachers_page", page, total_pages)
    if page_keyboard:
        for row in page_keyboard.inline_keyboard:
            builder.row(*row)
    keyboard = builder.as_markup() if teacher_buttons or page_keyboard else None
    if edit:
        await target.edit_text(result_message, reply_markup=keyboard)
    else:
        await target.answer(result_message, reply_markup=keyboard)


async def _send_groups_page(target: Message, session: AsyncSession, page: int, edit: bool = False) -> None:
    groups = await list_groups(session)
    total = len(groups)
    if total == 0:
        result_message = "📭 Hozircha hech qanday guruh yo'q. /add_group bilan qo'shing."
        keyboard = None
    else:
        total_pages = max(1, (total + PAGE_SIZE_GROUPS - 1) // PAGE_SIZE_GROUPS)
        page = max(1, min(page, total_pages))
        start = (page - 1) * PAGE_SIZE_GROUPS
        end = start + PAGE_SIZE_GROUPS
        page_groups = groups[start:end]
        schools = await list_schools(session)
        school_map = {s.id: s.name for s in schools}
        lines = [f"📚 Guruhlar ({page}/{total_pages})", f"Jami: {total} ta", ""]
        for group in page_groups:
            school_name = school_map.get(group.school_id, "Maktab biriktirilmagan")
            lines.append(f"• {group.name} — {school_name}")
        result_message = "\n".join(lines).strip()
        keyboard = _build_page_keyboard("groups_page", page, total_pages)

    if edit:
        await target.edit_text(result_message, reply_markup=keyboard)
    else:
        await target.answer(result_message, reply_markup=keyboard)


async def _send_groups_ids_page(target: Message, session: AsyncSession, page: int, edit: bool = False) -> None:
    groups = await list_groups(session, include_pending=False)
    total = len(groups)
    if total == 0:
        result_message = "📭 Hozircha hech qanday guruh yo'q. /add_group bilan qo'shing."
        keyboard = None
    else:
        total_pages = max(1, (total + PAGE_SIZE_GROUPS - 1) // PAGE_SIZE_GROUPS)
        page = max(1, min(page, total_pages))
        start = (page - 1) * PAGE_SIZE_GROUPS
        end = start + PAGE_SIZE_GROUPS
        page_groups = groups[start:end]
        schools = await list_schools(session)
        school_map = {s.id: s.name for s in schools}
        lines = [f"🆔 Guruhlar va chat IDlar ({page}/{total_pages})", f"Jami: {total} ta", ""]
        for group in page_groups:
            school_name = school_map.get(group.school_id, "Maktab biriktirilmagan")
            lines.append(f"• {group.name} — `{group.chat_id}` ({school_name})")
        result_message = "\n".join(lines).strip()
        keyboard = _build_page_keyboard("groups_ids_page", page, total_pages)

    if edit:
        await target.edit_text(result_message, reply_markup=keyboard)
    else:
        await target.answer(result_message, reply_markup=keyboard)


router = Router(name=__name__)


@router.message(Command("all_polls"))
async def cmd_all_polls(
        message: Message,
        session: AsyncSession,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu buyruq faqat superadminlar uchun.")
        return
    await show_all_polls_list(message, session, edit=False)


async def show_all_polls_list(
        message: Message,
        session: AsyncSession,
        edit: bool = False,
) -> None:
    result = await session.execute(
        select(Task)
        .where(Task.poll_id.is_not(None))
        .order_by(Task.created_at.desc())
        .limit(POLLS_PAGE_SIZE)
    )
    polls = result.scalars().all()
    if not polls:
        text = "📭 Hozircha so'rovnomalar yo'q."
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return

    lines = ["📊 **Barcha so'rovnomalar**", f"Jami: {len(polls)} ta", ""]
    builder = InlineKeyboardBuilder()
    for poll in polls:
        date = poll.created_at.strftime("%d.%m") if poll.created_at else "--"
        topic = poll.topic or "Topshiriq"
        short_topic = topic[:30] + ("..." if len(topic) > 30 else "")
        lines.append(f"📅 {date} · {short_topic}")
        builder.button(text=f"📅 {date}: {short_topic}", callback_data=f"admin_poll_all_view:{poll.id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_poll_cancel"))

    text = "\n".join(lines).strip()
    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("admin_poll_all_view:"))
async def admin_poll_all_view(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return
    try:
        task_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("❌ Noto'g'ri topshiriq", show_alert=True)
        return

    task = (await session.execute(
        select(Task).where(Task.id == task_id)
    )).scalar_one_or_none()

    if not task:
        await callback.answer("❌ Topshiriq topilmadi!", show_alert=True)
        return
    if not task.poll_id:
        await callback.answer("📭 Bu topshiriq uchun so'rovnoma yo'q.", show_alert=True)
        return

    votes = (await session.execute(
        select(PollVote)
        .where(PollVote.poll_id == task.poll_id)
        .options(selectinload(PollVote.user))
        .order_by(PollVote.voted_at)
    )).scalars().all()

    if not votes:
        await callback.answer("📭 Bu topshiriq uchun hali ovoz berilmagan.", show_alert=True)
        return

    from school_bot.bot.handlers.teacher import format_poll_voters
    text = format_poll_voters(task, votes=list(votes))

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="admin_poll_all_back"))
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_poll_cancel"))

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_poll_all_back")
async def admin_poll_all_back(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return
    await show_all_polls_list(callback.message, session, edit=True)
    await callback.answer()


class SuperadminOnly(BaseFilter):
    async def __call__(self, event, is_superadmin: bool = False, state: FSMContext | None = None, **kwargs) -> bool:
        if is_superadmin:
            return True

        # If user is in FSM flow, don't spam with superadmin warnings
        if state is not None:
            try:
                current_state = await state.get_state()
            except Exception:
                current_state = None
            if current_state is not None:
                return False

        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text.startswith("/") and not text.startswith(("/start", "/help", "/stop", "/cancel")):
                await event.answer("⛔ Bu bo'lim faqat superadminlar uchun.")
        elif isinstance(event, CallbackQuery):
            data = event.data or ""
            if data.startswith("admin_"):
                await event.answer("⛔ Bu bo'lim faqat superadminlar uchun.", show_alert=True)
        return False


router.message.filter(SuperadminOnly())
logger = get_logger(__name__)


def _build_school_keyboard(prefix: str, schools: list, per_row: int = 5) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for school in schools:
        builder.button(text=f"{school.number}-m", callback_data=f"{prefix}:{school.id}")
    builder.adjust(per_row)
    return builder


def _build_school_paged_keyboard(prefix: str, schools: list, page: int, per_page: int = 10) -> InlineKeyboardBuilder:
    pagination = SchoolPagination(page=page, per_page=per_page, total_schools=len(schools))
    start_index = (pagination.page - 1) * pagination.per_page
    end_index = start_index + pagination.per_page
    page_schools = schools[start_index:end_index]

    builder = InlineKeyboardBuilder()
    for school in page_schools:
        builder.button(text=f"{school.number}-m", callback_data=f"{prefix}:{school.id}:{pagination.page}")

    nav_row = []
    if pagination.has_previous():
        nav_row.append(("◀️ Oldingi", f"addgroup_page:{pagination.page - 1}"))
    nav_row.append((f"📍 {pagination.page}/{pagination.total_pages}", f"addgroup_page_info:{pagination.page}"))
    if pagination.has_next():
        nav_row.append(("▶️ Keyingi", f"addgroup_page:{pagination.page + 1}"))

    for text, data in nav_row:
        builder.button(text=text, callback_data=data)

    builder.adjust(5)
    return builder



@router.message(Command(commands=["pending_approvals", "kutayotganlar"]))
async def cmd_pending_approvals(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    result = await session.execute(
        select(Profile)
        .where(
            Profile.is_approved.is_(False),
            Profile.rejected_at.is_(None),
            Profile.removed_at.is_(None),
        )
        .order_by(Profile.registered_at.desc())
    )
    pending = list(result.scalars().all())

    if not pending:
        await message.answer("📭 Hozircha tasdiqlanmagan o'qituvchi yo'q.")
        return

    await message.answer(
        f"⏳ **Tasdiqlanishi kutilayotgan o'qituvchilar** ({len(pending)} ta)"
    )

    for profile in pending:
        user = await session.get(User, profile.bot_user_id)
        username = f"@{user.username}" if user and user.username else "(username yo'q)"
        full_name = f"{profile.first_name} {profile.last_name or ''}".strip()
        requested = profile.registered_at or datetime.now(timezone.utc)
        requested_str = requested.strftime("%d.%m.%Y %H:%M")

        school_name = "Tanlanmagan"
        if profile.school_id:
            school = await get_school_by_id(session, profile.school_id)
            if school:
                school_name = school.name

        text = (
            f"👤 {full_name}\n"
            f"🔹 {username}\n"
            f"📱 {profile.phone}\n"
            f"🏫 Maktab: {school_name}\n"
            f"📅 So'rov: {requested_str}"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Ko'rish / Tasdiqlash", callback_data=f"pending_approve:{profile.id}")
        builder.button(text="❌ Rad etish", callback_data=f"approve_reject:{profile.id}")
        builder.adjust(2)

        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("admin_poll_view:"))
async def view_poll_voters_admin(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return

    try:
        task_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("❌ Noto'g'ri topshiriq", show_alert=True)
        return

    task = (await session.execute(
        select(Task).where(Task.id == task_id)
    )).scalar_one_or_none()

    if not task:
        await callback.answer("❌ Topshiriq topilmadi!", show_alert=True)
        return

    if not task.poll_id:
        await callback.answer("📭 Bu topshiriq uchun so'rovnoma yo'q.", show_alert=True)
        return

    votes = (await session.execute(
        select(PollVote)
        .where(PollVote.poll_id == task.poll_id)
        .options(selectinload(PollVote.user))
        .order_by(PollVote.voted_at)
    )).scalars().all()

    if not votes:
        await callback.answer("📭 Bu topshiriq uchun hali ovoz berilmagan.", show_alert=True)
        return

    from school_bot.bot.handlers.teacher import format_poll_voters
    text = format_poll_voters(task, votes=list(votes))

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="admin_poll_back_to_polls"))
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_poll_cancel"))

    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_poll_back_to_schools")
async def admin_poll_back_to_schools(
        callback: CallbackQuery,
        session: AsyncSession,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return

    await show_all_teachers_overview(callback.message, session, state, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_poll_back_to_teachers")
async def admin_poll_back_to_teachers(
        callback: CallbackQuery,
        session: AsyncSession,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return

    data = await state.get_data()
    school_id = data.get("poll_school_id")
    if not school_id:
        await show_all_teachers_overview(callback.message, session, state, edit=True)
        await callback.answer()
        return
    await show_teachers_by_school(callback.message, session, school_id, state, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_poll_back_to_polls")
async def admin_poll_back_to_polls(
        callback: CallbackQuery,
        session: AsyncSession,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return

    data = await state.get_data()
    teacher_id = data.get("poll_teacher_id")
    if not teacher_id:
        await admin_poll_back_to_teachers(callback, session, state, is_superadmin)
        return

    result = await session.execute(
        select(Task)
        .where(Task.teacher_id == teacher_id)
        .order_by(Task.created_at.desc())
        .limit(20)
    )
    polls = result.scalars().all()

    if not polls:
        await admin_poll_back_to_teachers(callback, session, state, is_superadmin)
        return

    builder = InlineKeyboardBuilder()
    for poll in polls:
        date = poll.created_at.strftime("%d.%m")
        topic = poll.topic or "Topshiriq"
        short_topic = topic[:30] + ("..." if len(topic) > 30 else "")
        builder.button(
            text=f"📅 {date}: {short_topic}",
            callback_data=f"admin_poll_view:{poll.id}",
        )
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="admin_poll_back_to_teachers"))
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_poll_cancel"))

    await callback.message.edit_text(
        "📊 <b>Topshiriqni tanlang:</b>",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_poll_back_to_all")
async def admin_poll_back_to_all(
        callback: CallbackQuery,
        session: AsyncSession,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return

    await show_all_teachers_overview(callback.message, session, state, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data == "admin_poll_cancel")
async def admin_poll_cancel(
        callback: CallbackQuery,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Bu amal faqat superadminlar uchun.", show_alert=True)
        return
    await cancel_current_action(callback, state, is_superadmin=True)


# Umumiy cancel handler - har qanday admin state dan chiqish uchun
@router.message(
    Command("cancel"),
    StateFilter(
        AddTeacherByIdStates, RemoveTeacherStates, GroupManagementStates,
        RejectTeacherStates, TeacherEditStates,
    ),
)
async def cmd_cancel_admin(message: Message, state: FSMContext, is_superadmin: bool = False) -> None:
    """Admin state laridan chiqish"""
    await cancel_current_action(message, state, is_superadmin=is_superadmin)


@router.callback_query(lambda c: c.data.startswith("approve_toggle:"))
async def approval_toggle_group(
        callback: CallbackQuery,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Tasdiqlash faqat superadminlar uchun.", show_alert=True)
        return

    try:
        _, profile_id_str, group_id_str = callback.data.split(":")
        profile_id = int(profile_id_str)
        group_id = int(group_id_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or profile.is_approved or profile.rejected_at or profile.removed_at:
        try:
            await callback.message.edit_text(_STALE_APPROVAL_MSG)
        except Exception:
            pass
        await callback.answer(_STALE_APPROVAL_MSG, show_alert=True)
        return

    school_id = await get_selected_school(db_user.id, profile_id)
    if not school_id:
        await callback.answer("Avval maktabni tanlang.", show_alert=True)
        return

    selected = await toggle_selected_group(db_user.id, profile_id, group_id)
    keyboard = await build_approval_keyboard(session, profile_id, school_id, selected)

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("approve_school:"))
async def approval_select_school(
        callback: CallbackQuery,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Tasdiqlash faqat superadminlar uchun.", show_alert=True)
        return

    try:
        _, profile_id_str, school_id_str = callback.data.split(":")
        profile_id = int(profile_id_str)
        school_id = int(school_id_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri tanlov.", show_alert=True)
        return

    school = await get_school_by_id(session, school_id)
    if not school:
        await callback.answer("❌ Maktab topilmadi.", show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or profile.is_approved or profile.rejected_at or profile.removed_at:
        try:
            await callback.message.edit_text(_STALE_APPROVAL_MSG)
        except Exception:
            pass
        await callback.answer(_STALE_APPROVAL_MSG, show_alert=True)
        return

    user = await session.get(User, profile.bot_user_id)
    username = f"@{user.username}" if user and user.username else "(foydalanuvchi nomi yo'q)"
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()

    requested = profile.registered_at or datetime.now(timezone.utc)
    requested_str = requested.strftime("%d.%m.%Y %H:%M")

    await set_selected_school(db_user.id, profile_id, school.id)
    keyboard = await build_approval_keyboard(session, profile_id, school.id, set())
    groups = await list_groups_by_school(session, school.id)

    text = (
        f"👤 {full_name}\n"
        f"🔹 {username}\n"
        f"📱 {profile.phone}\n"
        f"🏫 Maktab: {school.name}\n"
        f"📅 So'rov: {requested_str}"
    )

    if not groups:
        text += "\n\n⚠️ Bu maktab uchun guruhlar topilmadi."

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("school_page:"))
async def approval_school_page(
        callback: CallbackQuery,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Tasdiqlash faqat superadminlar uchun.", show_alert=True)
        return

    try:
        _, profile_id_str, page_str = callback.data.split(":")
        profile_id = int(profile_id_str)
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or profile.is_approved or profile.rejected_at or profile.removed_at:
        try:
            await callback.message.edit_text(_STALE_APPROVAL_MSG)
        except Exception:
            pass
        await callback.answer(_STALE_APPROVAL_MSG, show_alert=True)
        return

    user = await session.get(User, profile.bot_user_id)
    username = f"@{user.username}" if user and user.username else "(foydalanuvchi nomi yo'q)"
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()

    requested = profile.registered_at or datetime.now(timezone.utc)
    requested_str = requested.strftime("%d.%m.%Y %H:%M")

    current_school_id = await get_selected_school(db_user.id, profile_id)
    current_school_name = "Tanlanmagan"
    if current_school_id:
        current_school = await get_school_by_id(session, current_school_id)
        if current_school:
            current_school_name = current_school.name

    schools = await list_schools(session)
    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = build_school_keyboard(profile_id, schools, page=page, per_page=10)

    text = (
        f"👤 {full_name}\n"
        f"🔹 {username}\n"
        f"📱 {profile.phone}\n"
        f"🏫 Maktab: {current_school_name}\n"
        f"📅 So'rov: {requested_str}"
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("school_page_info:"))
async def approval_school_page_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("approve_confirm:"))
async def approval_confirm(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Tasdiqlash faqat superadminlar uchun.", show_alert=True)
        return

    try:
        profile_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or profile.is_approved or profile.rejected_at or profile.removed_at:
        try:
            await callback.message.edit_text(_STALE_APPROVAL_MSG)
        except Exception:
            pass
        await callback.answer(_STALE_APPROVAL_MSG, show_alert=True)
        return

    school_id = await get_selected_school(db_user.id, profile_id)
    if not school_id:
        await callback.answer("Avval maktabni tanlang.", show_alert=True)
        return
    school = await get_school_by_id(session, school_id)
    school_name = school.name if school else f"{school_id}-maktab"

    selected_ids = await get_selected_group_ids(db_user.id, profile_id)
    if not selected_ids:
        await callback.answer("Kamida bitta guruhni tanlang.", show_alert=True)
        return

    groups = await list_groups_by_school(session, school_id)
    selected_groups = [g for g in groups if str(g.id) in selected_ids]
    if not selected_groups:
        await callback.answer("Tanlangan guruhlar topilmadi.", show_alert=True)
        return
    assigned_names = [g.name for g in selected_groups]

    await approve_profile(session, profile, db_user.id, assigned_names, school_id=school_id)
    await clear_selections_for_profile(profile_id)

    user = await session.get(User, profile.bot_user_id)
    if user:
        assigned_str = ", ".join(assigned_names)
        await callback.bot.send_message(
            chat_id=user.telegram_id,
            text=(
                "🎉 Tabriklaymiz! Ro'yxatdan o'tishingiz tasdiqlandi.\n"
                f"🏫 Maktab: {school_name}\n"
                f"👥 Guruhlar: {assigned_str}"
            ),
        )
        keyboard = get_main_keyboard(is_superadmin=False, is_teacher=True)
        await callback.bot.send_message(
            chat_id=user.telegram_id,
            text="📋 Quyidagi tugmalardan foydalanishingiz mumkin:",
            reply_markup=keyboard,
        )
        logger.info(
            f"Admin {db_user.id} o'qituvchini tasdiqladi: {user.telegram_id} (guruhlar: {assigned_str})",
            extra={"user_id": db_user.telegram_id if hasattr(db_user, "telegram_id") else db_user.id,
                   "chat_id": callback.message.chat.id, "command": "approve"},
        )

    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()
    await callback.message.edit_text(
        f"✅ Tasdiqlandi: {full_name}\nMaktab: {school_name}\nGuruhlar: {', '.join(assigned_names)}"
    )
    await state.clear()
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("pending_approve:"))
async def pending_approve_start(
        callback: CallbackQuery,
        session: AsyncSession,
        db_user,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Tasdiqlash faqat superadminlar uchun.", show_alert=True)
        return

    try:
        profile_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer("❌ Profil topilmadi.", show_alert=True)
        return

    if profile.is_approved:
        await callback.answer("ℹ️ Bu o'qituvchi allaqachon tasdiqlangan.", show_alert=True)
        return

    if profile.rejected_at or profile.removed_at:
        await callback.answer("ℹ️ Bu so'rov endi faol emas.", show_alert=True)
        return

    await clear_selections_for_profile(profile_id)

    user = await session.get(User, profile.bot_user_id)
    username = f"@{user.username}" if user and user.username else "(foydalanuvchi nomi yo'q)"
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()
    requested = profile.registered_at or datetime.now(timezone.utc)
    requested_str = requested.strftime("%d.%m.%Y %H:%M")

    school_name = "Tanlanmagan"
    school = None
    if profile.school_id:
        school = await get_school_by_id(session, profile.school_id)
        if school:
            school_name = school.name

    message_text = (
        "👑 Yangi o'qituvchi ro'yxatdan o'tishi:\n\n"
        f"👤 Ism: {full_name}\n"
        f"🔹 Foydalanuvchi nomi: {username}\n"
        f"📱 Telefon: {profile.phone}\n"
        f"🏫 Tanlangan maktab: {school_name}\n"
        f"📅 So'rov vaqti: {requested_str}"
    )

    if school:
        await set_selected_school(db_user.id, profile_id, school.id)
        keyboard = await build_approval_keyboard(session, profile.id, school.id, set())
        await callback.message.edit_text(
            f"{message_text}\n\n📚 {school.name} uchun guruhlarni tanlang:",
            reply_markup=keyboard,
        )
    else:
        schools = await list_schools(session)
        total_pages = max(1, (len(schools) + 9) // 10)
        keyboard = build_school_keyboard(profile.id, schools, page=1, per_page=10)
        await callback.message.edit_text(
            f"{message_text}\n\n🏫 Maktabni tanlang (1/{total_pages} sahifa):",
            reply_markup=keyboard,
        )

    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("approve_reject:"))
async def approval_reject_start(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Rad etish faqat superadminlar uchun.", show_alert=True)
        return

    try:
        profile_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    profile = await get_profile_by_id(session, profile_id)
    if not profile or profile.is_approved or profile.rejected_at or profile.removed_at:
        try:
            await callback.message.edit_text(_STALE_APPROVAL_MSG)
        except Exception:
            pass
        await callback.answer(_STALE_APPROVAL_MSG, show_alert=True)
        return

    await state.set_state(RejectTeacherStates.waiting_reason)
    await state.update_data(
        profile_id=str(profile_id),
        admin_chat_id=callback.message.chat.id,
        admin_message_id=callback.message.message_id,
    )
    await callback.message.answer(
        "❌ Rad etish sababini yuboring yoki ⏭️ O'tkazib yuborish tugmasini bosing.",
        reply_markup=get_skip_cancel_keyboard(),
    )
    await callback.answer()


async def _perform_reject(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        reason: str | None,
) -> None:
    data = await state.get_data()
    profile_id = data.get("profile_id")
    admin_chat_id = data.get("admin_chat_id")
    admin_message_id = data.get("admin_message_id")

    profile = await get_profile_by_id(session, int(profile_id)) if profile_id else None
    if not profile:
        await message.answer("❌ Ro'yxatdan o'tish profili topilmadi.")
        await state.clear()
        return

    user = await session.get(User, profile.bot_user_id)
    full_name = f"{profile.first_name} {profile.last_name or ''}".strip()

    await reject_profile(session, profile)
    await clear_selections_for_profile(profile.id)

    if user:
        reason_text = f"Sabab: {reason}" if reason else "Sabab: ko'rsatilmagan"
        try:
            await message.bot.send_message(
                chat_id=user.telegram_id,
                text=f"❌ Ro'yxatdan o'tish so'rovingiz rad etildi. {reason_text}",
            )
        except Exception as _send_err:
            logger.warning(
                f"Rad etish xabari yetkazilmadi: {user.telegram_id} — {_send_err}",
                extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "reject"},
            )
        logger.warning(
            f"Admin {message.from_user.id} o'qituvchini rad etdi: {user.telegram_id}, {reason_text}",
            extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "reject"},
        )

    if admin_chat_id and admin_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=admin_chat_id,
                message_id=admin_message_id,
                text=f"❌ Rad etildi: {full_name}",
            )
        except Exception:
            pass

    await state.clear()
    await message.answer("✅ Rad etish yakunlandi.")


@router.message(RejectTeacherStates.waiting_reason, Command("skip"))
@router.message(RejectTeacherStates.waiting_reason, F.text == "⏭️ O'tkazib yuborish")
async def approval_reject_skip(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    await _perform_reject(message, state, session, reason=None)


@router.message(RejectTeacherStates.waiting_reason, F.text)
async def approval_reject_reason(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    reason = (message.text or "").strip()
    await _perform_reject(message, state, session, reason=reason or None)


# ============== ADD TEACHER ==============
@router.message(Command("add_teacher"))
async def cmd_add_teacher_start(
        message: Message,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Siz o'qituvchilarni boshqarish huquqiga ega emassiz.")
        return

    await state.set_state(AddTeacherByIdStates.waiting_for_input)
    await message.answer(
        "➕ O'qituvchi Telegram ID sini kiriting:\n\n/cancel — bekor qilish"
    )


@router.message(StateFilter(AddTeacherByIdStates.waiting_for_input))
async def cmd_add_teacher_process(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    """ID yoki username ni qayta ishlash"""
    current_state = await state.get_state()

    # Xabarni tekshirish
    if not message.text:
        logger.warning(
            "Bo'sh xabar qabul qilindi",
            extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_teacher"},
        )
        await message.answer("❌ Iltimos, matn yuboring.")
        return

    parsed = parse_telegram_input(message.text)

    if parsed is None:
        await message.answer(
            "❌ Noto'g'ri format. Iltimos, Telegram ID yoki foydalanuvchi nomini yuboring:\n"
            "Masalan: 123456789 (ID) yoki @username yoki username (foydalanuvchi nomi)\n\n"
            "❌ Bekor qilish uchun /cancel bosing"
        )
        return

    input_type, value = parsed
    result_message = ""

    if input_type == "id":
        tg_id = value
        changed, user = await set_teacher_role(session=session, telegram_id=tg_id)
        if changed:
            result_message = f"✅ O'qituvchi qo'shildi: {user.telegram_id}"
            logger.info(
                f"O'qituvchi qo'shildi: {user.telegram_id}",
                extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_teacher"},
            )
        else:
            result_message = f"ℹ️ Bu foydalanuvchi allaqachon o'qituvchi: {user.telegram_id}"
            logger.info(
                f"Foydalanuvchi allaqachon o'qituvchi: {user.telegram_id}",
                extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_teacher"},
            )

    else:  # username
        username = value
        try:
            # Username dan @ ni qo'shish
            chat = await message.bot.get_chat(f"@{username}")
            tg_id = chat.id
            user_full_name = chat.full_name if hasattr(chat, 'full_name') else username

            # Foydalanuvchini bazaga qo'shish/ Yangilash
            from school_bot.bot.services.user_service import get_or_create_user
            user = await get_or_create_user(
                session=session,
                telegram_id=tg_id,
                full_name=user_full_name
            )

            # Teacher rolini berish
            changed, user = await set_teacher_role(session=session, telegram_id=tg_id)
            if changed:
                result_message = f"✅ O'qituvchi qo'shildi: @{username} (ID: {user.telegram_id})"
                logger.info(
                    f"O'qituvchi qo'shildi: @{username}",
                    extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_teacher"},
                )
            else:
                result_message = f"ℹ️ Bu foydalanuvchi allaqachon o'qituvchi: @{username}"
                logger.info(
                    f"Foydalanuvchi allaqachon o'qituvchi: @{username}",
                    extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_teacher"},
                )
        except Exception as e:
            logger.error(
                f"Username topilmadi: @{username}. Xatolik: {e}",
                exc_info=True,
                extra={"user_id": message.from_user.id, "chat_id": message.chat.id, "command": "add_teacher"},
            )
            result_message = f"❌ @{username} topilmadi yoki bot bilan gaplashmagan. Sabab: {str(e)}"

    await state.clear()

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


# ============== GROUP MANAGEMENT ==============
@router.message(Command("groups_ids"))
async def cmd_groups_ids(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    await _send_groups_ids_page(message, session, page=1, edit=False)


@router.callback_query(lambda c: c.data.startswith("groups_ids_page:"))
async def groups_ids_page_callback(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        _, page_str = callback.data.split(":")
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    await _send_groups_ids_page(callback.message, session, page=page, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("groups_ids_page_info:"))
async def groups_ids_page_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(Command("groups"))
async def cmd_groups(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    await _send_groups_page(message, session, page=1, edit=False)


@router.callback_query(lambda c: c.data.startswith("groups_page:"))
async def groups_page_callback(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        _, page_str = callback.data.split(":")
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    await _send_groups_page(callback.message, session, page=page, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("groups_page_info:"))
async def groups_page_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(Command(commands=["pending_groups", "kutayotgan_guruhlar"]))
async def cmd_pending_groups(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    pending = await list_pending_groups(session)
    if not pending:
        await message.answer("📭 Hozircha kutilayotgan guruh yo'q.")
        return

    builder = InlineKeyboardBuilder()
    for group in pending:
        builder.button(
            text=f"⏳ {group.name} ({group.chat_id})",
            callback_data=f"pending_group_select:{group.id}",
        )
    builder.adjust(1)

    await message.answer(
        f"⏳ **Kutilayotgan guruhlar** ({len(pending)} ta)\n\n"
        "Maktab biriktirish uchun guruhni tanlang:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(lambda c: c.data.startswith("pending_group_select:"))
async def pending_group_select(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return

    try:
        _, group_id_str = callback.data.split(":")
        group_id = int(group_id_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    group = await get_group_by_id(session, group_id)
    if not group:
        await callback.answer("❌ Guruh topilmadi.", show_alert=True)
        return

    schools = await list_schools(session)
    if not schools:
        await callback.message.edit_text("❌ Maktablar ro'yxati bo'sh. Avval /add_school orqali maktab qo'shing.")
        await callback.answer()
        return
    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = _build_group_join_school_keyboard(group.chat_id, schools, page=1, per_page=10)

    await callback.message.edit_text(
        f"🏫 **{group.name}** uchun maktabni tanlang (1/{total_pages} sahifa):",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.message(Command("set_invite_link"))
async def cmd_set_invite_link(
        message: Message,
        command: CommandObject,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer("❌ Foydalanish: /set_invite_link [guruh_nomi] [invite_link]")
        return

    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Foydalanish: /set_invite_link [guruh_nomi] [invite_link]")
        return

    group_name, invite_link = parts[0].strip(), parts[1].strip()
    group = await get_group_by_name(session, group_name)
    if not group:
        await message.answer("❌ Guruh topilmadi.")
        return

    await set_invite_link(session, group, invite_link)
    await message.answer(f"✅ Invite link saqlandi: {group.name}")


@router.message(Command("create_invite_link"))
async def cmd_create_invite_link(
        message: Message,
        command: CommandObject,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer("❌ Foydalanish: /create_invite_link [guruh_nomi]")
        return

    group = await get_group_by_name(session, args)
    if not group:
        await message.answer("❌ Guruh topilmadi.")
        return

    try:
        invite = await message.bot.create_chat_invite_link(chat_id=group.chat_id)
    except Exception as e:
        logger.error("Invite link yaratishda xatolik", exc_info=True)
        await message.answer("Qaytadan urinib ko'ring.")
        return

    await set_invite_link(session, group, invite.invite_link)
    await message.answer(f"✅ Invite link yaratildi: {group.name} - {invite.invite_link}")


@router.message(Command("add_group"))
async def cmd_add_group_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    await state.set_state(GroupManagementStates.add_school)
    schools = await list_schools(session)
    if not schools:
        await message.answer("📭 Hozircha hech qanday maktab yo'q.")
        return
    keyboard = _build_school_paged_keyboard("addgroup_school", schools, page=1, per_page=10).as_markup()
    total_pages = max(1, (len(schools) + 9) // 10)
    await message.answer(
        f"🏫 Guruh qo'shish uchun maktabni tanlang (1/{total_pages} sahifa):",
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data.startswith("addgroup_school:"))
async def cmd_add_group_select_school(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Faqat superadminlar uchun.", show_alert=True)
        return

    try:
        school_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri maktab.", show_alert=True)
        return

    school = await get_school_by_id(session, school_id)
    if not school:
        await callback.answer("❌ Maktab topilmadi.", show_alert=True)
        return

    await state.update_data(school_id=str(school.id))
    await state.set_state(GroupManagementStates.add_name)
    await callback.message.answer(
        f"🆕 Guruh nomini kiriting (masalan: 7-A).\nTanlangan maktab: {school.name}\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("addgroup_page:"))
async def cmd_add_group_page(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Faqat superadminlar uchun.", show_alert=True)
        return

    try:
        page = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri sahifa.", show_alert=True)
        return

    schools = await list_schools(session)
    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = _build_school_paged_keyboard("addgroup_school", schools, page=page, per_page=10).as_markup()
    await callback.message.edit_text(
        f"🏫 Guruh qo'shish uchun maktabni tanlang ({page}/{total_pages} sahifa):",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("addgroup_page_info:"))
async def cmd_add_group_page_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(GroupManagementStates.add_name, F.text)
async def cmd_add_group_name(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    data = await state.get_data()
    raw_school_id = data.get("school_id")
    if not raw_school_id:
        await message.answer("❌ Avval maktabni tanlang.")
        await state.clear()
        return

    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Guruh nomi bo'sh bo'lishi mumkin emas. Qayta kiriting:")
        return

    existing = await get_group_by_name(session, name)
    if existing:
        await message.answer("⚠️ Bu nomdagi guruh allaqachon mavjud. Boshqa nom kiriting:")
        return

    await state.update_data(group_name=name)
    await state.set_state(GroupManagementStates.add_chat_id)
    await message.answer("🔗 Guruh chat ID sini kiriting (masalan: -1001234567890):")


@router.message(GroupManagementStates.add_chat_id, F.text)
async def cmd_add_group_chat_id(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    try:
        chat_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri chat ID. Qayta kiriting:")
        return

    existing = await get_group_by_chat_id(session, chat_id)
    if existing:
        await message.answer("⚠️ Bu chat ID allaqachon boshqa guruhga biriktirilgan.")
        await state.clear()
        return

    data = await state.get_data()
    raw_school_id = data.get("school_id")
    group = await add_group(
        session,
        name=data["group_name"],
        chat_id=chat_id,
        school_id=int(raw_school_id) if raw_school_id else None,
    )
    await state.clear()

    await message.answer(f"✅ Guruh qo'shildi: {group.name} ({group.chat_id})")


@router.message(Command("edit_group"))
async def cmd_edit_group_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    groups = await list_groups(session)
    if not groups:
        await message.answer("📭 Guruh topilmadi. /add_group bilan qo'shing.")
        return

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"group_edit:{group.id}")
    builder.adjust(1)

    await message.answer("✏️ Qaysi guruhni tahrirlaysiz?", reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("group_edit:"))
async def cmd_edit_group_select(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Faqat superadminlar uchun.", show_alert=True)
        return

    try:
        group_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri guruh.", show_alert=True)
        return

    group = await get_group_by_id(session, group_id)
    if not group:
        await callback.answer("❌ Guruh topilmadi.", show_alert=True)
        return

    await state.set_state(GroupManagementStates.edit_school)
    await state.update_data(group_id=str(group.id))
    schools = await list_schools(session)
    if not schools:
        await callback.message.answer("📭 Hozircha hech qanday maktab yo'q.")
        await callback.answer()
        return
    keyboard = _build_school_keyboard("group_edit_school", schools).as_markup()
    current_school = group.school.name if group.school else "Noma'lum"
    await callback.message.answer(
        f"🏫 Guruh uchun maktabni tanlang (hozirgi: {current_school}):",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("group_edit_school:"))
async def cmd_edit_group_select_school(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Faqat superadminlar uchun.", show_alert=True)
        return

    try:
        school_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri maktab.", show_alert=True)
        return

    school = await get_school_by_id(session, school_id)
    if not school:
        await callback.answer("❌ Maktab topilmadi.", show_alert=True)
        return

    await state.update_data(school_id=str(school.id))
    await state.set_state(GroupManagementStates.edit_name)
    await callback.message.answer(
        "✏️ Yangi guruh nomini kiriting (masalan: 7-A) yoki ⏭️ O'tkazib yuborish tugmasini bosing.\n"
        f"Tanlangan maktab: {school.name}",
        reply_markup=get_skip_cancel_keyboard(),
    )
    await callback.answer()


@router.message(GroupManagementStates.edit_name, Command("skip"))
@router.message(GroupManagementStates.edit_name, F.text == "⏭️ O'tkazib yuborish")
async def cmd_edit_group_skip_name(message: Message, state: FSMContext) -> None:
    await state.update_data(new_name=None)
    await state.set_state(GroupManagementStates.edit_chat_id)
    await message.answer(
        "🔗 Yangi chat ID kiriting yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(GroupManagementStates.edit_name, F.text)
async def cmd_edit_group_name(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("❌ Guruh nomi bo'sh bo'lishi mumkin emas.")
        return

    existing = await get_group_by_name(session, name)
    if existing:
        await message.answer("⚠️ Bu nomdagi guruh allaqachon mavjud.")
        return

    await state.update_data(new_name=name)
    await state.set_state(GroupManagementStates.edit_chat_id)
    await message.answer(
        "🔗 Yangi chat ID kiriting yoki ⏭️ O'tkazib yuborish tugmasini bosing:",
        reply_markup=get_skip_cancel_keyboard(),
    )


@router.message(GroupManagementStates.edit_chat_id, Command("skip"))
@router.message(GroupManagementStates.edit_chat_id, F.text == "⏭️ O'tkazib yuborish")
async def cmd_edit_group_skip_chat_id(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    data = await state.get_data()
    group = await get_group_by_id(session, int(data["group_id"]))
    if not group:
        await message.answer("❌ Guruh topilmadi.")
        await state.clear()
        return

    raw_school_id = data.get("school_id")
    if data.get("new_name") is None and raw_school_id is None:
        await message.answer("ℹ️ O'zgarish yo'q.")
        await state.clear()
        await show_groups_menu(message, is_superadmin=True)
        return

    await update_group(session, group, name=data.get("new_name"), school_id=int(raw_school_id) if raw_school_id else None)
    await state.clear()
    await message.answer(f"✅ Guruh yangilandi: {group.name}")
    await show_groups_menu(message, is_superadmin=True)


@router.message(GroupManagementStates.edit_chat_id, F.text)
async def cmd_edit_group_chat_id(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    data = await state.get_data()
    group = await get_group_by_id(session, int(data["group_id"]))
    if not group:
        await message.answer("❌ Guruh topilmadi.")
        await state.clear()
        return

    try:
        chat_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri chat ID. Qayta kiriting:")
        return

    existing = await get_group_by_chat_id(session, chat_id)
    if existing and existing.id != group.id:
        await message.answer("⚠️ Bu chat ID boshqa guruhga biriktirilgan.")
        return

    raw_school_id = data.get("school_id")
    await update_group(
        session,
        group,
        name=data.get("new_name"),
        chat_id=chat_id,
        school_id=int(raw_school_id) if raw_school_id else None,
    )
    await state.clear()
    await message.answer(f"✅ Guruh yangilandi: {group.name} ({group.chat_id})")
    await show_groups_menu(message, is_superadmin=True)


@router.message(Command("remove_group"))
async def cmd_remove_group_start(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    groups = await list_groups(session)
    if not groups:
        await message.answer("📭 Guruh topilmadi.")
        return

    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=f"🗑️ {group.name}", callback_data=f"group_remove:{group.id}")
    builder.adjust(1)

    await message.answer("🗑️ Qaysi guruhni o'chirasiz?", reply_markup=builder.as_markup())


@router.callback_query(lambda c: c.data.startswith("group_remove:"))
async def cmd_remove_group_confirm(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Faqat superadminlar uchun.", show_alert=True)
        return

    try:
        group_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri guruh.", show_alert=True)
        return

    group = await get_group_by_id(session, group_id)
    if not group:
        await callback.answer("❌ Guruh topilmadi.", show_alert=True)
        return

    await remove_group(session, group)
    await callback.message.edit_text(f"✅ Guruh o'chirildi: {group.name}")
    await callback.answer()


# ============== REMOVE TEACHER ==============
@router.message(Command("remove_teacher"))
async def cmd_remove_teacher_start(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        db_user,
) -> None:
    """O'qituvchini o'chirish - tanlash usuli"""

    # db_user orqali superadminligini tekshirish
    is_superadmin = (db_user.role == UserRole.superadmin)

    if not is_superadmin:
        await message.answer("⛔ Siz o'qituvchilarni boshqarish huquqiga ega emassiz.")
        return

    # Teacherlar ro'yxatini olish
    result = await session.execute(
        select(User).where(
            User.role == UserRole.teacher,
        ).order_by(User.full_name)
    )
    teachers = result.scalars().all()

    if not teachers:
        await message.answer("📭 Hozircha hech qanday o'qituvchi yo'q.")
        return

    # Teacherlar ro'yxatini inline keyboard ko'rinishida ko'rsatish
    builder = InlineKeyboardBuilder()

    for teacher in teachers:
        if teacher.full_name:
            teacher_name = teacher.full_name
        else:
            teacher_name = f"ID: {teacher.telegram_id}"

        button_text = f"👨‍🏫 {teacher_name}"
        builder.button(text=button_text, callback_data=f"del_teacher_{teacher.id}")

    builder.adjust(1)

    await state.set_state(RemoveTeacherStates.waiting_for_selection)
    await message.answer(
        "👨‍🏫 O'chirmoqchi bo'lgan o'qituvchingizni tanlang:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(lambda c: c.data.startswith("del_teacher_"))
async def process_remove_teacher_selection(
        callback: CallbackQuery,
        state: FSMContext,
        session: AsyncSession,
) -> None:
    """Tanlangan o'qituvchini o'chirish"""
    teacher_id = int(callback.data.replace("del_teacher_", ""))
    logger.info(
        f"O'qituvchini o'chirish so'rovi: teacher_id={teacher_id}",
        extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "remove_teacher"},
    )

    # Teacherni bazadan olish
    result = await session.execute(
        select(User).where(User.id == teacher_id)
    )
    teacher = result.scalar_one_or_none()

    if not teacher:
        logger.warning(
            f"O'qituvchi topilmadi: teacher_id={teacher_id}",
            extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "remove_teacher"},
        )
        await callback.message.edit_text("❌ O'qituvchi topilmadi.")
        await callback.answer()
        return

    # Teacher ismini saqlab qolish
    if teacher.full_name:
        teacher_name = teacher.full_name
    else:
        teacher_name = f"ID: {teacher.telegram_id}"

    logger.info(
        f"O'qituvchi olib tashlanmoqda: {teacher_name}",
        extra={"user_id": callback.from_user.id, "chat_id": callback.message.chat.id, "command": "remove_teacher"},
    )

    # Teacherni hard-delete qilish (DB dan butunlay o'chirish)
    # Delete in FK order: Tasks → Profile → User
    await session.execute(delete(Task).where(Task.teacher_id == teacher.id))
    await session.execute(delete(Profile).where(Profile.bot_user_id == teacher.id))
    await session.execute(delete(User).where(User.id == teacher.id))
    await session.commit()

    await callback.message.edit_text(
        f"✅ O'qituvchi o'chirildi: {teacher_name}"
    )

    await state.clear()
    await callback.answer()


# ============== LIST TEACHERS ==============
@router.message(Command("list_teachers"))
async def cmd_list_teachers(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    await _send_teachers_page(message, session, page=1, edit=False)


@router.callback_query(lambda c: c.data.startswith("teachers_page:"))
async def teachers_page_callback(
        callback: CallbackQuery,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        _, page_str = callback.data.split(":")
        page = int(page_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    await _send_teachers_page(callback.message, session, page=page, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("teachers_page_info:"))
async def teachers_page_info(callback: CallbackQuery) -> None:
    await callback.answer()


# ============== MANUAL ADD TEACHER ==============
async def _resolve_user_from_input(
        session: AsyncSession,
        message: Message,
        raw_text: str,
) -> tuple[User | None, str | None]:
    parsed = parse_telegram_input(raw_text)
    if parsed is None:
        return None, "❌ Noto'g'ri format. Telegram ID yoki @username yuboring."

    input_type, value = parsed
    if input_type == "id":
        user = await get_or_create_user(session, telegram_id=value, full_name=None)
        return user, None

    username = value
    user = await get_user_by_username(session, username)
    if user:
        return user, None

    try:
        chat = await message.bot.get_chat(f"@{username}")
    except Exception:
        return None, f"❌ @{username} topilmadi yoki bot bilan gaplashmagan."

    full_name = getattr(chat, "full_name", None) or username
    user = await get_or_create_user(session, telegram_id=chat.id, full_name=full_name, username=username)
    return user, None


@router.message(Command("add_teacher_manual"))
async def cmd_add_teacher_manual_start(
        message: Message,
        state: FSMContext,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    await state.set_state(AddTeacherManualStates.waiting_for_user)
    await message.answer(
        "👤 O'qituvchi qilmoqchi bo'lgan foydalanuvchining Telegram ID sini yoki Username ni yuboring:\n"
        "Masalan: 123456789 yoki @username\n\n"
        "❌ Bekor qilish uchun /cancel bosing"
    )


@router.message(AddTeacherManualStates.waiting_for_user, F.text)
async def add_teacher_manual_waiting_user(
        message: Message,
        state: FSMContext,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    raw_text = (message.text or "").strip()
    user, error = await _resolve_user_from_input(session, message, raw_text)
    if error:
        await message.answer(error)
        return
    if not user:
        await message.answer("⚠️ Foydalanuvchi topilmadi.")
        return

    profile = await get_profile_by_user_id(session, user.id)
    if profile and profile.is_approved and user.role == UserRole.teacher:
        await message.answer("ℹ️ Bu foydalanuvchi allaqachon o'qituvchi.")
        await state.clear()
        return

    if profile is None:
        full_name = user.full_name or ""
        parts = full_name.split()
        first_name = parts[0] if parts else "Noma'lum"
        last_name = " ".join(parts[1:]) if len(parts) > 1 else None
        profile = Profile(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            phone="Noma'lum",
            assigned_groups=[],
            is_approved=False,
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    await clear_selections_for_profile(profile.id)

    schools = await list_schools(session)
    if not schools:
        await message.answer("📭 Maktablar topilmadi.")
        await state.clear()
        return

    total_pages = max(1, (len(schools) + 9) // 10)
    keyboard = build_school_keyboard(profile.id, schools, page=1, per_page=10)
    username = f"@{user.username}" if user.username else ""
    name = user.full_name or "Noma'lum"

    await state.set_state(AddTeacherManualStates.waiting_for_school)
    await message.answer(
        f"👤 Foydalanuvchi topildi: {name} {username}\n"
        f"🆔 ID: {user.telegram_id}\n\n"
        "🏫 Qaysi maktabga tegishli?\n"
        f"🏫 Maktabni tanlang (1/{total_pages} sahifa):",
        reply_markup=keyboard,
    )


# ============== USERS ==============
@router.message(Command("users"))
async def cmd_users(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    """Users management menu (faqat superadmin)"""
    if not is_superadmin:
        await message.answer("⛔ You don't have permission to access this section.")
        return
    await message.answer("Menyudan tanlang...", reply_markup=get_users_management_keyboard())


# ============== STATS ==============
@router.message(Command("stats"))
async def cmd_stats(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    """Bot statistikasi (faqat superadmin uchun)"""
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    # Umumiy statistika
    users_count = await session.scalar(select(func.count()).select_from(User))
    teachers_count = await session.scalar(select(func.count()).where(User.role == UserRole.teacher))
    superadmins_count = await session.scalar(select(func.count()).where(User.role == UserRole.superadmin))
    librarians_count = await session.scalar(select(func.count()).where(User.role == UserRole.librarian))
    regular_users = await session.scalar(
        select(func.count()).where(
            (User.role == UserRole.student) | (User.role.is_(None)),
        )
    )
    tasks_count = await session.scalar(select(func.count()).select_from(Task))

    # Eng faol teacherlar
    active_teachers = await session.execute(
        select(
            User.telegram_id,
            User.full_name,
            Profile.first_name,
            Profile.last_name,
            Profile.assigned_groups,
            School.name.label("school_name"),
            func.count(Task.id).label("task_count"),
        )
        .join(Profile, User.id == Profile.bot_user_id)
        .join(Task, User.id == Task.teacher_id)
        .outerjoin(School, Profile.school_id == School.id)
        .where(User.role == UserRole.teacher)
        .group_by(User.id, Profile.id, School.id)
        .order_by(func.count(Task.id).desc())
        .limit(10)
    )
    active_teachers = active_teachers.all()

    # Statistikani shakllantirish
    lines = [
        "📊 **Bot statistikasi**",
        "=" * 30,
        "",
        "👥 **Foydalanuvchilar:**",
        f"   • Jami: {users_count} ta",
        f"   • Superadmin: {superadmins_count} ta",
        f"   • Kutubxonachi: {librarians_count} ta",
        f"   • O'qituvchi: {teachers_count} ta",
        f"   • Oddiy user: {regular_users} ta",
        "",
        f"📝 **Topshiriqlar:** {tasks_count} ta",
        ""
    ]

    if active_teachers:
        lines.append("⭐ **Eng faol o'qituvchilar:**")
        for i, teacher in enumerate(active_teachers, 1):
            if teacher.first_name and teacher.last_name:
                teacher_name = f"{teacher.first_name} {teacher.last_name}"
            else:
                teacher_name = teacher.full_name or f"Foydalanuvchi {teacher.telegram_id}"

            school_name = teacher.school_name or "Maktab biriktirilmagan"
            groups = teacher.assigned_groups or []
            groups_text = ", ".join(groups) if groups else "Guruh biriktirilmagan"

            lines.append(f"   {i}. {teacher_name}")
            lines.append(f"      🏫 Maktab: {school_name}")
            lines.append(f"      📚 Guruhlar: {groups_text}")
            lines.append(f"      📊 Topshiriqlar: {teacher.task_count} ta")
            lines.append("")
    else:
        lines.append("⭐ **Eng faol o'qituvchilar:**")
        lines.append("   Hozircha hech qanday topshiriq yo'q")

    lines.extend(["", f"📅 Oxirgi yangilanish: {datetime.now().strftime('%d.%m.%Y %H:%M')}"])

    result_message = "\n".join(lines)

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


# ============== REMOVE SUPERADMIN ==============
@router.message(Command("remove_superadmin"))
async def cmd_remove_superadmin(
        message: Message,
        command: CommandObject,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    parsed = parse_telegram_input(command.args)
    if parsed is None or parsed[0] != "id":
        await message.answer("Ishlatilishi: /remove_superadmin [telegram_id]")
        return

    tg_id = parsed[1]

    result = await session.execute(
        select(User).where(User.telegram_id == tg_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        result_message = "⚠️ Foydalanuvchi topilmadi."
    elif user.role != UserRole.superadmin:
        result_message = f"ℹ️ Bu foydalanuvchi superadmin emas: {tg_id}"
    else:
        user.role = None
        await session.commit()
        result_message = f"✅ Super foydalanuvchi olib tashlandi: {tg_id}"

    # Asosiy menyuni qaytarish
    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await message.answer(result_message, reply_markup=keyboard)


# ============== LIBRARIAN MANAGEMENT ==============
@router.message(Command("add_librarian"))
async def cmd_add_librarian(
        message: Message,
        command: CommandObject,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    if not command.args:
        await message.answer("Ishlatilishi: /add_librarian [telegram_id] yoki @username")
        return

    parsed = parse_telegram_input(command.args)
    if parsed is None:
        await message.answer("❌ Noto'g'ri format. Telegram ID yoki username yuboring.")
        return

    input_type, value = parsed
    if input_type == "id":
        tg_id = value
        user = await get_or_create_user(session, telegram_id=tg_id, full_name=None)
        if user.role == UserRole.librarian:
            await message.answer(f"ℹ️ Bu foydalanuvchi allaqachon kutubxonachi: {tg_id}")
            return
        user.role = UserRole.librarian
        await session.commit()
        await message.answer(f"✅ Kutubxonachi qo'shildi: {tg_id}")
        return

    username = value
    try:
        chat = await message.bot.get_chat(f"@{username}")
        tg_id = chat.id
        full_name = getattr(chat, "full_name", None) or username
        user = await get_or_create_user(session, telegram_id=tg_id, full_name=full_name, username=username)
        if user.role == UserRole.librarian:
            await message.answer(f"ℹ️ Bu foydalanuvchi allaqachon kutubxonachi: @{username}")
            return
        user.role = UserRole.librarian
        await session.commit()
        await message.answer(f"✅ Kutubxonachi qo'shildi: @{username} (ID: {tg_id})")
    except Exception:
        await message.answer(f"❌ @{username} topilmadi yoki bot bilan gaplashmagan.")


@router.message(Command("remove_librarian"))
async def cmd_remove_librarian(
        message: Message,
        command: CommandObject,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    if not command.args:
        await message.answer("Ishlatilishi: /remove_librarian [telegram_id] yoki @username")
        return

    parsed = parse_telegram_input(command.args)
    if parsed is None:
        await message.answer("❌ Noto'g'ri format. Telegram ID yoki username yuboring.")
        return

    input_type, value = parsed
    user = None
    if input_type == "id":
        tg_id = value
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("⚠️ Foydalanuvchi topilmadi.")
            return
    else:
        username = value
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            await message.answer("⚠️ Foydalanuvchi topilmadi.")
            return

    if user.role != UserRole.librarian:
        await message.answer("ℹ️ Bu foydalanuvchi kutubxonachi emas.")
        return

    user.role = None
    await session.commit()
    await message.answer("✅ Kutubxonachi olib tashlandi.")


@router.message(Command("list_librarians"))
async def cmd_list_librarians(
        message: Message,
        session: AsyncSession,
        is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await message.answer("⛔ Bu komanda faqat superadminlar uchun.")
        return

    result = await session.execute(select(User).where(User.role == UserRole.librarian).order_by(User.created_at))
    librarians = list(result.scalars().all())

    if not librarians:
        await message.answer("📭 Hozircha kutubxonachi yo'q.")
        return

    lines = ["📚 **Kutubxonachilar ro'yxati:**", ""]
    for idx, librarian in enumerate(librarians, 1):
        username = f"@{librarian.username}" if librarian.username else "(foydalanuvchi nomi yo'q)"
        name = librarian.full_name or f"ID: {librarian.telegram_id}"
        lines.append(f"{idx}. {name} {username} - 🆔 {librarian.telegram_id}")

    keyboard = get_main_keyboard(is_superadmin=True, is_teacher=False)
    await message.answer("\n".join(lines), reply_markup=keyboard)


# ============== TEACHER DETAIL VIEW ==============

import re as _re
_PHONE_RE = _re.compile(r"^\+998\d{9}$")
_VALID_ROLES = {
    UserRole.teacher: "o'qituvchi",
    UserRole.librarian: "kutubxonachi",
    UserRole.superadmin: "superadmin",
}


def _format_teacher_detail(user: User, profile: Profile | None, school_name: str) -> str:
    full_name = user.full_name or "Yo'q"
    if profile:
        first = profile.first_name or ""
        last = profile.last_name or ""
        profile_name = f"{first} {last}".strip() or "Yo'q"
        phone = profile.phone or "Yo'q"
        groups = ", ".join(profile.assigned_groups or []) or "Yo'q"
    else:
        profile_name = "Yo'q"
        phone = "Yo'q"
        groups = "Yo'q"
    username = f"@{user.username}" if user.username else "Yo'q"
    role_display = _VALID_ROLES.get(user.role, str(user.role) if user.role else "Yo'q")
    return (
        f"👨‍🏫 O'qituvchi ma'lumotlari\n\n"
        f"👤 To'liq ism (User): {full_name}\n"
        f"📛 Ism (Profil): {profile_name}\n"
        f"🔹 Username: {username}\n"
        f"🆔 Telegram ID: {user.telegram_id}\n"
        f"📱 Telefon: {phone}\n"
        f"🏫 Maktab: {school_name}\n"
        f"📚 Guruhlar: {groups}\n"
        f"🎭 Rol: {role_display}"
    )


def _build_group_toggle_keyboard(
    user_id: int,
    all_groups: list,
    selected_names: list[str],
) -> InlineKeyboardBuilder:
    """Build a toggle keyboard showing each group with a checkmark if currently selected."""
    selected_set = set(selected_names)
    builder = InlineKeyboardBuilder()
    for group in all_groups:
        mark = "✅" if group.name in selected_set else "☐"
        builder.button(
            text=f"{mark} {group.name}",
            callback_data=f"tg:{user_id}:{group.id}",
        )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="💾 Saqlash", callback_data=f"tg_save:{user_id}"))  # max: 8+1+19 = 28 bytes
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"te_x:{user_id}"))  # max: 5+1+19 = 25 bytes
    return builder


def _build_teacher_detail_keyboard(user_id: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"te_menu:{user_id}"))  # max: 8+1+19 = 28 bytes
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="td_x"))  # max: 4 bytes
    return builder


def _build_teacher_edit_field_keyboard(user_id: int) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ To'liq ism", callback_data=f"te_f:full_name:{user_id}")  # max: 15+1+19 = 35 bytes
    builder.button(text="📞 Telefon raqam", callback_data=f"te_f:phone:{user_id}")  # max: 11+1+19 = 31 bytes
    builder.button(text="🎭 Rol", callback_data=f"te_f:role:{user_id}")  # max: 10+1+19 = 30 bytes
    builder.button(text="👥 Guruhlar", callback_data=f"te_f:groups:{user_id}")  # max: 12+1+19 = 32 bytes
    builder.button(text="❌ Bekor qilish", callback_data=f"te_x:{user_id}")  # max: 5+1+19 = 25 bytes
    builder.adjust(1)
    return builder


async def _show_teacher_detail(
    target: Message,
    session: AsyncSession,
    user_id: int,
    edit: bool = False,
) -> None:
    user = await session.get(User, user_id)
    if not user or user.role not in (UserRole.teacher, UserRole.librarian, UserRole.superadmin):
        text = "❌ O'qituvchi topilmadi."
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return

    profile = await get_profile_by_user_id(session, user_id)
    school_name = "Biriktirilmagan"
    if profile and profile.school_id:
        school = await get_school_by_id(session, profile.school_id)
        if school:
            school_name = school.name

    text = _format_teacher_detail(user, profile, school_name)
    keyboard = _build_teacher_detail_keyboard(user_id).as_markup()
    if edit:
        await target.edit_text(text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


@router.callback_query(lambda c: c.data.startswith("td:"))
async def teacher_detail_view(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return
    await _show_teacher_detail(callback.message, session, user_id, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data == "td_x")
async def teacher_detail_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    await cancel_current_action(callback, state, is_superadmin=True)


# ============== TEACHER EDIT — ADMIN SIDE ==============

@router.callback_query(lambda c: c.data.startswith("te_menu:"))
async def teacher_edit_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    user = await session.get(User, user_id)
    if not user or user.role not in (UserRole.teacher, UserRole.librarian, UserRole.superadmin):
        await callback.answer("❌ O'qituvchi topilmadi.", show_alert=True)
        return

    await state.set_state(TeacherEditStates.choose_field)
    await state.update_data(edit_user_id=str(user_id))

    keyboard = _build_teacher_edit_field_keyboard(user_id).as_markup()
    display = user.full_name or f"ID: {user.telegram_id}"
    await callback.message.edit_text(
        f"✏️ {display} uchun tahrirlash maydonini tanlang:",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(TeacherEditStates.choose_field, lambda c: c.data.startswith("te_f:"))
async def teacher_edit_field_select(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        _, field, user_id_str = callback.data.split(":")
        user_id = int(user_id_str)
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    await state.update_data(edit_user_id=str(user_id), edit_field=field)

    if field == "full_name":
        await state.set_state(TeacherEditStates.waiting_full_name)
        await callback.message.edit_text(
            "✏️ Yangi to'liq ismni kiriting (3–100 belgi):\n\n/cancel — bekor qilish"
        )
    elif field == "phone":
        await state.set_state(TeacherEditStates.waiting_phone)
        await callback.message.edit_text(
            "📞 Yangi telefon raqamni kiriting (+998XXXXXXXXX):\n\n/cancel — bekor qilish"
        )
    elif field == "role":
        await state.set_state(TeacherEditStates.waiting_role)
        builder = InlineKeyboardBuilder()
        builder.button(text="👨‍🏫 O'qituvchi", callback_data=f"tsr:t:{user_id}")  # max: 25+1+19 = 45 bytes
        builder.button(text="📚 Kutubxonachi", callback_data=f"tsr:l:{user_id}")  # max: 28+1+19 = 48 bytes
        builder.button(text="👑 Superadmin", callback_data=f"tsr:s:{user_id}")  # max: 29+1+19 = 49 bytes
        builder.button(text="❌ Bekor qilish", callback_data=f"te_x:{user_id}")  # max: 5+1+19 = 25 bytes
        builder.adjust(1)
        await callback.message.edit_text(
            "🎭 Yangi rolni tanlang:",
            reply_markup=builder.as_markup(),
        )
    elif field == "groups":
        await state.set_state(TeacherEditStates.waiting_groups)
        profile = await get_profile_by_user_id(session, user_id)
        current_groups = list(profile.assigned_groups or []) if profile else []
        await state.update_data(pending_groups=current_groups)
        groups = await list_groups(session)
        if not groups:
            await callback.message.edit_text(
                "📭 Hech qanday guruh topilmadi.\n\n/cancel — bekor qilish"
            )
            await callback.answer()
            return
        keyboard = _build_group_toggle_keyboard(user_id, groups, current_groups).as_markup()
        await callback.message.edit_text(
            "👥 Guruhlarga belgi qo'ying yoki olib tashlang, so'ng 💾 Saqlash tugmasini bosing:",
            reply_markup=keyboard,
        )
    else:
        await callback.answer("❌ Noma'lum maydon.", show_alert=True)
        return

    await callback.answer()


@router.callback_query(TeacherEditStates.choose_field, lambda c: c.data.startswith("te_x:"))
@router.callback_query(TeacherEditStates.waiting_full_name, lambda c: c.data.startswith("te_x:"))
@router.callback_query(TeacherEditStates.waiting_phone, lambda c: c.data.startswith("te_x:"))
@router.callback_query(TeacherEditStates.waiting_role, lambda c: c.data.startswith("te_x:"))
@router.callback_query(TeacherEditStates.waiting_groups, lambda c: c.data.startswith("te_x:"))
async def teacher_edit_cancel_inline(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await cancel_current_action(callback, state, is_superadmin=True)
        return
    await state.clear()
    await _show_teacher_detail(callback.message, session, user_id, edit=True)
    await callback.answer()


@router.message(TeacherEditStates.waiting_full_name, F.text)
async def teacher_edit_full_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.text and message.text.strip().startswith("/cancel"):
        await cancel_current_action(message, state, is_superadmin=True)
        return

    value = (message.text or "").strip()
    if len(value) < 3 or len(value) > 100:
        await message.answer("❌ To'liq ism 3 dan 100 gacha belgi bo'lishi kerak. Qayta kiriting:\n\n/cancel — bekor qilish")
        return

    data = await state.get_data()
    user_id = int(data["edit_user_id"])

    user = await update_teacher_user(session, user_id, full_name=value)
    if not user:
        await message.answer("❌ Foydalanuvchi topilmadi.")
        await state.clear()
        return

    await state.clear()
    await message.answer("✅ Saqlandi.")
    await _show_teacher_detail(message, session, user_id, edit=False)


@router.message(TeacherEditStates.waiting_phone, F.text)
async def teacher_edit_phone(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if message.text and message.text.strip().startswith("/cancel"):
        await cancel_current_action(message, state, is_superadmin=True)
        return

    value = (message.text or "").strip()
    if not _PHONE_RE.match(value):
        await message.answer(
            "❌ Noto'g'ri format. +998XXXXXXXXX ko'rinishida kiriting:\n\n/cancel — bekor qilish"
        )
        return

    data = await state.get_data()
    user_id = int(data["edit_user_id"])

    profile = await update_teacher_profile(session, user_id, phone=value)
    if not profile:
        await message.answer("❌ Profil topilmadi.")
        await state.clear()
        return

    await state.clear()
    await message.answer("✅ Saqlandi.")
    await _show_teacher_detail(message, session, user_id, edit=False)


@router.callback_query(TeacherEditStates.waiting_role, lambda c: c.data.startswith("tsr:"))
async def teacher_set_role(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        _, role_str, user_id_str = callback.data.split(":")
        user_id = int(user_id_str)
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    role_map = {
        "t": UserRole.teacher,
        "l": UserRole.librarian,
        "s": UserRole.superadmin,
    }
    new_role = role_map.get(role_str)
    if not new_role:
        await callback.answer("❌ Noto'g'ri rol.", show_alert=True)
        return

    user = await update_teacher_user(session, user_id, role=new_role)
    if not user:
        await callback.answer("❌ Foydalanuvchi topilmadi.", show_alert=True)
        await state.clear()
        return

    await state.clear()
    await _show_teacher_detail(callback.message, session, user_id, edit=True)
    await callback.answer("✅ Saqlandi.")


# ============== TEACHER EDIT — GROUP TOGGLE ==============

@router.callback_query(TeacherEditStates.waiting_groups, lambda c: c.data.startswith("tg:"))
async def teacher_toggle_group(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        parts = callback.data.split(":", 2)
        user_id = int(parts[1])
        group_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    all_groups = await list_groups(session)
    group = next((g for g in all_groups if g.id == group_id), None)
    if not group:
        await callback.answer("❌ Guruh topilmadi.", show_alert=True)
        return
    group_name = group.name

    data = await state.get_data()
    pending: list[str] = list(data.get("pending_groups") or [])

    if group_name in pending:
        pending.remove(group_name)
    else:
        pending.append(group_name)

    await state.update_data(pending_groups=pending)

    groups = await list_groups(session)
    keyboard = _build_group_toggle_keyboard(user_id, groups, pending).as_markup()
    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@router.callback_query(TeacherEditStates.waiting_groups, lambda c: c.data.startswith("tg_save:"))
async def teacher_save_groups(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q.", show_alert=True)
        return
    try:
        user_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    data = await state.get_data()
    pending: list[str] = list(data.get("pending_groups") or [])

    profile = await update_teacher_groups(session, user_id, pending)
    if not profile:
        await callback.answer("❌ Profil topilmadi.", show_alert=True)
        await state.clear()
        return

    await state.clear()
    groups_text = ", ".join(pending) if pending else "Yo'q"
    await callback.answer(f"✅ Saqlandi: {groups_text}", show_alert=False)
    await _show_teacher_detail(callback.message, session, user_id, edit=True)


# ============== STUBS for poll flows (referenced but not yet implemented) ==============

async def show_all_teachers_overview(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    edit: bool = False,
) -> None:
    """Placeholder: shows paginated teacher list. Used by poll navigation flows."""
    await _send_teachers_page(message, session, page=1, edit=edit)


async def show_teachers_by_school(
    message: Message,
    session: AsyncSession,
    school_id: int,
    state: FSMContext,
    edit: bool = False,
) -> None:
    """Placeholder: shows teachers filtered by school. Used by poll navigation flows."""
    total = await session.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.teacher)
    ) or 0
    result = await session.execute(
        select(User, Profile, School)
        .join(Profile, User.id == Profile.bot_user_id)
        .outerjoin(School, Profile.school_id == School.id)
        .where(User.role == UserRole.teacher, Profile.school_id == school_id)
        .order_by(User.full_name)
        .limit(PAGE_SIZE_TEACHERS)
    )
    rows = result.all()
    if not rows:
        text = "📭 Bu maktabda o'qituvchi topilmadi."
        if edit:
            await message.edit_text(text)
        else:
            await message.answer(text)
        return

    school_obj = await get_school_by_id(session, school_id)
    school_name = school_obj.name if school_obj else f"Maktab {school_id}"
    lines = [f"👨‍🏫 {school_name} o'qituvchilari", ""]
    builder = InlineKeyboardBuilder()
    for t, p, s in rows:
        display = f"{p.first_name} {p.last_name or ''}".strip() if p else (t.full_name or f"ID:{t.telegram_id}")
        lines.append(f"• {display}")
        builder.button(text=display, callback_data=f"admin_poll_teacher:{t.id}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Ortga", callback_data="admin_poll_back_to_schools"))

    text = "\n".join(lines).strip()
    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())
