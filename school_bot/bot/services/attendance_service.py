from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.services.profile_service import get_profile_by_user_id
from school_bot.bot.utils.geo import haversine_distance_m
from school_bot.database.models import School, TeacherAttendance, User

TASHKENT_TZ = ZoneInfo("Asia/Tashkent")


class AttendanceServiceError(Exception):
    pass


@dataclass
class AttendanceCreateResult:
    attendance: TeacherAttendance
    school: School


def tashkent_now() -> datetime:
    return datetime.now(TASHKENT_TZ)


def tashkent_today() -> date:
    return tashkent_now().date()


async def set_school_location(
    session: AsyncSession,
    school_id: int,
    latitude: float,
    longitude: float,
    radius_m: int,
) -> School:
    school = await session.get(School, school_id)
    if not school:
        raise AttendanceServiceError("Maktab topilmadi.")

    school.latitude = latitude
    school.longitude = longitude
    school.radius_m = radius_m
    await session.commit()
    await session.refresh(school)
    return school


async def _get_teacher_school(session: AsyncSession, teacher_user_id: int) -> School:
    profile = await get_profile_by_user_id(session, teacher_user_id)
    if not profile or not profile.school_id:
        raise AttendanceServiceError("Sizga maktab biriktirilmagan.")

    school = await session.get(School, profile.school_id)
    if not school:
        raise AttendanceServiceError("Maktab topilmadi.")

    if school.latitude is None or school.longitude is None:
        raise AttendanceServiceError(
            "Maktab lokatsiyasi hali sozlanmagan.\nAdministrator bilan bog'laning."
        )

    return school


async def _has_action_today(
    session: AsyncSession,
    teacher_user_id: int,
    action: str,
    day: date,
) -> bool:
    existing = await session.scalar(
        select(TeacherAttendance.id).where(
            and_(
                TeacherAttendance.teacher_id == teacher_user_id,
                TeacherAttendance.attendance_date == day,
                TeacherAttendance.action == action,
            )
        )
    )
    return existing is not None


async def create_teacher_attendance(
    session: AsyncSession,
    teacher_user_id: int,
    action: str,
    teacher_lat: float,
    teacher_lon: float,
) -> AttendanceCreateResult:
    if action not in {"check_in", "check_out"}:
        raise AttendanceServiceError("Noto'g'ri davomat turi.")

    school = await _get_teacher_school(session, teacher_user_id)
    day = tashkent_today()

    has_check_in = await _has_action_today(session, teacher_user_id, "check_in", day)
    has_check_out = await _has_action_today(session, teacher_user_id, "check_out", day)

    if action == "check_in" and has_check_in:
        raise AttendanceServiceError("Bugun allaqachon 'Keldim' yuborgansiz.")

    if action == "check_out":
        if not has_check_in:
            raise AttendanceServiceError("Avval 'Keldim' yuboring.")
        if has_check_out:
            raise AttendanceServiceError("Bugun allaqachon 'Ketdim' yuborgansiz.")

    distance_m = haversine_distance_m(
        teacher_lat,
        teacher_lon,
        float(school.latitude),
        float(school.longitude),
    )
    is_inside = distance_m <= int(school.radius_m or 150)

    attendance = TeacherAttendance(
        teacher_id=teacher_user_id,
        school_id=school.id,
        action=action,
        teacher_lat=teacher_lat,
        teacher_lon=teacher_lon,
        school_lat=float(school.latitude),
        school_lon=float(school.longitude),
        distance_m=distance_m,
        is_inside=is_inside,
        attendance_date=day,
    )
    session.add(attendance)
    await session.commit()
    await session.refresh(attendance)

    return AttendanceCreateResult(attendance=attendance, school=school)


async def list_attendance(
    session: AsyncSession,
    page: int = 1,
    per_page: int = 20,
    attendance_date: date | None = None,
    school_id: int | None = None,
) -> tuple[int, list[tuple[TeacherAttendance, User, School]]]:
    filters = []
    if attendance_date is not None:
        filters.append(TeacherAttendance.attendance_date == attendance_date)
    if school_id is not None:
        filters.append(TeacherAttendance.school_id == school_id)

    where_clause = and_(*filters) if filters else None

    count_stmt = select(func.count()).select_from(TeacherAttendance)
    if where_clause is not None:
        count_stmt = count_stmt.where(where_clause)
    total = await session.scalar(count_stmt) or 0

    page = max(1, page)
    offset = (page - 1) * per_page

    stmt = (
        select(TeacherAttendance, User, School)
        .join(User, User.id == TeacherAttendance.teacher_id)
        .join(School, School.id == TeacherAttendance.school_id)
        .order_by(TeacherAttendance.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    if where_clause is not None:
        stmt = stmt.where(where_clause)

    result = await session.execute(stmt)
    rows = list(result.all())
    return total, rows
