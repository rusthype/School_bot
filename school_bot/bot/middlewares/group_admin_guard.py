from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class GroupAdminGuardMiddleware(BaseMiddleware):
    def __init__(self, ttl_seconds: int = 60, max_cache: int = 2000) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[tuple[int, int], tuple[bool, float]] = {}
        self._max_cache = max_cache

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.chat.type in ("group", "supergroup"):
                user = event.from_user
                if not user:
                    return
                if not await self._is_group_admin(event.bot, event.chat.id, user.id):
                    return
        if isinstance(event, CallbackQuery):
            if event.message and event.message.chat.type in ("group", "supergroup"):
                user = event.from_user
                if not user:
                    return
                if not await self._is_group_admin(event.bot, event.message.chat.id, user.id):
                    return
        return await handler(event, data)

    async def _is_group_admin(self, bot, chat_id: int, user_id: int) -> bool:
        key = (chat_id, user_id)
        now = time.time()
        cached = self._cache.get(key)
        if cached:
            is_admin, ts = cached
            if now - ts <= self._ttl:
                return is_admin

        try:
            member = await bot.get_chat_member(chat_id, user_id)
            is_admin = member.status in ("creator", "administrator")
        except Exception:
            is_admin = False

        if len(self._cache) >= self._max_cache:
            self._cache.clear()
        self._cache[key] = (is_admin, now)
        return is_admin
