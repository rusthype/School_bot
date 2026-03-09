from __future__ import annotations

from aiogram.types import Message

MAX_TG_MESSAGE = 4000


def split_message(text: str, limit: int = MAX_TG_MESSAGE) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        line_len = len(line) + 1
        if size + line_len > limit and current:
            chunks.append("\n".join(current))
            current = [line]
            size = line_len
        else:
            current.append(line)
            size += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


async def send_chunked_message(
    message: Message,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        if i == 0:
            await message.answer(chunk, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await message.answer(chunk, parse_mode=parse_mode)


async def safe_edit_or_send(
    message: Message,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    if len(text) <= MAX_TG_MESSAGE:
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except Exception:
            pass
    await send_chunked_message(message, text, reply_markup=reply_markup, parse_mode=parse_mode)


async def send_chunked_to_chat(
    bot,
    chat_id: int,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        if i == 0:
            await bot.send_message(chat_id=chat_id, text=chunk, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)
