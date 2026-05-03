from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import BookOrder, BookOrderItem, OrderStatusHistory
from school_bot.bot.services.order_status import get_status_text


__all__ = ["get_status_text", "StatusChangeResult"]


@dataclass
class StatusChangeResult:
    """Carries the mutation outcome back to the handler so it can fire
    the teacher notification without an extra DB round-trip."""

    order: BookOrder
    old_status: str
    new_status: str
    comment: str | None


async def create_book_order(
    session: AsyncSession,
    teacher_id: int,
    items: list[tuple[int, int]],  # (book_id, quantity)
    notes: str | None = None,
    priority: str = "normal",
) -> BookOrder:
    priority_days = {"normal": 7, "urgent": 3, "express": 2}
    default_deadline = datetime.now(timezone.utc) + timedelta(days=priority_days.get(priority, 7))
    order = BookOrder(
        teacher_id=teacher_id,
        status="pending",
        notes=notes,
        priority=priority,
        delivery_deadline=default_deadline,
        escalated=False,
        updated_at=datetime.now(timezone.utc),
        updated_by_id=teacher_id,
    )
    session.add(order)
    await session.flush()

    for book_id, quantity in items:
        if quantity < 1:
            continue
        session.add(BookOrderItem(order_id=order.id, book_id=book_id, quantity=quantity))

    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status="",
            new_status="pending",
            changed_by_id=teacher_id,
            comment="Buyurtma yaratildi",
        )
    )

    await session.commit()
    await session.refresh(order)
    return order


async def get_book_order_by_id(session: AsyncSession, order_id: int) -> BookOrder | None:
    result = await session.execute(select(BookOrder).where(BookOrder.id == order_id))
    return result.scalar_one_or_none()


async def list_book_orders(
    session: AsyncSession,
    limit: int = 20,
    status: str | None = None,
) -> list[BookOrder]:
    query = select(BookOrder).order_by(BookOrder.created_at.desc())
    if status:
        query = query.where(BookOrder.status == status)
    if limit:
        query = query.limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def list_orders_by_teacher(
    session: AsyncSession,
    teacher_id: int,
    limit: int = 20,
) -> list[BookOrder]:
    query = (
        select(BookOrder)
        .where(BookOrder.teacher_id == teacher_id)
        .order_by(BookOrder.created_at.desc())
    )
    if limit:
        query = query.limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def list_order_items(session: AsyncSession, order_id: int) -> list[BookOrderItem]:
    result = await session.execute(select(BookOrderItem).where(BookOrderItem.order_id == order_id))
    return list(result.scalars().all())


async def set_delivery_date(
    session: AsyncSession,
    order: BookOrder,
    delivery_date: datetime,
    librarian_id: int,
) -> BookOrder:
    """Persist delivery_date and return the refreshed order.

    The handler is responsible for calling
    notify_teacher_delivery_date_set after this returns, using
    order.delivery_date which is now set.
    """
    order.delivery_date = delivery_date
    order.delivery_deadline = delivery_date
    order.escalated = False
    order.librarian_id = librarian_id
    await session.commit()
    await session.refresh(order)
    return order


async def confirm_order(
    session: AsyncSession,
    order: BookOrder,
    librarian_id: int,
) -> StatusChangeResult:
    old_status = order.status
    comment = "Tasdiqlandi"
    order.status = "confirmed"
    order.confirmed_at = datetime.now(timezone.utc)
    order.librarian_id = librarian_id
    order.updated_at = datetime.now(timezone.utc)
    order.updated_by_id = librarian_id
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=old_status,
            new_status=order.status,
            changed_by_id=librarian_id,
            comment=comment,
        )
    )
    await session.commit()
    await session.refresh(order)
    return StatusChangeResult(
        order=order,
        old_status=old_status,
        new_status="confirmed",
        comment=comment,
    )


async def mark_processing(
    session: AsyncSession,
    order: BookOrder,
    librarian_id: int,
) -> StatusChangeResult:
    old_status = order.status
    comment = "Jarayonda"
    order.status = "processing"
    order.librarian_id = librarian_id
    order.updated_at = datetime.now(timezone.utc)
    order.updated_by_id = librarian_id
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=old_status,
            new_status=order.status,
            changed_by_id=librarian_id,
            comment=comment,
        )
    )
    await session.commit()
    await session.refresh(order)
    return StatusChangeResult(
        order=order,
        old_status=old_status,
        new_status="processing",
        comment=comment,
    )


async def reject_order(
    session: AsyncSession,
    order: BookOrder,
    librarian_id: int,
    comment: str | None = None,
) -> StatusChangeResult:
    old_status = order.status
    history_comment = "Rad etildi" + (f": {comment}" if comment else "")
    order.status = "rejected"
    order.librarian_id = librarian_id
    order.updated_at = datetime.now(timezone.utc)
    order.updated_by_id = librarian_id
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=old_status,
            new_status=order.status,
            changed_by_id=librarian_id,
            comment=history_comment,
        )
    )
    await session.commit()
    await session.refresh(order)
    return StatusChangeResult(
        order=order,
        old_status=old_status,
        new_status="rejected",
        comment=history_comment,
    )


async def mark_delivered(
    session: AsyncSession,
    order: BookOrder,
    librarian_id: int,
) -> StatusChangeResult:
    old_status = order.status
    comment = "Yetkazildi"
    order.status = "delivered"
    order.delivered_at = datetime.now(timezone.utc)
    order.delivered_by_id = librarian_id
    order.librarian_id = librarian_id
    order.updated_at = datetime.now(timezone.utc)
    order.updated_by_id = librarian_id
    session.add(
        OrderStatusHistory(
            order_id=order.id,
            old_status=old_status,
            new_status=order.status,
            changed_by_id=librarian_id,
            comment=comment,
        )
    )
    await session.commit()
    await session.refresh(order)
    return StatusChangeResult(
        order=order,
        old_status=old_status,
        new_status="delivered",
        comment=comment,
    )


async def get_order_stats(session: AsyncSession) -> dict[str, int]:
    total = await session.scalar(select(func.count()).select_from(BookOrder))
    pending = await session.scalar(select(func.count()).where(BookOrder.status == "pending"))
    processing = await session.scalar(select(func.count()).where(BookOrder.status == "processing"))
    confirmed = await session.scalar(select(func.count()).where(BookOrder.status == "confirmed"))
    delivered = await session.scalar(select(func.count()).where(BookOrder.status == "delivered"))
    rejected = await session.scalar(select(func.count()).where(BookOrder.status == "rejected"))
    return {
        "total": int(total or 0),
        "pending": int(pending or 0),
        "processing": int(processing or 0),
        "confirmed": int(confirmed or 0),
        "delivered": int(delivered or 0),
        "rejected": int(rejected or 0),
    }
