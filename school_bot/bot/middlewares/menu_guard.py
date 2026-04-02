from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.fsm.context import FSMContext


class MenuGuardMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            if event.chat.type != "private":
                return await handler(event, data)

            is_authorized = bool(
                data.get("is_superadmin")
                or data.get("is_teacher")
                or data.get("is_librarian")
                or data.get("is_group_admin")
            )

            state: FSMContext | None = data.get("state")
            if state is not None:
                current_state = await state.get_state()
                if current_state is not None:
                    return await handler(event, data)

            text = event.text or ""
            if text.startswith("/start") or text.startswith("/help") or text.startswith("/stop") or text.startswith("/cancel") or text == "🏠 Bosh menyu":
                return await handler(event, data)

            if text.startswith("/") and not is_authorized:
                await event.answer("Kechirasiz, bu buyruq faqat admin va teacherlar uchun")
                return

            if not is_authorized:
                return

            menu_active = False
            if state is not None:
                state_data = await state.get_data()
                menu_active = bool(state_data.get("menu_active"))

            if not menu_active:
                return

            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            if not event.message or event.message.chat.type != "private":
                return await handler(event, data)

            is_authorized = bool(
                data.get("is_superadmin")
                or data.get("is_teacher")
                or data.get("is_librarian")
                or data.get("is_group_admin")
            )

            state: FSMContext | None = data.get("state")
            if state is not None:
                current_state = await state.get_state()
                if current_state is not None:
                    return await handler(event, data)

            if not is_authorized:
                return

            menu_active = False
            if state is not None:
                state_data = await state.get_data()
                menu_active = bool(state_data.get("menu_active"))

            if not menu_active:
                return

            return await handler(event, data)

        return await handler(event, data)
