from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import BotSettings


async def get_or_create_settings(session: AsyncSession) -> BotSettings:
    result = await session.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings:
        return settings

    settings = BotSettings(id=1)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


async def update_settings(session: AsyncSession, **changes) -> BotSettings:
    settings = await get_or_create_settings(session)
    for key, value in changes.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    await session.commit()
    await session.refresh(settings)
    return settings
