"""HTTP client for the Alochi panel's internal bot -> sync endpoints.

Pairs with ``apps.teachers.views_internal.BotSyncTeacherView`` on the
Alochi side. When a teacher gets tasdiqlangan in the bot's superadmin
flow, we POST a small payload describing them to the panel and the
panel:

  1. Creates (or links) an ``apps.teachers.Teacher`` row.
  2. Provisions an ``apps.users.User`` so the teacher can log in.
  3. Returns the generated ``username`` + plain-text ``password``.

This module wraps that round trip behind a single coroutine
(``sync_teacher_to_alochi``) and a dataclass (``AlochiSyncResult``) so
the calling handler doesn't deal with HTTP details. Errors are
caught and logged; the sync is best-effort — a failed sync MUST NOT
block the bot's own approval flow.

Disabled by default
~~~~~~~~~~~~~~~~~~~

If ``ALOCHI_SYNC_URL`` or ``ALOCHI_SYNC_TOKEN`` is empty (the default),
``sync_teacher_to_alochi`` returns ``None`` immediately. That keeps
local development frictionless — you only configure the sync when
you're testing the bridge end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import aiohttp

from school_bot.bot.config import Settings
from school_bot.bot.services.logger_service import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AlochiSyncResult:
    """Successful response from the panel's bot-sync endpoint."""
    teacher_id: str
    username: str
    # Plain-text password — only set on first sync (when a fresh User was
    # provisioned). On re-link the panel returns ``None`` and we should
    # not attempt to DM credentials. Callers MUST handle the ``None`` case.
    password: Optional[str]
    created: bool
    login_url: str


async def sync_teacher_to_alochi(
    *,
    bot_user_id: int,
    name: str,
    phone: str,
    school_id: Optional[str] = None,
    subjects: Optional[list[str]] = None,
    timeout_seconds: float = 10.0,
) -> Optional[AlochiSyncResult]:
    """POST the teacher payload to the Alochi panel and return parsed creds.

    Returns ``None`` when:

      * The sync is not configured (empty URL or token in env).
      * The HTTP call fails (timeout, 5xx, network error).
      * The panel rejects the payload (4xx).

    On success, returns an :class:`AlochiSyncResult` with the
    panel-provisioned credentials. The caller decides whether to DM
    them based on ``result.password is not None``.
    """
    settings = Settings()
    base_url = (settings.alochi_sync_url or '').rstrip('/')
    token = settings.alochi_sync_token or ''

    if not base_url or not token:
        logger.info(
            'alochi_sync.disabled — ALOCHI_SYNC_URL or ALOCHI_SYNC_TOKEN '
            'not configured; skipping panel sync',
            extra={'bot_user_id': bot_user_id},
        )
        return None

    url = f'{base_url}/sync-teacher/'
    payload = {
        'bot_user_id': bot_user_id,
        'name': name,
        'phone': phone or '',
        'school_id': school_id,
        'subjects': list(subjects or []),
    }
    headers = {
        'X-Bot-Sync-Token': token,
        'Content-Type': 'application/json',
    }

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.error(
                        'alochi_sync.http_error status=%s body=%s',
                        resp.status,
                        text[:300],
                        extra={'bot_user_id': bot_user_id, 'url': url},
                    )
                    return None
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.error(
            'alochi_sync.network_error %s',
            exc,
            extra={'bot_user_id': bot_user_id, 'url': url},
        )
        return None
    except Exception:
        logger.exception(
            'alochi_sync.unexpected_error',
            extra={'bot_user_id': bot_user_id, 'url': url},
        )
        return None

    # Parse the response defensively — the panel contract is stable but
    # the bot must never crash on a malformed body.
    try:
        result = AlochiSyncResult(
            teacher_id=str(data['teacher_id']),
            username=str(data['username']),
            password=data.get('password'),  # may legitimately be None
            created=bool(data.get('created', False)),
            login_url=str(data.get('login_url', '')),
        )
    except (KeyError, TypeError) as exc:
        logger.error(
            'alochi_sync.bad_response %s body=%s',
            exc,
            data,
            extra={'bot_user_id': bot_user_id},
        )
        return None

    logger.info(
        'alochi_sync.ok teacher_id=%s username=%s created=%s password_returned=%s',
        result.teacher_id,
        result.username,
        result.created,
        result.password is not None,
        extra={'bot_user_id': bot_user_id},
    )
    return result


def format_credentials_message(result: AlochiSyncResult, full_name: str) -> str:
    """Build the HTML DM body the bot sends to the freshly approved teacher.

    Three branches:

      * ``result.created and result.password`` — brand-new teacher,
        send full credentials.
      * ``not result.created and result.password`` — existing teacher,
        password was rotated (rare — only when the row had an unusable
        password). Send credentials with a "Sizning parolingiz yangilandi"
        note.
      * ``result.password is None`` — already provisioned, no creds to
        share. Send a short "Siz allaqachon panelda ro'yxatdan o'tgansiz"
        note with the login URL.
    """
    import html as _html

    safe_name = _html.escape(full_name or "O'qituvchi")

    if result.password is None:
        return (
            f"✅ <b>Tasdiqlandi!</b>\n\n"
            f"Hurmatli {safe_name}, siz allaqachon A'lochi panelida "
            f"ro'yxatdan o'tgansiz.\n\n"
            f"🔗 Kirish: {result.login_url}"
        )

    cred_label = (
        "Yangi login ma'lumotlaringiz:" if not result.created
        else "Login ma'lumotlaringiz:"
    )
    safe_username = _html.escape(result.username)
    safe_password = _html.escape(result.password)
    return (
        f"✅ <b>Tasdiqlandi!</b>\n\n"
        f"Hurmatli {safe_name}, siz A'lochi platformasida o'qituvchi "
        f"sifatida tasdiqlandingiz.\n\n"
        f"📝 <b>{cred_label}</b>\n"
        f"👤 Username: <code>{safe_username}</code>\n"
        f"🔑 Parol: <code>{safe_password}</code>\n\n"
        f"🔗 Kirish: {result.login_url}\n\n"
        f"⚠️ <i>Parolingizni xavfsiz joyga saqlang va birinchi "
        f"kirishdan keyin o'zgartiring.</i>"
    )


async def revoke_teacher_in_alochi(
    *,
    bot_user_id: int,
    timeout_seconds: float = 10.0,
) -> bool:
    """POST to the panel's /revoke-teacher/ endpoint to soft-delete the Alochi Teacher.

    Returns True when the panel confirmed a revocation (``revoked: true``),
    False on any failure path: not configured, network error, no Alochi
    match, already revoked, or unexpected response. The bot caller treats
    this as best-effort — the bot's own profile state is the source of
    truth and gets revoked locally before this function is called.
    """
    settings = Settings()
    base_url = (settings.alochi_sync_url or '').rstrip('/')
    token = settings.alochi_sync_token or ''

    if not base_url or not token:
        logger.info(
            'alochi_revoke.disabled — ALOCHI_SYNC_URL or ALOCHI_SYNC_TOKEN '
            'not configured; skipping panel revoke',
            extra={'bot_user_id': bot_user_id},
        )
        return False

    url = f'{base_url}/revoke-teacher/'
    headers = {
        'X-Bot-Sync-Token': token,
        'Content-Type': 'application/json',
    }
    payload = {'bot_user_id': bot_user_id}

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    logger.error(
                        'alochi_revoke.http_error status=%s body=%s',
                        resp.status,
                        text[:300],
                        extra={'bot_user_id': bot_user_id, 'url': url},
                    )
                    return False
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        logger.error(
            'alochi_revoke.network_error %s',
            exc,
            extra={'bot_user_id': bot_user_id, 'url': url},
        )
        return False
    except Exception:
        logger.exception(
            'alochi_revoke.unexpected_error',
            extra={'bot_user_id': bot_user_id, 'url': url},
        )
        return False

    revoked = bool(data.get('revoked', False))
    logger.info(
        'alochi_revoke.ok teacher_id=%s revoked=%s reason=%s',
        data.get('teacher_id'),
        revoked,
        data.get('reason', ''),
        extra={'bot_user_id': bot_user_id},
    )
    return revoked
