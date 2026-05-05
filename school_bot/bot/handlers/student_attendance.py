from datetime import datetime, date, timedelta
from typing import Any

from aiogram import Router, F, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from school_bot.database.models import User, Profile, StudentDailyAttendance
from school_bot.bot.states.attendance import StudentClassAttendanceStates
from school_bot.bot.handlers.common import get_main_keyboard
from school_bot.bot.services.attendance_service import tashkent_today
from school_bot.bot.services.vision_service import run_ocr_pipeline
from school_bot.bot.services.logger_service import get_logger

router = Router(name="student_attendance")
logger = get_logger(__name__)

STATUS_MAP = {
    "present": "✅ Keldi",
    "absent": "❌ Kelmadi",
    "late": "🟡 Kech",
}

STATUS_DB = {
    "present": "present",
    "absent": "absent",
    "late": "late",
}


def get_date_selection_keyboard() -> InlineKeyboardMarkup:
    today = tashkent_today()
    yesterday = today - timedelta(days=1)
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"📅 Bugun ({today.strftime('%d.%m')})", callback_data="sca_date:today"),
        InlineKeyboardButton(text=f"📅 Kecha ({yesterday.strftime('%d.%m')})", callback_data="sca_date:yesterday"),
    )
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="sca_cancel"))
    return builder.as_markup()


def get_method_selection_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📸 Rasm yuboring (label only)", callback_data="sca_noop"))
    builder.row(InlineKeyboardButton(text="⌨️ Qo'lda belgilash", callback_data="sca_manual"))
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="sca_cancel"))
    return builder.as_markup()


def build_manual_keyboard(students: list[dict[str, Any]], marks: dict[int, str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    for s in students:
        s_id = s["id"]
        status = marks.get(s_id)
        
        name_text = s["name"]
        
        # Row per student
        row = [InlineKeyboardButton(text=name_text, callback_data="sca_noop")]
        
        for code, label in STATUS_MAP.items():
            text = label
            if status == code:
                text = f"● {label}"
            row.append(InlineKeyboardButton(text=text, callback_data=f"sca_mark:{s_id}:{code}"))
        
        builder.row(*row)
    
    marked_count = len(marks)
    total_count = len(students)
    
    builder.row(
        InlineKeyboardButton(text=f"💾 Saqlash ({marked_count}/{total_count})", callback_data="sca_save"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="sca_cancel"),
    )
    
    return builder.as_markup()


@router.message(F.text == "📸 O'quvchi davomati")
async def sca_start(message: Message, state: FSMContext, session: AsyncSession, profile: Profile) -> None:
    if not profile or not profile.school_id:
        await message.answer("⚠️ Ushbu funksiyadan foydalanish uchun profilingizda maktab biriktirilgan bo'lishi kerak.")
        return

    # Load students
    stmt = (
        select(Profile)
        .where(
            Profile.profile_type == "student",
            Profile.school_id == profile.school_id,
            Profile.is_approved == True
        )
        .order_by(Profile.last_name, Profile.first_name)
        .limit(100)
    )
    result = await session.execute(stmt)
    students = result.scalars().all()
    
    if not students:
        await message.answer("⚠️ Maktabingizda tasdiqlangan o'quvchilar topilmadi.")
        return

    student_list = [{"id": s.id, "name": f"{s.last_name} {s.first_name}".strip()} for s in students]
    
    await state.set_state(StudentClassAttendanceStates.choosing_date)
    await state.update_data(
        teacher_school_id=profile.school_id,
        students=student_list,
        marks={},
    )
    
    await message.answer(
        "📅 Qaysi sana uchun davomat qilmoqchisiz?",
        reply_markup=get_date_selection_keyboard()
    )


@router.callback_query(F.data.startswith("sca_date:"), StudentClassAttendanceStates.choosing_date)
async def sca_date_selected(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":")[1]
    today = tashkent_today()
    
    if choice == "today":
        selected_date = today
    else:
        selected_date = today - timedelta(days=1)
    
    await state.update_data(attendance_date=selected_date.isoformat())
    await state.set_state(StudentClassAttendanceStates.waiting_for_photo_or_manual)
    
    await callback.message.edit_text(
        f"📅 Sana: {selected_date.strftime('%d.%m.%Y')}\n\n"
        "📸 O'quvchilar ro'yxati (jurnal) rasmini yuboring yoki qo'lda belgilang.",
        reply_markup=get_method_selection_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "sca_manual", StudentClassAttendanceStates.waiting_for_photo_or_manual)
async def sca_manual_start(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    students = data["students"]
    marks = data.get("marks", {})
    
    await state.set_state(StudentClassAttendanceStates.marking_manual)
    msg = await callback.message.edit_text(
        "⌨️ O'quvchilarni belgilang:",
        reply_markup=build_manual_keyboard(students, marks)
    )
    await state.update_data(manual_message_id=msg.message_id)
    await callback.answer()


@router.callback_query(F.data.startswith("sca_mark:"), StudentClassAttendanceStates.marking_manual)
async def sca_mark_student(callback: CallbackQuery, state: FSMContext) -> None:
    _, student_id, status = callback.data.split(":")
    student_id = int(student_id)
    
    data = await state.get_data()
    marks = data.get("marks", {}).copy()
    
    # Toggle off if same status clicked? No, just set.
    marks[student_id] = status
    
    await state.update_data(marks=marks)
    
    try:
        await callback.message.edit_reply_markup(
            reply_markup=build_manual_keyboard(data["students"], marks)
        )
    except Exception:
        # MessageNotModified
        pass
    
    await callback.answer()


@router.callback_query(F.data == "sca_save", StudentClassAttendanceStates.marking_manual)
async def sca_save_manual(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    marks = data.get("marks", {})
    
    if not marks:
        await callback.answer("Kamida 1 ta belgilang!", show_alert=True)
        return
    
    await save_attendance(callback.message, state, session, db_user, marks, source="manual")
    await callback.answer()


@router.message(F.photo, StudentClassAttendanceStates.waiting_for_photo_or_manual)
async def sca_photo_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    photo = message.photo[-1]
    await state.update_data(photo_file_id=photo.file_id)
    
    data = await state.get_data()
    students = data["students"]
    
    wait_msg = await message.answer("🔍 Rasm tahlil qilinmoqda, iltimos kuting...")
    
    result = await run_ocr_pipeline(bot, photo.file_id, students)
    marks = result["marks"]
    source = result["source"]
    
    await wait_msg.delete()
    
    if not source:
        # Fallback to manual
        await state.set_state(StudentClassAttendanceStates.marking_manual)
        msg = await message.answer(
            "⚠️ Rasmdan ma'lumotlarni o'qib bo'lmadi. Iltimos, qo'lda belgilang:",
            reply_markup=build_manual_keyboard(students, {})
        )
        await state.update_data(manual_message_id=msg.message_id)
        return

    # Show AI/OCR results
    await state.update_data(marks=marks, ocr_source=source)
    await state.set_state(StudentClassAttendanceStates.confirming_result)
    
    # Prepare summary
    present = []
    absent = []
    late = []
    unknown = []
    
    student_map = {s["id"]: s["name"] for s in students}
    for s_id, status in marks.items():
        name = student_map.get(s_id, f"ID:{s_id}")
        if status == "present": present.append(name)
        elif status == "absent": absent.append(name)
        elif status == "late": late.append(name)
    
    for s in students:
        if s["id"] not in marks:
            unknown.append(s["name"])
            
    summary = [
        f"🔍 Tahlil natijasi ({source.upper()}) — {data['attendance_date']}",
        f"✅ Keldi ({len(present)}): {', '.join(present) if present else '-'}",
        f"❌ Kelmadi ({len(absent)}): {', '.join(absent) if absent else '-'}",
        f"🟡 Kech keldi ({len(late)}): {', '.join(late) if late else '-'}",
        f"❓ Aniqlanmadi ({len(unknown)}): {', '.join(unknown) if unknown else '-'}",
    ]
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="sca_confirm_ai"),
        InlineKeyboardButton(text="✏️ Tahrirlash", callback_data="sca_edit_ai"),
    )
    builder.row(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="sca_cancel"))
    
    await message.answer("\n".join(summary), reply_markup=builder.as_markup())


@router.callback_query(F.data == "sca_confirm_ai", StudentClassAttendanceStates.confirming_result)
async def sca_confirm_ai(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    data = await state.get_data()
    marks = data["marks"]
    source = data["ocr_source"]
    
    await save_attendance(callback.message, state, session, db_user, marks, source=source)
    await callback.answer()


@router.callback_query(F.data == "sca_edit_ai", StudentClassAttendanceStates.confirming_result)
async def sca_edit_ai(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    students = data["students"]
    marks = data["marks"]
    
    await state.set_state(StudentClassAttendanceStates.marking_manual)
    msg = await callback.message.edit_text(
        "✏️ Natijalarni tahrirlang:",
        reply_markup=build_manual_keyboard(students, marks)
    )
    await state.update_data(manual_message_id=msg.message_id)
    await callback.answer()


@router.callback_query(F.data == "sca_cancel")
async def sca_cancel(callback: CallbackQuery, state: FSMContext, is_superadmin: bool = False, is_teacher: bool = False) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Davomat bekor qilindi.")
    await callback.message.answer(
        "Asosiy menyu",
        reply_markup=get_main_keyboard(is_superadmin=is_superadmin, is_teacher=is_teacher)
    )
    await callback.answer()


@router.callback_query(F.data == "sca_noop")
async def sca_noop(callback: CallbackQuery) -> None:
    await callback.answer()


async def save_attendance(message: Message, state: FSMContext, session: AsyncSession, db_user: User, marks: dict[int, str], source: str) -> None:
    data = await state.get_data()
    att_date = date.fromisoformat(data["attendance_date"])
    photo_file_id = data.get("photo_file_id")
    
    # UPSERT
    for student_id, status in marks.items():
        stmt = insert(StudentDailyAttendance).values(
            teacher_id=db_user.id,
            student_profile_id=student_id,
            attendance_date=att_date,
            status=status,
            photo_file_id=photo_file_id,
            source=source,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_sda_teacher_student_date",
            set_={
                "status": stmt.excluded.status,
                "source": stmt.excluded.source,
                "photo_file_id": stmt.excluded.photo_file_id,
                "updated_at": func.now(),
            }
        )
        await session.execute(stmt)
    
    await session.commit()
    
    # Final message
    present_count = sum(1 for s in marks.values() if s == "present")
    absent_count = sum(1 for s in marks.values() if s == "absent")
    late_count = sum(1 for s in marks.values() if s == "late")
    total_students = len(data["students"])
    
    summary = [
        f"✅ Davomat saqlandi! — {att_date.strftime('%d.%m.%Y')}",
        f"✅ Keldi: {present_count} ta",
        f"❌ Kelmadi: {absent_count} ta",
        f"🟡 Kech keldi: {late_count} ta",
        "─────────────────",
        f"Jami: {len(marks)} / {total_students} ta",
    ]
    
    await state.clear()
    await message.answer(
        "\n".join(summary),
        reply_markup=get_main_keyboard(is_superadmin=False, is_teacher=True)
    )
