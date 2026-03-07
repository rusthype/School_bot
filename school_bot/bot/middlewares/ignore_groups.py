from __future__ import annotations

from typing import Any, Callable, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject


class IgnoreGroupMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.chat.type in ("group", "supergroup"):
                return
        if isinstance(event, CallbackQuery):
            if event.message and event.message.chat.type in ("group", "supergroup"):
                return
        return await handler(event, data)
