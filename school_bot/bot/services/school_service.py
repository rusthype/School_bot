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

        for i in range(1, 47):
            session.add(School(number=i, name=f"{i}-maktab"))

        await session.commit()
