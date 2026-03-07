import re


def parse_telegram_input(text: str) -> tuple[str, str | int] | None:
    """
    Telegram username yoki ID ni parse qilish
    Returns: (type, value)  # type: "id" yoki "username"
    """
    if not text:
        return None

    text = text.strip()

    if text.startswith('@'):
        username = text[1:]
        if re.match(r'^[a-zA-Z0-9_]{5,32}$', username):
            return ("username", username)
        return None

    if text.isdigit():
        return ("id", int(text))

    if re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', text):
        return ("username", text)

    return None
