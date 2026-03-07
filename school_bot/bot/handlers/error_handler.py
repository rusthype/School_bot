from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Dict

from aiogram import Router
from aiogram.exceptions import TelegramUnauthorizedError, TelegramBadRequest
from aiogram.types import ErrorEvent
from sqlalchemy.exc import SQLAlchemyError

from school_bot.bot.config import Settings

router = Router(name="errors")
logger = logging.getLogger(__name__)


def _build_context(event: ErrorEvent) -> Dict[str, Any]:
    update = event.update
    if not update:
        return {}
    update_id = getattr(update, "update_id", None)
    user_id = None
    chat_id = None

    try:
        event_obj = update.event
        if event_obj and hasattr(event_obj, "from_user") and event_obj.from_user:
            user_id = event_obj.from_user.id
        if event_obj and hasattr(event_obj, "chat") and event_obj.chat:
            chat_id = event_obj.chat.id
    except Exception:
        pass

    return {
        "update_id": update_id,
        "user_id": user_id,
        "chat_id": chat_id,
    }


def _should_alert_superadmin(error: Exception) -> bool:
    critical_errors = (
        TelegramUnauthorizedError,
        MemoryError,
        ConnectionError,
        SQLAlchemyError,
        asyncio.TimeoutError,
    )
    return isinstance(error, critical_errors)


async def _silent_alert_superadmins(bot, error: Exception, context: Dict[str, Any]) -> None:
    settings = Settings()
    if not settings.superadmin_ids:
        return

    alert_text = (
        "🚨 <b>Kritik xatolik</b>\n"
        f"<b>Type:</b> {type(error).__name__}\n"
        f"<b>Message:</b> {str(error)[:200]}\n"
        f"<b>User:</b> {context.get('user_id')}\n"
        f"<b>Chat:</b> {context.get('chat_id')}"
    )

    for admin_id in settings.superadmin_ids:
        try:
            await bot.send_message(admin_id, alert_text)
        except Exception:
            pass


@router.errors()
async def silent_global_error_handler(event: ErrorEvent) -> bool:
    error = event.exception
    context = _build_context(event)

    if isinstance(error, TelegramBadRequest) and "query is too old" in str(error).lower():
        return True

    logger.error(
        "Silent error [%s]: %s | context=%s\n%s",
        type(error).__name__,
        error,
        context,
        traceback.format_exc(),
    )

    if _should_alert_superadmin(error):
        try:
            await _silent_alert_superadmins(event.bot, error, context)
        except Exception:
            pass

    return True
