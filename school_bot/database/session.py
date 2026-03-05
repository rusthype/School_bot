from __future__ import annotations

from typing import Callable

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from school_bot.database.base import Base


def create_session_factory(database_url: str) -> tuple[AsyncEngine, Callable[[], AsyncSession]]:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, session_maker


async def init_models(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

