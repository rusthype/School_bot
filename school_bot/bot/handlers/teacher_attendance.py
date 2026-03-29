from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.handlers.common import cancel_current_action, get_main_keyboard
from school_bot.bot.services.attendance_service import AttendanceServiceError, create_teacher_attendance
from school_bot.bot.states.attendance import TeacherAttendanceStates

router = Router(name="teacher_attendance")


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


@router.message(F.text == "📍 Keldim")
async def teacher_check_in_start(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
) -> None:
    if not is_teacher:
        return
    await state.set_state(TeacherAttendanceStates.waiting_for_check_in_location)
    await message.answer(
        "📍 Maktabga kelganingizni tasdiqlash uchun lokatsiyangizni yuboring.",
        reply_markup=_location_keyboard(),
    )


@router.message(F.text == "🚪 Ketdim")
async def teacher_check_out_start(
    message: Message,
    state: FSMContext,
    is_teacher: bool = False,
) -> None:
    if not is_teacher:
        return
    await state.set_state(TeacherAttendanceStates.waiting_for_check_out_location)
    await message.answer(
        "📍 Maktabdan chiqqaningizni tasdiqlash uchun lokatsiyangizni yuboring.",
        reply_markup=_location_keyboard(),
    )


@router.message(
    (Command("cancel") | (F.text == "❌ Bekor qilish")),
    StateFilter(
        TeacherAttendanceStates.waiting_for_check_in_location,
        TeacherAttendanceStates.waiting_for_check_out_location,
    ),
)
async def teacher_attendance_cancel(
    message: Message,
    state: FSMContext,
    db_user,
    is_teacher: bool = False,
) -> None:
    await cancel_current_action(message, state, db_user=db_user, is_teacher=is_teacher)


@router.message(
    TeacherAttendanceStates.waiting_for_check_in_location,
    F.location,
)
async def teacher_check_in_location(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
) -> None:
    if not is_teacher:
        return

    try:
        result = await create_teacher_attendance(
            session=session,
            teacher_user_id=db_user.id,
            action="check_in",
            teacher_lat=message.location.latitude,
            teacher_lon=message.location.longitude,
        )
    except AttendanceServiceError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await state.update_data(menu_active=True)
    inside_text = "✅ Maktab hududi ichida" if result.attendance.is_inside else "⚠️ Maktab hududidan tashqarida"
    await message.answer(
        "\n".join(
            [
                "✅ Keldim qayd etildi.",
                f"🏫 Maktab: {result.school.name}",
                f"📏 Masofa: {result.attendance.distance_m} m",
                inside_text,
            ]
        ),
        reply_markup=get_main_keyboard(is_teacher=True),
    )


@router.message(
    TeacherAttendanceStates.waiting_for_check_out_location,
    F.location,
)
async def teacher_check_out_location(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user,
    is_teacher: bool = False,
) -> None:
    if not is_teacher:
        return

    try:
        result = await create_teacher_attendance(
            session=session,
            teacher_user_id=db_user.id,
            action="check_out",
            teacher_lat=message.location.latitude,
            teacher_lon=message.location.longitude,
        )
    except AttendanceServiceError as exc:
        await message.answer(str(exc))
        return

    await state.clear()
    await state.update_data(menu_active=True)
    inside_text = "✅ Maktab hududi ichida" if result.attendance.is_inside else "⚠️ Maktab hududidan tashqarida"
    await message.answer(
        "\n".join(
            [
                "✅ Ketdim qayd etildi.",
                f"🏫 Maktab: {result.school.name}",
                f"📏 Masofa: {result.attendance.distance_m} m",
                inside_text,
            ]
        ),
        reply_markup=get_main_keyboard(is_teacher=True),
    )


@router.message(
    StateFilter(
        TeacherAttendanceStates.waiting_for_check_in_location,
        TeacherAttendanceStates.waiting_for_check_out_location,
    )
)
async def teacher_attendance_expect_location(message: Message) -> None:
    await message.answer("Iltimos, lokatsiyani yuboring yoki /cancel bosing.")
