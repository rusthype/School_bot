from __future__ import annotations

from typing import Callable

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

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
        if conn.dialect.name == "postgresql":
            await conn.execute(text("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_enum
            WHERE enumtypid = 'user_role'::regtype AND enumlabel = 'student'
        ) THEN
            ALTER TYPE user_role ADD VALUE 'student';
        END IF;
    END IF;
END $$;
"""))

            await conn.execute(text("""
ALTER TABLE groups ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active';
"""))
            await conn.execute(text("""
UPDATE groups SET status = 'active' WHERE status IS NULL;
"""))

