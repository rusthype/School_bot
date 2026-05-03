"""
Notification helpers for book order status changes.

All functions are fire-and-forget from the handler's perspective:
they catch and log any TelegramAPIError so a notification failure
never rolls back a committed status change.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.order_status import get_status_text

logger = get_logger(__name__)


async def notify_teacher_status_change(
    bot: Bot,
    teacher_chat_id: int,
    order_id: int,
    old_status: str,
    new_status: str,
    comment: str | None = None,
) -> None:
    """Send a Telegram DM to the teacher when an order's status changes.

    Safe to call after every status mutation regardless of who triggered
    it (librarian or superadmin). Any TelegramAPIError is logged and
    swallowed — it must never cause a rollback.
    """
    text = (
        "🔄 <b>Buyurtma statusi o'zgartirildi</b>\n\n"
        f"🆔 Buyurtma #{order_id}\n"
        f"Eski status: {get_status_text(old_status) if old_status else '—'}\n"
        f"Yangi status: {get_status_text(new_status)}\n"
    )
    if comment:
        text += f"\n💬 Izoh: {comment}"

    try:
        await bot.send_message(chat_id=teacher_chat_id, text=text, parse_mode="HTML")
    except TelegramAPIError:
        logger.error(
            "notify_teacher_status_change: Telegram xabari yuborilmadi "
            "(order_id=%s, chat_id=%s)",
            order_id,
            teacher_chat_id,
            exc_info=True,
        )
    except Exception:
        logger.error(
            "notify_teacher_status_change: kutilmagan xato "
            "(order_id=%s, chat_id=%s)",
            order_id,
            teacher_chat_id,
            exc_info=True,
        )


async def notify_teacher_delivery_date_set(
    bot: Bot,
    teacher_chat_id: int,
    order_id: int,
    delivery_date,
) -> None:
    """Notify the teacher when a delivery date is set on their order.

    ``delivery_date`` is a datetime object; it is formatted as
    DD.MM.YYYY HH:MM in the message. Any send error is logged and
    swallowed.
    """
    date_str = delivery_date.strftime("%d.%m.%Y %H:%M")
    text = (
        "📅 <b>Yetkazib berish vaqti belgilandi</b>\n\n"
        f"🆔 Buyurtma #{order_id}\n"
        f"Sana: {date_str}"
    )
    try:
        await bot.send_message(chat_id=teacher_chat_id, text=text, parse_mode="HTML")
    except TelegramAPIError:
        logger.error(
            "notify_teacher_delivery_date_set: Telegram xabari yuborilmadi "
            "(order_id=%s, chat_id=%s)",
            order_id,
            teacher_chat_id,
            exc_info=True,
        )
    except Exception:
        logger.error(
            "notify_teacher_delivery_date_set: kutilmagan xato "
            "(order_id=%s, chat_id=%s)",
            order_id,
            teacher_chat_id,
            exc_info=True,
        )
