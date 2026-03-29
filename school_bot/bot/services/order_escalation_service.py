from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from school_bot.bot.services.logger_service import get_logger
from school_bot.database.models import BookOrder, BookOrderItem, Profile, School, User, UserRole

logger = get_logger(__name__)


async def _build_order_items(session: AsyncSession, order: BookOrder) -> list[str]:
    items: list[str] = []
    for item in order.items:
        title = None
        if item.book:
            title = item.book.title
        if not title:
            result = await session.execute(
                select(BookOrderItem)
                .where(BookOrderItem.id == item.id)
                .options(selectinload(BookOrderItem.book))
            )
            refreshed = result.scalar_one_or_none()
            if refreshed and refreshed.book:
                title = refreshed.book.title
        if not title:
            title = f"ID: {item.book_id}"
        items.append(f"   • {escape(title)} - {item.quantity} dona")
    return items


async def _resolve_teacher_context(session: AsyncSession, teacher: User | None) -> tuple[str, str, str]:
    if not teacher:
        return "O'qituvchi: Noma'lum", "Noma'lum", "-"

    if teacher.role == UserRole.superadmin:
        display = f"👑 SUPERADMIN: {escape(teacher.full_name or str(teacher.telegram_id))}"
        if teacher.username:
            display += f" (@{escape(teacher.username)})"
        return display, "Superadmin", "-"

    display = f"👨‍🏫 O'qituvchi: {escape(teacher.full_name or str(teacher.telegram_id))}"
    if teacher.username:
        display += f" (@{escape(teacher.username)})"

    school_name = "Noma'lum"
    class_name = "-"
    profile_result = await session.execute(select(Profile).where(Profile.user_id == teacher.id))
    profile = profile_result.scalar_one_or_none()
    if profile:
        if profile.school_id:
            school = await session.get(School, profile.school_id)
            if school:
                school_name = school.name
        if profile.assigned_groups:
            class_name = ", ".join(profile.assigned_groups)

    return display, school_name, class_name


async def notify_superadmins_about_overdue(
    bot: Bot,
    session: AsyncSession,
    order: BookOrder,
    now: datetime,
) -> None:
    teacher = order.teacher
    teacher_display, school_name, class_name = await _resolve_teacher_context(session, teacher)
    items_text = await _build_order_items(session, order)

    delivery_deadline = order.delivery_deadline
    if not delivery_deadline:
        base_time = order.created_at or now
        delivery_deadline = base_time + timedelta(days=7)

    days_overdue = max((now.date() - delivery_deadline.date()).days, 0)
    created_str = (order.created_at or now).strftime("%d.%m.%Y")
    deadline_str = delivery_deadline.strftime("%d.%m.%Y")

    librarian_link = ""
    if order.librarian_id:
        librarian = await session.get(User, order.librarian_id)
        if librarian:
            librarian_link = (
                f"\n<a href=\"tg://user?id={librarian.telegram_id}\">"
                "📞 Librarian bilan bog'lanish</a>"
            )

    message = (
        "⚠️ <b>MUHIM: MUDDATI O'TGAN BUYURTMA</b>\n\n"
        f"🆔 Buyurtma ID: {order.id}\n"
        f"{teacher_display}\n"
        f"🏫 Maktab: {escape(school_name)}\n"
        f"📚 Sinf: {escape(class_name)}\n"
        "📖 Kitoblar:\n"
        + "\n".join(items_text)
        + "\n\n"
        f"📅 Buyurtma qilingan: {created_str}\n"
        f"📅 Yetkazish muddati: {deadline_str}\n"
        f"⚠️ Kechikkan: {days_overdue} kun\n\n"
        "❌ Hali yetkazilmagan! Superadmin e'tiboriga!"
        + librarian_link
    )

    result = await session.execute(select(User).where(User.role == UserRole.superadmin))
    superadmins = result.scalars().all()
    for admin in superadmins:
        try:
            await bot.send_message(chat_id=admin.telegram_id, text=message, disable_web_page_preview=True)
        except Exception:
            logger.error("Overdue order notification failed", exc_info=True)


async def start_overdue_order_watch(
    bot: Bot,
    session_factory,
    interval_seconds: int = 3600,
    default_deadline_days: int = 7,
) -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            default_cutoff = now - timedelta(days=default_deadline_days)
            async with session_factory() as session:
                result = await session.execute(
                    select(BookOrder)
                    .where(
                        BookOrder.status.in_(["pending", "processing", "confirmed"]),
                        BookOrder.escalated.is_(False),
                        or_(
                            and_(
                                BookOrder.delivery_deadline.is_not(None),
                                BookOrder.delivery_deadline < now,
                            ),
                            and_(
                                BookOrder.delivery_deadline.is_(None),
                                BookOrder.created_at < default_cutoff,
                            ),
                        ),
                    )
                    .options(
                        selectinload(BookOrder.items).selectinload(BookOrderItem.book),
                        selectinload(BookOrder.teacher),
                    )
                )
                overdue_orders = result.scalars().all()
                for order in overdue_orders:
                    if not order.delivery_deadline and order.created_at:
                        order.delivery_deadline = order.created_at + timedelta(days=default_deadline_days)
                    await notify_superadmins_about_overdue(bot, session, order, now)
                    order.escalated = True
                if overdue_orders:
                    await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Overdue order check failed", exc_info=True)

        await asyncio.sleep(interval_seconds)
