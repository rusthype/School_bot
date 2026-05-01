from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DbSessionMiddleware(BaseMiddleware):
    """Inject a per-update SQLAlchemy session AND the session factory.

    The factory is exposed alongside the session so handlers can pass
    it to coroutines whose lifetime outlives the current update — e.g.
    the 24h digest scheduler in ``teacher_notifier.schedule_teacher_digest``
    needs to open its own session 24h from now, well after the
    current update's session has closed.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            data["session_factory"] = self._session_factory
            return await handler(event, data)

