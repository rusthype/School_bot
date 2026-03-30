from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import School


async def list_schools(session: AsyncSession) -> list[School]:
    result = await session.execute(select(School).order_by(School.number))
    return list(result.scalars().all())


async def get_school_by_id(session: AsyncSession, school_id: int) -> School | None:
    result = await session.execute(select(School).where(School.id == school_id))
    return result.scalar_one_or_none()


async def get_school_by_number(session: AsyncSession, number: int) -> School | None:
    result = await session.execute(select(School).where(School.number == number))
    return result.scalar_one_or_none()


async def seed_schools(session_factory) -> None:
    async with session_factory() as session:
        existing = await session.scalar(select(func.count()).select_from(School))
        if existing and existing > 0:
            return

        session.add(School(number=39, name="39-maktab"))

        await session.commit()


async def add_school(session: AsyncSession, number: int, name: str | None = None) -> School:
    existing = await get_school_by_number(session, number)
    if existing:
        return existing
    school = School(number=number, name=name or f"{number}-maktab")
    session.add(school)
    await session.commit()
    await session.refresh(school)
    return school


async def remove_school(session: AsyncSession, school_id: int) -> bool:
    school = await get_school_by_id(session, school_id)
    if not school:
        return False
    await session.delete(school)
    await session.commit()
    return True
