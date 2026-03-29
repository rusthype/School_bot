from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, limit: int = 30, window: int = 60) -> None:
        self._counts: dict[int, list[float]] = defaultdict(list)
        self._limit = limit
        self._window = window

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            now = time.monotonic()
            timestamps = self._counts[user.id]
            self._counts[user.id] = [t for t in timestamps if now - t < self._window]
            if len(self._counts[user.id]) >= self._limit:
                return  # silently drop
            self._counts[user.id].append(now)
        return await handler(event, data)
