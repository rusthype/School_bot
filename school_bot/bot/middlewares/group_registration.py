from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.database.models import Group

logger = logging.getLogger(__name__)

# Process-local cache of chat_ids already confirmed to be in bot_groups.
# Trade-off: each bot pod restart re-queries the DB once per known group
# chat on the next observed message.  That is at most a handful of rows
# and is the same pattern used by RateLimitMiddleware and
# GroupAdminGuardMiddleware in this codebase.  A Redis-backed cache
# would survive restarts, but Redis is only wired into aiogram's FSM
# storage here — it is not injected into middleware data — so a
# process-local set is the right choice to stay consistent.
_known_chat_ids: set[int] = set()


class GroupRegistrationMiddleware(BaseMiddleware):
    """Lazy-register group/supergroup chats on the first observed message.

    Safety net for groups where on_bot_added_to_group fired but failed to
    persist a row (e.g. during the UUID type-mismatch window of
    2026-05-03, commit bd5e5d8).  After deploy, the first message any
    participant sends in such a group creates a 'pending' bot_groups row,
    ready for admin approval via the existing flow.

    Placement in the middleware chain (main.py):
        dp.update : RateLimitMiddleware  <- not relevant, registered on dp.message
        dp.update : GroupAdminGuardMiddleware
        dp.update : DbSessionMiddleware        <- injects data["session"]
        dp.update : UserContextMiddleware
        dp.update : MenuGuardMiddleware
        dp.message: RateLimitMiddleware
        dp.message: GroupRegistrationMiddleware (NEW)  <- needs session, before UserContext

    aiogram applies dp.update middlewares first, then dp.message ones, so
    by the time this middleware runs DbSessionMiddleware has already placed
    the AsyncSession into data["session"].

    This middleware is intentionally a no-op for:
      - private chats and channels (chat.type check)
      - chats already in the cache (fast-path, no DB hit)
      - any event that is not a Message (guard on isinstance check)

    Failure policy: any exception is swallowed, rollback is attempted,
    and handler dispatch continues normally.  This safety net must never
    break message handling.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message: Message | None = event if isinstance(event, Message) else None
        if message is None or message.chat is None:
            return await handler(event, data)

        chat = message.chat
        if chat.type not in {"group", "supergroup"}:
            return await handler(event, data)

        if chat.id in _known_chat_ids:
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        if session is None:
            # DbSessionMiddleware did not run (should not happen in normal
            # operation); skip registration rather than crash.
            return await handler(event, data)

        try:
            result = await session.execute(
                select(Group.id).where(Group.chat_id == chat.id)
            )
            if result.scalar_one_or_none() is None:
                name = chat.title or f"Unknown group {chat.id}"
                try:
                    session.add(
                        Group(
                            chat_id=chat.id,
                            name=name,
                            status="pending",
                            alochi_group_id=None,
                            school_id=None,
                            invite_link=None,
                        )
                    )
                    await session.commit()
                    logger.info(
                        "Auto-registered group from message: chat_id=%s title=%r",
                        chat.id,
                        chat.title,
                    )
                except IntegrityError:
                    # Race condition: another worker (or the MyChatMember handler)
                    # committed the same row between our SELECT and INSERT.
                    # Roll back and continue — the row is in the DB, which is
                    # exactly what we want.
                    await session.rollback()
                    logger.debug(
                        "IntegrityError on auto-register for chat_id=%s — concurrent INSERT, ignoring",
                        chat.id,
                    )
        except Exception:
            logger.exception(
                "Group auto-registration failed for chat_id=%s; continuing dispatch",
                chat.id,
            )
            try:
                await session.rollback()
            except Exception:
                pass

        # Mark as known regardless of whether the INSERT succeeded or raced.
        # On IntegrityError the row already exists; on success we just created it.
        # Either way, future messages from this chat skip the DB entirely.
        _known_chat_ids.add(chat.id)

        return await handler(event, data)
