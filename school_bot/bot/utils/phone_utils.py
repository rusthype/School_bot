"""Phone number normalization utilities.

Canonical format used across Alochi + School_bot: '+998XXXXXXXXX' (13 chars).

Alochi panel stores phones as '+998XXXXXXXXX' (with leading +).
Telegram Contact.phone_number comes back as '998XXXXXXXXX' (no +) or
sometimes '+998XXXXXXXXX' depending on client. Users may also type
with spaces / dashes / parentheses, or only provide the 9-digit
subscriber portion.

normalize_phone() unifies all of those to the canonical form so the
two databases can be matched reliably. Returns None for anything
that is not a plausible Uzbek number.
"""
from __future__ import annotations

__all__ = ["normalize_phone"]


def normalize_phone(raw: str | None) -> str | None:
    """Normalize an Uzbek phone number to '+998XXXXXXXXX'.

    Accepts:
      - '+998XXXXXXXXX'  (already canonical)
      - '998XXXXXXXXX'   (Telegram Contact style, no +)
      - 'XXXXXXXXX'      (9-digit subscriber part)
      - Any of the above with spaces, dashes, parentheses, or
        leading '00' international prefix.

    Returns None when the input is empty, non-numeric, or does not
    match one of the supported lengths.
    """
    if not raw:
        return None
    cleaned = "".join(c for c in str(raw) if c.isdigit())
    if not cleaned:
        return None
    # Handle '00998...' double-zero international prefix.
    if cleaned.startswith("00") and len(cleaned) == 14:
        cleaned = cleaned[2:]
    if cleaned.startswith("998") and len(cleaned) == 12:
        return f"+{cleaned}"
    if len(cleaned) == 9:
        return f"+998{cleaned}"
    return None
