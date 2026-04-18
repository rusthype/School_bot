from __future__ import annotations

from aiogram import Bot

from school_bot.bot.services.logger_service import get_logger

logger = get_logger(__name__)

_SUBSCRIBED_STATUSES = {"member", "administrator", "creator"}


async def check_subscription(bot: Bot, user_id: int, channel: str) -> bool:
    """
    Return True if user_id is a current member of the given channel.

    - `channel` can be a @username or a numeric chat_id (as str or int).
    - If channel is falsy, subscription is treated as OK (feature disabled).
    - All Telegram / network errors are swallowed and treated as "not subscribed"
      so that bot misconfiguration does not hard-block the user without a trace.
    """
    if not channel:
        return True

    try:
        # aiogram accepts both @username and numeric chat_id in get_chat_member.
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
    except Exception as exc:
        # Common causes:
        #   - Bot is not admin in channel
        #   - Bot has never seen this user (for private channels)
        #   - Wrong channel username
        # We log once per call but do not raise — simply report "not subscribed".
        logger.warning(
            "check_subscription failed for user_id=%s channel=%s: %s",
            user_id,
            channel,
            exc,
        )
        return False

    status = getattr(member, "status", None)
    status_value = getattr(status, "value", status)  # aiogram enum or str
    return status_value in _SUBSCRIBED_STATUSES
