from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Bot

from school_bot.bot.services.escalation_service import (
    escalate_order,
    find_overdue_orders,
    list_superadmin_chat_ids,
)
from school_bot.bot.services.logger_service import get_logger
from school_bot.bot.services.order_notifications import (
    notify_librarian_escalation,
    notify_superadmin_escalation,
    notify_teacher_escalation,
)

logger = get_logger(__name__)


async def run_escalation_check(bot: Bot, session_factory) -> int:
    notifications_sent = 0
    escalated_count = 0

    async with session_factory() as session:
        orders = await find_overdue_orders(session)
        if not orders:
            logger.info("run_escalation_check: no overdue orders")
            return 0

        superadmin_chat_ids = await list_superadmin_chat_ids(session)
        now = datetime.now(timezone.utc)

        for order in orders:
            try:
                await escalate_order(session, order)
                escalated_count += 1

                teacher = order.teacher
                librarian = order.librarian
                teacher_chat_id = teacher.telegram_id if teacher else None
                librarian_chat_id = librarian.telegram_id if librarian else None
                teacher_name = (
                    (teacher.full_name or str(teacher.telegram_id))
                    if teacher
                    else None
                )
                librarian_name = (
                    (librarian.full_name or str(librarian.telegram_id))
                    if librarian
                    else None
                )

                if librarian_chat_id:
                    await notify_librarian_escalation(
                        bot,
                        librarian_chat_id,
                        order.id,
                        order.delivery_deadline,
                        now=now,
                    )
                    notifications_sent += 1

                if teacher_chat_id:
                    await notify_teacher_escalation(
                        bot,
                        teacher_chat_id,
                        order.id,
                        order.delivery_deadline,
                        now=now,
                    )
                    notifications_sent += 1

                for admin_chat_id in superadmin_chat_ids:
                    await notify_superadmin_escalation(
                        bot,
                        admin_chat_id,
                        order.id,
                        order.delivery_deadline,
                        teacher_name=teacher_name,
                        librarian_name=librarian_name,
                        now=now,
                    )
                    notifications_sent += 1
            except Exception:
                logger.error(
                    "run_escalation_check: failed to process order_id=%s",
                    order.id,
                    exc_info=True,
                )

        await session.commit()

    logger.info(
        "run_escalation_check: escalated %d orders, sent %d notifications",
        escalated_count,
        notifications_sent,
    )
    return escalated_count
