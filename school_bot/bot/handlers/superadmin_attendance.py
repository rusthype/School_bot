from __future__ import annotations

import uuid

from aiogram import F, Router
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.handlers.common import cancel_current_action
from school_bot.bot.services.attendance_service import (
    AttendanceServiceError,
    TASHKENT_TZ,
    list_attendance,
    set_school_location,
    tashkent_today,
)
from school_bot.bot.services.pagination import SchoolPagination
from school_bot.bot.services.school_service import get_school_by_id, list_schools
from school_bot.bot.states.attendance import SuperadminAttendanceStates

router = Router(name="superadmin_attendance")

SCHOOL_PAGE_SIZE = 15
REPORT_PAGE_SIZE = 15


def get_attendance_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Maktab lokatsiyasi sozlash")],
            [KeyboardButton(text="📅 Bugungi davomat")],
            [KeyboardButton(text="🏫 Maktab bo'yicha davomat")],
            [KeyboardButton(text="🔙 Orqaga"), KeyboardButton(text="🏠 Bosh menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Bo'limni tanlang...",
    )


def _location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📍 Lokatsiyani yuborish", request_location=True)],
            [KeyboardButton(text="❌ Bekor qilish")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Lokatsiyani yuboring...",
    )


def _build_school_select_keyboard(schools: list, page: int, mode: str) -> InlineKeyboardBuilder:
    pagination = SchoolPagination(page=page, per_page=SCHOOL_PAGE_SIZE, total_schools=len(schools))
    start = (pagination.page - 1) * pagination.per_page
    end = start + pagination.per_page
    page_schools = schools[start:end]

    builder = InlineKeyboardBuilder()
    for school in page_schools:
        suffix = "📍" if school.latitude is not None and school.longitude is not None else ""
        builder.button(
            text=f"{school.number}-m {suffix}".strip(),
            callback_data=f"{mode}_school:{school.id}",
        )
    builder.adjust(5)

    nav_row = []
    if pagination.has_previous():
        nav_row.append(InlineKeyboardButton(text="◀️ Oldingi", callback_data=f"{mode}_page:{pagination.page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"📍 {pagination.page}/{pagination.total_pages}", callback_data=f"{mode}_page_info"))
    if pagination.has_next():
        nav_row.append(InlineKeyboardButton(text="▶️ Keyingi", callback_data=f"{mode}_page:{pagination.page + 1}"))
    if nav_row:
        builder.row(*nav_row)

    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"{mode}_cancel"))
    return builder


def _build_report_nav(mode: str, page: int, total_pages: int, school_id: uuid.UUID | None = None):
    if total_pages <= 1:
        return None

    builder = InlineKeyboardBuilder()
    if page > 1:
        if school_id is None:
            builder.button(text="◀️ Oldingi", callback_data=f"{mode}:{page - 1}")
        else:
            builder.button(text="◀️ Oldingi", callback_data=f"{mode}:{school_id}:{page - 1}")

    if school_id is None:
        builder.button(text=f"📍 {page}/{total_pages}", callback_data=f"{mode}_info:{page}")
    else:
        builder.button(text=f"📍 {page}/{total_pages}", callback_data=f"{mode}_info:{school_id}:{page}")

    if page < total_pages:
        if school_id is None:
            builder.button(text="▶️ Keyingi", callback_data=f"{mode}:{page + 1}")
        else:
            builder.button(text="▶️ Keyingi", callback_data=f"{mode}:{school_id}:{page + 1}")
    builder.adjust(3)
    return builder.as_markup()


def _format_report_rows(rows: list[tuple]) -> list[str]:
    lines: list[str] = []
    for attendance, teacher, school in rows:
        created_at = attendance.created_at
        if created_at and created_at.tzinfo is not None:
            ts = created_at.astimezone(TASHKENT_TZ).strftime("%d.%m.%Y %H:%M")
        elif created_at:
            ts = created_at.strftime("%d.%m.%Y %H:%M")
        else:
            ts = "Noma'lum"

        teacher_name = teacher.full_name or f"ID:{teacher.telegram_id}"
        username = f"@{teacher.username}" if teacher.username else "username yo'q"
        action = "🟢 Keldi" if attendance.action == "check_in" else "🔴 Ketdi"
        status = "Ichkarida" if attendance.is_inside else "Tashqarida"

        lines.extend(
            [
                f"{action} | {ts}",
                f"👤 {teacher_name} ({username})",
                f"🏫 {school.name}",
                f"📏 {attendance.distance_m} m | {status}",
                "",
            ]
        )
    return lines


async def _send_today_report(target: Message, session: AsyncSession, page: int, edit: bool = False) -> None:
    total, rows = await list_attendance(
        session=session,
        page=page,
        per_page=REPORT_PAGE_SIZE,
        attendance_date=tashkent_today(),
    )

    if total == 0:
        text = "📭 Bugun davomat yozuvlari yo'q."
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return

    total_pages = max(1, (total + REPORT_PAGE_SIZE - 1) // REPORT_PAGE_SIZE)
    page = max(1, min(page, total_pages))

    lines = [f"📅 Bugungi davomat ({total} ta)", ""]
    lines.extend(_format_report_rows(rows))
    keyboard = _build_report_nav("attendance_today", page, total_pages)

    text = "\n".join(lines).strip()
    if edit:
        await target.edit_text(text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


async def _send_school_report(
    target: Message,
    session: AsyncSession,
    school_id: uuid.UUID,
    page: int,
    edit: bool = False,
) -> None:
    school = await get_school_by_id(session, school_id)
    if not school:
        if edit:
            await target.edit_text("❌ Maktab topilmadi.")
        else:
            await target.answer("❌ Maktab topilmadi.")
        return

    total, rows = await list_attendance(
        session=session,
        page=page,
        per_page=REPORT_PAGE_SIZE,
        school_id=school_id,
    )

    if total == 0:
        text = f"📭 {school.name} uchun davomat yozuvlari yo'q."
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
        return

    total_pages = max(1, (total + REPORT_PAGE_SIZE - 1) // REPORT_PAGE_SIZE)
    page = max(1, min(page, total_pages))

    lines = [f"🏫 {school.name} bo'yicha davomat ({total} ta)", ""]
    lines.extend(_format_report_rows(rows))
    keyboard = _build_report_nav("attendance_school", page, total_pages, school_id=school_id)

    text = "\n".join(lines).strip()
    if edit:
        await target.edit_text(text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


@router.message(F.text == "🕒 Davomat")
async def attendance_menu(
    message: Message,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    await message.answer("🕒 DAVOMAT BO'LIMI", reply_markup=get_attendance_menu_keyboard())


@router.message(
    or_f(Command("cancel"), F.text == "❌ Bekor qilish"),
    StateFilter(
        SuperadminAttendanceStates.waiting_for_school_location,
        SuperadminAttendanceStates.waiting_for_radius,
    ),
)
async def attendance_superadmin_cancel(
    message: Message,
    state: FSMContext,
    db_user,
    is_superadmin: bool = False,
) -> None:
    await cancel_current_action(message, state, db_user=db_user, is_superadmin=is_superadmin)


@router.message(F.text == "📍 Maktab lokatsiyasi sozlash")
async def set_school_location_start(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return

    schools = await list_schools(session)
    if not schools:
        await message.answer("📭 Maktablar ro'yxati bo'sh.")
        return

    keyboard = _build_school_select_keyboard(schools, page=1, mode="attendance_set")
    total_pages = max(1, (len(schools) + SCHOOL_PAGE_SIZE - 1) // SCHOOL_PAGE_SIZE)
    await message.answer(
        f"🏫 Lokatsiya sozlash uchun maktabni tanlang (1/{total_pages}):",
        reply_markup=keyboard.as_markup(),
    )


@router.callback_query(lambda c: c.data.startswith("attendance_set_page:"))
async def set_school_location_page(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return

    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Noto'g'ri sahifa.", show_alert=True)
        return

    schools = await list_schools(session)
    keyboard = _build_school_select_keyboard(schools, page=page, mode="attendance_set")
    total_pages = max(1, (len(schools) + SCHOOL_PAGE_SIZE - 1) // SCHOOL_PAGE_SIZE)
    await callback.message.edit_text(
        f"🏫 Lokatsiya sozlash uchun maktabni tanlang ({page}/{total_pages}):",
        reply_markup=keyboard.as_markup(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "attendance_set_page_info")
async def set_school_location_page_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(lambda c: c.data == "attendance_set_cancel")
async def set_school_location_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("✅ Bekor qilindi.")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("attendance_set_school:"))
async def set_school_location_pick(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return

    try:
        school_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Noto'g'ri maktab.", show_alert=True)
        return

    school = await get_school_by_id(session, school_id)
    if not school:
        await callback.answer("❌ Maktab topilmadi.", show_alert=True)
        return

    await state.update_data(attendance_school_id=str(school.id))
    await state.set_state(SuperadminAttendanceStates.waiting_for_school_location)
    await callback.message.answer(
        f"📍 {school.name} uchun lokatsiya yuboring.",
        reply_markup=_location_keyboard(),
    )
    await callback.answer()


@router.message(StateFilter(SuperadminAttendanceStates.waiting_for_school_location), F.location)
async def set_school_location_input(
    message: Message,
    state: FSMContext,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return

    await state.update_data(
        attendance_school_lat=message.location.latitude,
        attendance_school_lon=message.location.longitude,
    )
    await state.set_state(SuperadminAttendanceStates.waiting_for_radius)
    await message.answer("📏 Radiusni metrda yuboring (masalan: 100, 150, 200).")


@router.message(StateFilter(SuperadminAttendanceStates.waiting_for_school_location))
async def set_school_location_expect_location(message: Message) -> None:
    await message.answer("Iltimos, lokatsiya yuboring yoki /cancel bosing.")


@router.message(StateFilter(SuperadminAttendanceStates.waiting_for_radius), F.text)
async def set_school_radius_input(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return

    try:
        radius_m = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Radius raqam bo'lishi kerak. Masalan: 150")
        return

    if radius_m < 20 or radius_m > 5000:
        await message.answer("❌ Radius 20 dan 5000 gacha bo'lishi kerak.")
        return

    data = await state.get_data()
    school_id = data.get("attendance_school_id")
    latitude = data.get("attendance_school_lat")
    longitude = data.get("attendance_school_lon")

    if school_id is None or latitude is None or longitude is None:
        await message.answer("❌ Ma'lumot yetarli emas. Qaytadan urinib ko'ring.")
        await state.clear()
        await state.update_data(menu_active=True)
        return

    try:
        school = await set_school_location(
            session=session,
            school_id=int(school_id),
            latitude=float(latitude),
            longitude=float(longitude),
            radius_m=radius_m,
        )
    except AttendanceServiceError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await state.update_data(menu_active=True)
    await message.answer(
        f"✅ Lokatsiya saqlandi: {school.name}\n📏 Radius: {school.radius_m} m",
        reply_markup=get_attendance_menu_keyboard(),
    )


@router.message(StateFilter(SuperadminAttendanceStates.waiting_for_radius))
async def set_school_radius_expect_text(message: Message) -> None:
    await message.answer("Iltimos, radiusni raqamda yuboring yoki /cancel bosing.")


@router.message(Command("attendance_today"))
@router.message(F.text == "📅 Bugungi davomat")
async def attendance_today(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return
    await _send_today_report(message, session, page=1, edit=False)


@router.callback_query(lambda c: c.data.startswith("attendance_today:"))
async def attendance_today_page(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Noto'g'ri sahifa.", show_alert=True)
        return
    await _send_today_report(callback.message, session, page=page, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("attendance_today_info:"))
async def attendance_today_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(Command("attendance_school"))
@router.message(F.text == "🏫 Maktab bo'yicha davomat")
async def attendance_school_select_start(
    message: Message,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        return

    schools = await list_schools(session)
    if not schools:
        await message.answer("📭 Maktablar ro'yxati bo'sh.")
        return

    keyboard = _build_school_select_keyboard(schools, page=1, mode="attendance_report")
    total_pages = max(1, (len(schools) + SCHOOL_PAGE_SIZE - 1) // SCHOOL_PAGE_SIZE)
    await message.answer(
        f"🏫 Hisobot uchun maktabni tanlang (1/{total_pages}):",
        reply_markup=keyboard.as_markup(),
    )


@router.callback_query(lambda c: c.data.startswith("attendance_report_page:"))
async def attendance_school_select_page(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return
    try:
        page = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Noto'g'ri sahifa.", show_alert=True)
        return

    schools = await list_schools(session)
    keyboard = _build_school_select_keyboard(schools, page=page, mode="attendance_report")
    total_pages = max(1, (len(schools) + SCHOOL_PAGE_SIZE - 1) // SCHOOL_PAGE_SIZE)
    await callback.message.edit_text(
        f"🏫 Hisobot uchun maktabni tanlang ({page}/{total_pages}):",
        reply_markup=keyboard.as_markup(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "attendance_report_page_info")
async def attendance_school_select_info(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(lambda c: c.data == "attendance_report_cancel")
async def attendance_report_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("✅ Bekor qilindi.")
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("attendance_report_school:"))
async def attendance_school_select(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return

    try:
        school_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Noto'g'ri maktab.", show_alert=True)
        return

    await _send_school_report(callback.message, session, school_id=school_id, page=1, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("attendance_school:"))
async def attendance_school_page(
    callback: CallbackQuery,
    session: AsyncSession,
    is_superadmin: bool = False,
) -> None:
    if not is_superadmin:
        await callback.answer("⛔ Ruxsat yo'q", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    try:
        school_id = int(parts[1])
        page = int(parts[2])
    except ValueError:
        await callback.answer("❌ Noto'g'ri so'rov.", show_alert=True)
        return

    await _send_school_report(callback.message, session, school_id=school_id, page=page, edit=True)
    await callback.answer()


@router.callback_query(lambda c: c.data.startswith("attendance_school_info:"))
async def attendance_school_info(callback: CallbackQuery) -> None:
    await callback.answer()
