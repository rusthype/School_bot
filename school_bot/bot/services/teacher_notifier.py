"""Send the 24h digest of poll results to the teacher's private chat.

Replaces the previous per-vote notifier (Bosqich 2). Now fires ONCE per
task, 24 hours after the poll lands in the parent group. The user
requested this change to cut down on chat spam: instead of seeing a
fresh card every time a parent votes, the teacher now receives a
single, calm summary the next day.

Two entry points
~~~~~~~~~~~~~~~~

* :func:`schedule_teacher_digest` \u2014 called from ``send_task_poll`` at
  poll-creation time. Sets ``Task.notify_scheduled_at`` and spawns an
  asyncio task that sleeps for the configured delay then calls
  :func:`fire_teacher_digest`.
* :func:`fire_teacher_digest` \u2014 the worker that actually renders and
  sends the message. Idempotent against double-fires: it bails out
  early if ``teacher_notif_message_id`` is already set on the task.

The startup recovery path in ``main.start_pending_notifications`` also
calls :func:`schedule_teacher_digest` (with ``delay_seconds`` computed
from the existing ``notify_scheduled_at``) so a bot restart within the
24h window doesn't drop the pending digest.

Anti-spam
~~~~~~~~~

Errors are caught and logged. The digest is best-effort: if Telegram is
down at fire-time, the task simply doesn't get notified. We deliberately
do NOT retry \u2014 the alternative (a retry queue) is more complexity than
this is worth, and the teacher can always run /poll_voters to see the
results on demand.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from school_bot.bot.services.logger_service import get_logger
from school_bot.database.models import PollVote, Profile, Task, User

logger = get_logger(__name__)

# How long to wait before sending the digest. Defaults to 24 hours but
# can be overridden via the TEACHER_NOTIFY_DELAY_HOURS env var \u2014 useful
# for setting a 5-minute window during sinov / staging without code
# changes. The value is read at module import time; bot restart is
# required to pick up a change.
_DEFAULT_DELAY_HOURS = 24
try:
    TEACHER_NOTIFY_DELAY_HOURS = float(
        os.getenv("TEACHER_NOTIFY_DELAY_HOURS", str(_DEFAULT_DELAY_HOURS))
    )
    if TEACHER_NOTIFY_DELAY_HOURS < 0:
        raise ValueError("must be >= 0")
except (TypeError, ValueError) as exc:
    logger.warning(
        "Invalid TEACHER_NOTIFY_DELAY_HOURS=%r (%s) \u2014 falling back to %sh",
        os.getenv("TEACHER_NOTIFY_DELAY_HOURS"),
        exc,
        _DEFAULT_DELAY_HOURS,
    )
    TEACHER_NOTIFY_DELAY_HOURS = float(_DEFAULT_DELAY_HOURS)

TEACHER_NOTIFY_DELAY_SECONDS = TEACHER_NOTIFY_DELAY_HOURS * 3600

# Star labels for each option index, mirroring the UI on the panel side.
# Index 0 (best) \u2192 4 stars, index 3 (worst) \u2192 1 star. Out-of-range
# option ids fall back to a generic bullet so a malformed vote can't
# crash the renderer.
_OPTION_LABELS = [
    ("\u2b50\u2b50\u2b50\u2b50", "Juda yaxshi tushundi"),
    ("\u2b50\u2b50\u2b50", "Yaxshi tushundi"),
    ("\u2b50\u2b50", "Oz tushundi"),
    ("\u2b50", "Umuman tushunmadi"),
]


def _voter_display(profile: Profile | None, user: User) -> str:
    """Human-readable label for a single voter row.

    Priority order:
      1. ``Profile.first_name [last_name]`` if a profile exists
      2. ``BotUser.full_name`` (Telegram first/last name on registration)
      3. ``@username`` if available
      4. fallback: ``BotUser tg=<id>`` so we never render an empty bullet

    Telegram username (``@handle``) is appended in parentheses when
    available \u2014 handy for teachers who recognise parents by handle but
    not by their official name.
    """
    label_parts: list[str] = []
    if profile is not None:
        full = " ".join(
            part for part in (profile.first_name, profile.last_name) if part
        ).strip()
        if full:
            label_parts.append(full)
    if not label_parts and user.full_name:
        label_parts.append(user.full_name.strip())
    if not label_parts and user.username:
        return f"@{user.username}"
    if not label_parts:
        return f"BotUser tg={user.telegram_id}"

    label = label_parts[0]
    if user.username:
        label = f"{label} (@{user.username})"
    return label


def _format_digest_card(
    task: Task,
    votes: Sequence[PollVote],
    voter_lookup: dict[int, tuple[User, Profile | None]],
) -> str:
    """Build the teacher-facing HTML message body for the 24h digest.

    Same layout as the legacy per-vote card but with a 24h-summary
    header so the teacher knows this is the final tally, not a live
    snapshot. Empty vote-options still get a header so a bucket with
    no voters reads as "0 voted for X" rather than disappearing.

    Special case: zero total votes. The user explicitly asked for a
    confirmation message even when nobody participated \u2014 it tells
    them the poll did go out and lets them decide whether to re-prompt
    parents. We render a short, polite "no votes" notice instead of
    the full leaderboard in that case.
    """
    import html as _html

    safe_topic = _html.escape(task.topic or "")

    if len(votes) == 0:
        # Zero-vote case \u2014 keep it short. The teacher only needs to
        # know the poll completed without participation; rendering four
        # empty option buckets would just be noise.
        return (
            f"\U0001f4ca <b>Topshiriq:</b> {safe_topic}\n"
            f"\u23f1 <b>24 soatlik so'rov yakuni</b>\n\n"
            f"\u26a0\ufe0f <i>Hech kim ovoz bermadi.</i>\n\n"
            f"Ota-onalarga qaytadan eslatma yuborishni o'ylab ko'ring."
        )

    # Group voters by option_id. Preserve the chronological order
    # within each group so a newly-added voter shows up at the bottom
    # of their bucket, matching how Telegram comments appear elsewhere
    # in the chat.
    by_option: dict[int, list[PollVote]] = {0: [], 1: [], 2: [], 3: []}
    for vote in votes:
        by_option.setdefault(vote.option_id, []).append(vote)

    lines: list[str] = []
    lines.append(f"\U0001f4ca <b>Topshiriq:</b> {safe_topic}")
    lines.append(f"\u23f1 <b>24 soatlik so'rov yakuni</b>")
    lines.append("")

    total_votes = len(votes)
    for option_id in (0, 1, 2, 3):
        option_votes = by_option.get(option_id, [])
        if option_id < len(_OPTION_LABELS):
            stars, label = _OPTION_LABELS[option_id]
        else:
            stars, label = "\u2022", f"Variant {option_id + 1}"
        count = len(option_votes)
        lines.append(f"{stars} <b>{label}</b> ({count}):")
        if option_votes:
            for v in option_votes:
                user, profile = voter_lookup.get(v.user_id, (None, None))
                if user is None:
                    lines.append(f"  \u2022 <i>BotUser tg=?</i>")
                    continue
                lines.append(f"  \u2022 {_html.escape(_voter_display(profile, user))}")
        else:
            lines.append(f"  <i>(0 ovoz)</i>")
        lines.append("")

    lines.append(f"\U0001f4ca <b>Jami ovoz:</b> {total_votes}")
    return "\n".join(lines)


async def fire_teacher_digest(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
) -> None:
    """Render and send the 24h digest for a single task.

    Idempotent: if ``Task.teacher_notif_message_id`` is already set, we
    silently return without sending. That guards against:

      * A startup-recovery scheduler racing with a still-running
        in-flight ``schedule_teacher_digest`` task from the previous
        boot (shouldn't happen but cheap to guard).
      * Duplicate calls from human error (e.g. someone re-running the
        recovery scan manually).

    Uses its own session (not a passed-in one) because the firing
    coroutine outlives the request that scheduled it. We accept a
    session_factory and open/close a session here so the lifetime is
    tied to this work unit.
    """
    try:
        async with session_factory() as session:
            # Fetch the task with its poll_votes and teacher eagerly
            # loaded so the renderer doesn't trigger lazy-load round
            # trips on a session that's about to close.
            result = await session.execute(
                select(Task)
                .options(
                    selectinload(Task.poll_votes),
                    selectinload(Task.teacher),
                )
                .where(Task.id == task_id)
            )
            task = result.scalar_one_or_none()
            if task is None:
                logger.info("digest skip: task not found", extra={"task_id": task_id})
                return
            if task.teacher_notif_message_id is not None:
                logger.info(
                    "digest skip: already delivered",
                    extra={
                        "task_id": task_id,
                        "existing_message_id": task.teacher_notif_message_id,
                    },
                )
                return
            if task.teacher is None or task.teacher.telegram_id is None:
                logger.info(
                    "digest skip: teacher missing or no telegram_id",
                    extra={"task_id": task_id},
                )
                return

            teacher_chat_id = task.teacher.telegram_id

            # Pull voter User+Profile rows in a single query so the
            # renderer never hits the DB again.
            voter_ids = {v.user_id for v in task.poll_votes}
            voter_lookup: dict[int, tuple[User, Profile | None]] = {}
            if voter_ids:
                voters_result = await session.execute(
                    select(User)
                    .options(selectinload(User.profile))
                    .where(User.id.in_(voter_ids))
                )
                for u in voters_result.scalars():
                    voter_lookup[u.id] = (u, u.profile)

            text = _format_digest_card(task, task.poll_votes, voter_lookup)

            try:
                sent = await bot.send_message(
                    chat_id=teacher_chat_id,
                    text=text,
                    parse_mode="HTML",
                    disable_notification=False,
                )
            except TelegramAPIError:
                logger.exception(
                    "digest send failed",
                    extra={
                        "task_id": task_id,
                        "teacher_chat_id": teacher_chat_id,
                    },
                )
                return

            # Mark as delivered so a future recovery scan won't re-fire.
            task.teacher_notif_message_id = sent.message_id
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception(
                    "digest persist failed (already sent on Telegram)",
                    extra={
                        "task_id": task_id,
                        "sent_message_id": sent.message_id,
                    },
                )
                return

            logger.info(
                "digest sent",
                extra={
                    "task_id": task_id,
                    "teacher_chat_id": teacher_chat_id,
                    "sent_message_id": sent.message_id,
                    "vote_count": len(task.poll_votes),
                },
            )
    except Exception:
        logger.exception(
            "fire_teacher_digest failed \u2014 non-fatal",
            extra={"task_id": task_id},
        )


def schedule_teacher_digest(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    task_id: int,
    delay_seconds: float | None = None,
) -> asyncio.Task:
    """Schedule a single fire-once digest for ``task_id``.

    Uses ``asyncio.create_task`` + ``asyncio.sleep``. Caller doesn't
    need to await the returned Task \u2014 it runs in the background. The
    bot's main event loop owns the scheduled coroutine so it keeps
    running for the full delay.

    ``delay_seconds`` defaults to ``TEACHER_NOTIFY_DELAY_SECONDS``
    (24h or the env-overridden value). The startup recovery path
    passes a smaller computed value when restarting an in-flight
    timer, or 0 for tasks whose deadline has already passed.

    A non-positive ``delay_seconds`` fires the digest immediately
    (still on a fresh asyncio task so we don't block the caller).
    """
    delay = (
        TEACHER_NOTIFY_DELAY_SECONDS if delay_seconds is None else max(0.0, delay_seconds)
    )

    async def _runner() -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await fire_teacher_digest(bot, session_factory, task_id)
        except asyncio.CancelledError:
            # Bot shutdown \u2014 the task will be picked up on next start
            # by the recovery scan (its notify_scheduled_at survives in
            # the DB and teacher_notif_message_id is still NULL).
            logger.info(
                "digest scheduler cancelled (will recover on next startup)",
                extra={"task_id": task_id},
            )
            raise
        except Exception:
            logger.exception(
                "digest scheduler crashed",
                extra={"task_id": task_id},
            )

    return asyncio.create_task(_runner(), name=f"teacher_digest_{task_id}")


async def schedule_pending_digests(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Recover digests that survived a bot restart.

    Scans every ``Task`` where ``notify_scheduled_at`` is set AND
    ``teacher_notif_message_id`` is still NULL (i.e. scheduled but
    not yet delivered). For each:

      * If the deadline has already passed, fires the digest
        immediately (delay_seconds=0).
      * Otherwise, schedules a fresh asyncio sleep for the remaining
        time.

    Returns the number of tasks (re-)scheduled. Called once on bot
    startup from ``main.on_startup``. Safe to call multiple times \u2014
    the per-task ``fire_teacher_digest`` is idempotent against
    double-fires.
    """
    scheduled = 0
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(Task).where(
                    Task.notify_scheduled_at.is_not(None),
                    Task.teacher_notif_message_id.is_(None),
                )
            )
            tasks = list(result.scalars())

        now = datetime.now(timezone.utc)
        for task in tasks:
            deadline = task.notify_scheduled_at
            if deadline is None:
                continue
            # The DB column is timezone-aware (DateTime(timezone=True)),
            # but a naive datetime can sneak through if a future caller
            # writes one. Coerce defensively so the subtraction below
            # never throws TypeError("can't subtract offset-naive...").
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            remaining = (deadline - now).total_seconds()
            schedule_teacher_digest(
                bot,
                session_factory,
                task.id,
                delay_seconds=remaining,
            )
            scheduled += 1
            logger.info(
                "digest rescheduled on startup",
                extra={
                    "task_id": task.id,
                    "remaining_seconds": int(remaining),
                    "fires_immediately": remaining <= 0,
                },
            )
        if scheduled:
            logger.info("digest startup recovery: %s tasks rescheduled", scheduled)
    except Exception:
        logger.exception("schedule_pending_digests failed \u2014 non-fatal")
    return scheduled
