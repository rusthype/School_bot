from __future__ import annotations

from typing import Callable

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_session_factory(database_url: str) -> tuple[AsyncEngine, Callable[[], AsyncSession]]:
    # Connected to Alochi platform DB (migrated from standalone school_bot_db)
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, session_maker


async def init_models(engine: AsyncEngine) -> None:
    # Schema is managed by Django migrations — DDL intentionally disabled.
    # The SQLAlchemy engine remains active for query operations only.
    import logging
    logging.getLogger(__name__).info("Schema managed by Django — skipping DDL")
