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

            await conn.execute(text("""
ALTER TABLE schools ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION;
"""))
            await conn.execute(text("""
ALTER TABLE schools ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;
"""))
            await conn.execute(text("""
ALTER TABLE schools ADD COLUMN IF NOT EXISTS radius_m INTEGER DEFAULT 150;
"""))
            await conn.execute(text("""
UPDATE schools SET radius_m = 150 WHERE radius_m IS NULL;
"""))

            await conn.execute(text("""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'attendance_action') THEN
        CREATE TYPE attendance_action AS ENUM ('check_in', 'check_out');
    END IF;
END $$;
"""))

            await conn.execute(text("""
CREATE TABLE IF NOT EXISTS teacher_attendance (
    id SERIAL PRIMARY KEY,
    teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    action attendance_action NOT NULL,
    teacher_lat DOUBLE PRECISION NOT NULL,
    teacher_lon DOUBLE PRECISION NOT NULL,
    school_lat DOUBLE PRECISION NOT NULL,
    school_lon DOUBLE PRECISION NOT NULL,
    distance_m INTEGER NOT NULL,
    is_inside BOOLEAN NOT NULL DEFAULT FALSE,
    attendance_date DATE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""))
            await conn.execute(text("""
CREATE UNIQUE INDEX IF NOT EXISTS uq_teacher_attendance_daily_action
ON teacher_attendance (teacher_id, attendance_date, action);
"""))
            await conn.execute(text("""
CREATE INDEX IF NOT EXISTS ix_teacher_attendance_date
ON teacher_attendance (attendance_date);
"""))
            await conn.execute(text("""
CREATE INDEX IF NOT EXISTS ix_teacher_attendance_school_date
ON teacher_attendance (school_id, attendance_date);
"""))
