from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message
from aiogram.enums import PollType


async def send_task_poll(
        bot: Bot,
        group_chat_id: int,
        topic: str,
        description: str,
        poll_options: list[str],
) -> Message:
    # Mavzu va vazifani xabar sifatida yuboramiz
    task_message = (
        f"📌 Bugungi mavzu:\n"
        f"➤ {topic}\n\n"
        f"🏠 Uyga vazifa:\n"
        f"➤ {description}"
    )
    await bot.send_message(chat_id=group_chat_id, text=task_message)

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
