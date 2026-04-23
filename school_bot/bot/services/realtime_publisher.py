"""Real-time Redis publisher for poll-vote events.

Each time a parent submits a Telegram poll answer, `handle_poll_answer` calls
``publish_poll_vote`` after the ``PollVote`` rows are committed to Postgres.
This helper looks up the school_id through the
``bot_profiles.alochi_teacher_id -> teachers_teacher.school_id`` chain and
publishes a JSON payload to Redis channel ``poll_votes`` on DB 1 (separate
from the aiogram FSM storage, which lives on DB 0).

The Alochi backend (alochi_backend) subscribes to this channel and forwards
the event to admin panel WebSockets for real-time notifications.

Design notes
------------
* Failure is non-fatal: all errors are caught and logged. A broken Redis must
  never prevent a vote from being saved.
* Skips silently when the teacher is not linked to an Alochi row
  (``alochi_teacher_id`` is NULL) — publish has nothing useful to send.
* Uses a lazily-initialised, module-level Redis client so we don't open a
  new connection per vote. DB index is forced to 1 regardless of what the
  FSM ``REDIS_URL`` points at.
"""
from __future__ import annotations

import json
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from school_bot.bot.config import Settings
from school_bot.bot.services.logger_service import get_logger
from school_bot.database.models import PollVote, Task


logger = get_logger(__name__)

# Channel name is part of the public contract with alochi_backend.
# DO NOT rename without updating the subscriber on the panel side.
PUBSUB_CHANNEL = "poll_votes"
PUBSUB_DB = 1

# Human-readable labels for the four poll options. Index 0 is the most
# positive answer and index 3 is the most negative — matches POLL_OPTIONS
# order in handlers/teacher.py.
OPTION_TEXTS = [
    "Juda yaxshi",
    "Yaxshi",
    "Oz",
    "Umuman tushunmadi",
]

_redis: Optional[Redis] = None


def _pubsub_redis_url(base_url: str) -> str:
    """Return a redis:// URL pointing at the pub/sub DB.

    Re-uses host/port/password from the existing REDIS_URL but rewrites the
    database index to ``PUBSUB_DB`` so we never clobber FSM keys on DB 0.
    Falls back to ``redis://localhost:6379/{PUBSUB_DB}`` if the URL cannot
    be parsed.
    """
    try:
        # URL format: redis://[:password@]host[:port][/db]
        # We strip the trailing `/<db>` if present and append our own.
        if "://" not in base_url:
            raise ValueError("missing scheme")
        scheme, rest = base_url.split("://", 1)
        if "/" in rest:
            host_part, _ = rest.split("/", 1)
        else:
            host_part = rest
        return f"{scheme}://{host_part}/{PUBSUB_DB}"
    except Exception:
        logger.warning(
            "Could not derive pub/sub Redis URL from %r — using default",
            base_url,
        )
        return f"redis://localhost:6379/{PUBSUB_DB}"


async def get_redis() -> Redis:
    """Return the shared Redis client for the pub/sub channel.

    Lazy: the first call constructs the client, subsequent calls reuse it.
    Uses ``decode_responses=False`` because we publish UTF-8 JSON bytes and
    the subscriber decodes explicitly.
    """
    global _redis
    if _redis is None:
        settings = Settings()
        url = _pubsub_redis_url(settings.redis_url)
        _redis = Redis.from_url(url)
    return _redis


async def publish_poll_vote(
    session: AsyncSession,
    vote: PollVote,
    task: Task,
) -> None:
    """Publish a poll-vote event to Redis for real-time panel notifications.

    Called after ``PollVote`` rows have been committed. Looks up the school
    and voter/teacher names in one SQL round-trip, builds the JSON payload,
    and publishes on channel ``poll_votes`` (DB 1).

    Safely skips publish when the teacher is not linked to an Alochi row.
    All errors are swallowed after logging — we never want a Redis failure
    to surface to the user.
    """
    try:
        # One round-trip: teacher -> Alochi teacher -> school, plus voter name.
        # `task.teacher_id` is `bot_users.id`. The teacher's Profile carries
        # `alochi_teacher_id`, which joins to `teachers_teacher.id`.
        # The voter's Profile lives in the same `bot_profiles` table, looked
        # up by `vote.user_id` (also a `bot_users.id`). Voter-profile join is
        # LEFT so votes from users without a Profile still publish.
        result = await session.execute(
            text(
                """
                SELECT
                    t.school_id,
                    t.name       AS teacher_name,
                    p_voter.first_name AS voter_first_name,
                    p_voter.last_name  AS voter_last_name
                FROM bot_profiles p_teacher
                JOIN teachers_teacher t
                    ON t.id::text = p_teacher.alochi_teacher_id
                LEFT JOIN bot_profiles p_voter
                    ON p_voter.bot_user_id = :voter_id
                WHERE p_teacher.bot_user_id = :teacher_bot_id
                LIMIT 1
                """
            ),
            {
                "teacher_bot_id": task.teacher_id,
                "voter_id": vote.user_id,
            },
        )
        row = result.fetchone()
        if not row or row[0] is None:
            logger.info(
                "Skipping poll_vote publish: teacher not linked to Alochi",
                extra={
                    "task_id": task.id,
                    "teacher_bot_user_id": task.teacher_id,
                },
            )
            return

        school_id, teacher_name, voter_first, voter_last = row

        voter_name = " ".join(
            part for part in (voter_first, voter_last) if part
        ).strip() or "Noma'lum"

        option_id = vote.option_id
        # Stars: option 0 (best) -> 4 stars, option 3 (worst) -> 1 star.
        if 0 <= option_id <= 3:
            rating_stars = 4 - option_id
            option_text = OPTION_TEXTS[option_id]
        else:
            rating_stars = 0
            option_text = ""

        voted_at = getattr(vote, "voted_at", None)
        payload = {
            "school_id": str(school_id),
            "task_id": str(task.id),
            "teacher_bot_user_id": str(task.teacher_id),
            "teacher_name": teacher_name,
            "topic": getattr(task, "topic", "") or "",
            "voter_bot_user_id": str(vote.user_id),
            "voter_name": voter_name,
            "rating_stars": rating_stars,
            "option_id": option_id,
            "option_text": option_text,
            "voted_at": voted_at.isoformat() if voted_at else None,
        }

        redis = await get_redis()
        await redis.publish(PUBSUB_CHANNEL, json.dumps(payload))
        logger.info(
            "Published poll_vote",
            extra={
                "school_id": school_id,
                "task_id": task.id,
                "rating_stars": rating_stars,
            },
        )
    except Exception:
        # Never let a Redis/SQL/serialization failure break vote saving.
        logger.exception(
            "Failed to publish poll_vote — non-fatal",
            extra={
                "task_id": getattr(task, "id", None),
                "voter_id": getattr(vote, "user_id", None),
            },
        )
