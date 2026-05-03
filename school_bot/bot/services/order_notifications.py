"""
Notification helpers for book order status changes.

All functions are fire-and-forget from the handler's perspective:
they catch and log any TelegramAPIError so a notification failure
never rolls back a committed status change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.order_status import get_status_text

logger = get_logger(__name__)


async def _send_safe(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    log_label: str,
    order_id: int,
) -> None:
    """Send an HTML message and swallow any send failure with a logged error.

    Used by every escalation notification helper so the per-recipient
    fan-out in ``run_escalation_check`` never aborts the batch on a
    single bad chat_id.
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramAPIError:
        logger.error(
            "%s: Telegram xabari yuborilmadi (order_id=%s, chat_id=%s)",
            log_label,
            order_id,
            chat_id,
            exc_info=True,
        )
    except Exception:
        logger.error(
            "%s: kutilmagan xato (order_id=%s, chat_id=%s)",
            log_label,
            order_id,
            chat_id,
            exc_info=True,
        )


def _format_deadline(
    deadline: datetime | None,
    now: datetime,
) -> tuple[str, int]:
    if deadline is None:
        return "—", 0
    days_overdue = max((now.date() - deadline.date()).days, 0)
    return deadline.strftime("%d.%m.%Y"), days_overdue


async def notify_librarian_escalation(
    bot: Bot,
    librarian_chat_id: int,
    order_id: int,
    deadline: datetime | None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    deadline_str, days_overdue = _format_deadline(deadline, now)
    text = (
        "🚨 <b>MUDDATI O'TGAN BUYURTMA</b>\n\n"
        f"🆔 Buyurtma #{order_id}\n"
        f"📅 Yetkazish muddati: {deadline_str}\n"
        f"⚠️ Kechikkan: {days_overdue} kun\n\n"
        "Iltimos, zudlik bilan buyurtmani yetkazing yoki holatini yangilang."
    )
    await _send_safe(
        bot,
        librarian_chat_id,
        text,
        log_label="notify_librarian_escalation",
        order_id=order_id,
    )


async def notify_teacher_escalation(
    bot: Bot,
    teacher_chat_id: int,
    order_id: int,
    deadline: datetime | None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    deadline_str, days_overdue = _format_deadline(deadline, now)
    text = (
        "ℹ️ <b>Sizning buyurtmangiz kechikmoqda</b>\n\n"
        f"🆔 Buyurtma #{order_id}\n"
        f"📅 Yetkazish muddati: {deadline_str}\n"
        f"⚠️ Kechikkan: {days_overdue} kun\n\n"
        "Superadminga xabar yuborildi va tez orada hal qilinadi."
    )
    await _send_safe(
        bot,
        teacher_chat_id,
        text,
        log_label="notify_teacher_escalation",
        order_id=order_id,
    )


async def notify_superadmin_escalation(
    bot: Bot,
    superadmin_chat_id: int,
    order_id: int,
    deadline: datetime | None,
    teacher_name: str | None = None,
    librarian_name: str | None = None,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    deadline_str, days_overdue = _format_deadline(deadline, now)
    teacher_str = escape(teacher_name) if teacher_name else "Noma'lum"
    librarian_str = escape(librarian_name) if librarian_name else "—"
    text = (
        "⚠️ <b>MUHIM: AUTO-ESCALATED BUYURTMA</b>\n\n"
        f"🆔 Buyurtma #{order_id}\n"
        f"👨‍🏫 O'qituvchi: {teacher_str}\n"
        f"📚 Kutubxonachi: {librarian_str}\n"
        f"📅 Yetkazish muddati: {deadline_str}\n"
        f"⚠️ Kechikkan: {days_overdue} kun\n\n"
        "❌ Hali yetkazilmagan! E'tibor qaratish kerak."
    )
    await _send_safe(
        bot,
        superadmin_chat_id,
        text,
        log_label="notify_superadmin_escalation",
        order_id=order_id,
    )


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
