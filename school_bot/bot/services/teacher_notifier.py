"""Send the LIVE poll-results card to the teacher's private chat.

Triggered after each ``PollVote`` is committed in ``handle_poll_answer``.
Pattern: every new vote DELETES the previous results card and SENDS a
fresh one. The teacher therefore always sees a single up-to-date card
sitting at the top of their chat, with the latest message marker
("New" indicator) drawing their attention to the change.

Why delete + send instead of edit_message_text
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* The teacher explicitly asked for the message to bubble back to the
  top of the chat on each vote — Telegram doesn't surface edits the
  same way it surfaces new messages.
* edit_message_text silently no-ops when the rendered text is identical
  to the current message body; we always have new content (a new voter
  added) but a future case where two voters tie would still need a
  fallback. Delete-and-send sidesteps that edge case entirely.
* deleteMessage on a 48h-old message returns a Bad Request — that's the
  ONE case our error handler tolerates. We log and proceed; a stale
  card is harmless.

Anti-spam
~~~~~~~~~

Fire-and-forget at the call site: any exception is logged and swallowed
so that vote persistence is never blocked by a Telegram rate limit or
the teacher having blocked the bot. The `BotPollVote` insert + commit
has already happened by the time we run, so failures here only affect
notification delivery, not the source of truth.
"""
from __future__ import annotations

from typing import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from school_bot.bot.services.logger_service import get_logger
from school_bot.database.models import PollVote, Profile, Task, User

logger = get_logger(__name__)

# Star labels for each option index, mirroring the UI on the panel side.
# Index 0 (best) → 4 stars, index 3 (worst) → 1 star. Out-of-range
# option ids fall back to a generic bullet so a malformed vote can't
# crash the renderer.
_OPTION_LABELS = [
    ("⭐⭐⭐⭐", "Juda yaxshi tushundi"),
    ("⭐⭐⭐", "Yaxshi tushundi"),
    ("⭐⭐", "Oz tushundi"),
    ("⭐", "Umuman tushunmadi"),
]


def _voter_display(profile: Profile | None, user: User) -> str:
    """Human-readable label for a single voter row.

    Priority order:
      1. ``Profile.first_name [last_name]`` if a profile exists
      2. ``BotUser.full_name`` (Telegram first/last name on registration)
      3. ``@username`` if available
      4. fallback: ``BotUser tg=<id>`` so we never render an empty bullet

    Telegram username (``@handle``) is appended in parentheses when
    available — handy for teachers who recognise parents by handle but
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
        # No registered name; fall back to the @handle as the primary
        # label rather than synthesising one.
        return f"@{user.username}"
    if not label_parts:
        return f"BotUser tg={user.telegram_id}"

    label = label_parts[0]
    if user.username:
        label = f"{label} (@{user.username})"
    return label


def _format_results_card(
    task: Task,
    votes: Sequence[PollVote],
    voter_lookup: dict[int, tuple[User, Profile | None]],
) -> str:
    """Build the teacher-facing HTML message body.

    `voter_lookup` maps ``BotUser.id`` → (User, Profile?) so the renderer
    can produce a label per voter without further DB calls. Votes are
    grouped by ``option_id`` and rendered under their star label; empty
    options still get a header so the teacher can see "0 voted for X".
    """
    # Group voters by option_id. Preserve the chronological order within
    # each group so a newly-added voter shows up at the bottom of their
    # bucket, matching how Telegram comments appear elsewhere in the chat.
    by_option: dict[int, list[PollVote]] = {0: [], 1: [], 2: [], 3: []}
    for vote in votes:
        by_option.setdefault(vote.option_id, []).append(vote)

    lines: list[str] = []
    # Escape topic content because aiogram parse_mode='HTML' will treat
    # any < or & as markup. ``html.escape`` is intentionally NOT imported
    # at module level to keep this file slim — inline import is fine.
    import html as _html

    safe_topic = _html.escape(task.topic or "")
    lines.append(f"📊 <b>Topshiriq:</b> {safe_topic}")
    lines.append("👨‍🏫 <b>So'rov natijalari:</b>")
    lines.append("")

    total_votes = len(votes)
    for option_id in (0, 1, 2, 3):
        option_votes = by_option.get(option_id, [])
        if option_id < len(_OPTION_LABELS):
            stars, label = _OPTION_LABELS[option_id]
        else:
            stars, label = "•", f"Variant {option_id + 1}"
        count = len(option_votes)
        lines.append(f"{stars} <b>{label}</b> ({count}):")
        if option_votes:
            for v in option_votes:
                user, profile = voter_lookup.get(v.user_id, (None, None))
                if user is None:
                    # Lookup miss — shouldn't happen because we SELECT
                    # all voters before rendering, but render a stub
                    # instead of crashing.
                    lines.append(f"  • <i>BotUser tg=?</i>")
                    continue
                lines.append(f"  • {_html.escape(_voter_display(profile, user))}")
        else:
            lines.append("  <i>(hali ovoz yo'q)</i>")
        lines.append("")

    lines.append(f"📊 <b>Jami ovoz:</b> {total_votes}")
    return "\n".join(lines)


async def notify_teacher_of_vote(
    bot: Bot,
    session: AsyncSession,
    task: Task,
) -> None:
    """Refresh the teacher's results card after a new vote.

    Caller passes the parent ``Task``; we fetch the live state of every
    vote on it (via ``selectinload`` so the relationship is hydrated),
    plus the teacher's BotUser to learn their telegram_id. We then:

      1. Delete the previous card (``task.teacher_notif_message_id``) if
         one exists. ``TelegramBadRequest`` from a >48h-old message is
         tolerated; any other delete failure is logged and the new card
         is sent anyway — a duplicate beats a missing card.
      2. Send the new card to the teacher's private chat.
      3. Persist the new ``message.message_id`` on the ``Task`` row so
         the next vote can find the card to delete.

    Errors at any step are caught and logged. Vote saving has already
    been committed by the caller; this function is best-effort.
    """
    try:
        # Re-fetch the task with its relationship eagerly loaded so we
        # don't accidentally lazy-load on the active session. Also pulls
        # the teacher BotUser so we have telegram_id without a second
        # round-trip.
        result = await session.execute(
            select(Task)
            .options(
                selectinload(Task.poll_votes),
                selectinload(Task.teacher),
            )
            .where(Task.id == task.id)
        )
        task = result.scalar_one_or_none()
        if task is None or task.teacher is None or task.teacher.telegram_id is None:
            logger.info(
                "Skipping teacher notification: task or teacher missing",
                extra={"task_id": getattr(task, "id", None)},
            )
            return

        teacher_chat_id = task.teacher.telegram_id

        # Pull voter User+Profile rows in a single query so the card
        # renderer never hits the DB again. ``Profile`` is left-joined
        # so a vote from an unregistered user still renders.
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

        text = _format_results_card(task, task.poll_votes, voter_lookup)

        # ── Step 1: delete previous card if any ───────────────────────
        old_message_id = task.teacher_notif_message_id
        if old_message_id is not None:
            try:
                await bot.delete_message(
                    chat_id=teacher_chat_id,
                    message_id=old_message_id,
                )
            except TelegramBadRequest as exc:
                # Message too old (>48h), already deleted, or wrong
                # chat. None of these are fatal — the new card lands
                # below the stale one and the teacher sees both for
                # one cycle, then only the new one going forward.
                logger.info(
                    "Could not delete old teacher card (continuing)",
                    extra={
                        "task_id": task.id,
                        "old_message_id": old_message_id,
                        "reason": str(exc),
                    },
                )
            except TelegramAPIError:
                logger.exception(
                    "TelegramAPIError deleting old teacher card",
                    extra={
                        "task_id": task.id,
                        "old_message_id": old_message_id,
                    },
                )

        # ── Step 2: send the new card ─────────────────────────────────
        try:
            sent = await bot.send_message(
                chat_id=teacher_chat_id,
                text=text,
                parse_mode="HTML",
                disable_notification=False,  # bubble to top + ping
            )
        except TelegramAPIError:
            # Teacher blocked the bot, chat not found, etc. Log and
            # bail; the vote is already committed.
            logger.exception(
                "Failed to send teacher card",
                extra={"task_id": task.id, "teacher_chat_id": teacher_chat_id},
            )
            return

        # ── Step 3: persist the new message_id ────────────────────────
        # Single UPDATE so we don't hold the session in a long
        # transaction. Wrapped in its own try because DB hiccups
        # shouldn't shadow the (successful) Telegram send.
        try:
            task.teacher_notif_message_id = sent.message_id
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "Failed to persist teacher_notif_message_id",
                extra={"task_id": task.id, "new_message_id": sent.message_id},
            )

        logger.info(
            "Sent teacher card",
            extra={
                "task_id": task.id,
                "teacher_chat_id": teacher_chat_id,
                "new_message_id": sent.message_id,
                "vote_count": len(task.poll_votes),
            },
        )
    except Exception:
        # Catch-all: notifications must never break vote saving.
        logger.exception(
            "notify_teacher_of_vote failed — non-fatal",
            extra={"task_id": getattr(task, "id", None)},
        )
