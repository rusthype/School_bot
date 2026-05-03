from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from school_bot.bot.services.logger_service import get_logger
from school_bot.database.models import (
    BookOrder,
    BookOrderItem,
    OrderStatusHistory,
    User,
    UserRole,
)

logger = get_logger(__name__)

OPEN_STATUSES: tuple[str, ...] = ("pending", "processing", "confirmed")
ESCALATION_COMMENT = "Auto-escalated: delivery overdue"


@dataclass
class EscalationResult:
    order_id: int
    history_id: int | None


async def find_overdue_orders(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> list[BookOrder]:
    now = now or datetime.now(timezone.utc)
    stmt = (
        select(BookOrder)
        .where(
            BookOrder.status.in_(OPEN_STATUSES),
            BookOrder.escalated.is_(False),
            BookOrder.delivery_deadline.is_not(None),
            BookOrder.delivery_deadline < now,
        )
        .options(
            selectinload(BookOrder.teacher),
            selectinload(BookOrder.librarian),
            selectinload(BookOrder.items).selectinload(BookOrderItem.book),
        )
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def list_superadmins(session: AsyncSession) -> list[User]:
    result = await session.execute(
        select(User).where(User.role == UserRole.superadmin)
    )
    return list(result.scalars().all())


async def list_superadmin_chat_ids(session: AsyncSession) -> list[int]:
    admins = await list_superadmins(session)
    return [a.telegram_id for a in admins if a.telegram_id is not None]


async def escalate_order(
    session: AsyncSession,
    order: BookOrder,
) -> EscalationResult:
    # status doesn't actually change — history row is purely an audit trail
    # of the auto-escalation event. Attribute to the first superadmin we
    # find (system actor); if none exists, skip the row but still flip
    # the flag so the order isn't picked up again on the next run.
    superadmins = await list_superadmins(session)
    attributed_user_id: int | None = superadmins[0].id if superadmins else None

    history_id: int | None = None
    if attributed_user_id is not None:
        history = OrderStatusHistory(
            order_id=order.id,
            old_status=order.status,
            new_status=order.status,
            changed_by_id=attributed_user_id,
            comment=ESCALATION_COMMENT,
        )
        session.add(history)
        await session.flush()
        history_id = history.id
    else:
        logger.warning(
            "escalate_order: no superadmin found, history row skipped (order_id=%s)",
            order.id,
        )

    order.escalated = True
    return EscalationResult(order_id=order.id, history_id=history_id)
