from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message
import html

from aiogram.enums import PollType, ParseMode


async def send_task_poll(
        bot: Bot,
        group_chat_id: int,
        topic: str,
        description: str,
        poll_options: list[str],
        notes: str | None = None,
) -> Message:
    # Mavzu va vazifani xabar sifatida yuboramiz
    safe_topic = html.escape(topic)
    safe_description = html.escape(description)
    safe_notes = html.escape(notes) if notes else None

    task_message = (
        f"📌 Bugungi mavzu:\n"
        f"➤ {safe_topic}\n\n"
        f"🏠 Uyga vazifa:\n"
        f"➤ {safe_description}"
    )
    if safe_notes:
        task_message += f"\n\n<b>📝 Izoh: {safe_notes}</b>"
    await bot.send_message(chat_id=group_chat_id, text=task_message, parse_mode=ParseMode.HTML)

    # Poll savoli - topic bilan
    poll_question = (
        f"Mavzu: {topic}.\n"
        f"Farzandingiz bu mavzuni\n"
        f"qanday darajada tushundi deb\n"
        f"o'ylaysiz? Iltimos,\n"
        f"quyidagilardan birini tanlang:"
    )

    # Poll yuboramiz
    poll = await bot.send_poll(
        chat_id=group_chat_id,
        question=poll_question,
        options=poll_options,
        is_anonymous=False,
        type=PollType.REGULAR,
        allows_multiple_answers=False,
    )
    return poll
