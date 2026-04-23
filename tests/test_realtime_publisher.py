"""Tests for realtime_publisher.publish_poll_vote.

These tests mock AsyncSession and the module-level Redis client so they
run without a database or a live Redis instance. They verify:

  - When the teacher is not linked to Alochi (row missing or school_id is
    NULL), we skip publish silently — nothing gets sent to Redis.
  - When the teacher IS linked, we publish a correctly-shaped JSON payload
    on the expected channel with rating_stars computed from option_id.
  - Any failure inside the publisher is swallowed (non-fatal).

Run with: python -m pytest tests/test_realtime_publisher.py -v
(or python -m unittest tests.test_realtime_publisher)
"""
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from school_bot.bot.services import realtime_publisher
from school_bot.bot.services.realtime_publisher import (
    PUBSUB_CHANNEL,
    publish_poll_vote,
)


def _run(coro):
    return asyncio.run(coro)


def _make_vote(option_id: int = 0, user_id: int = 1001):
    return SimpleNamespace(
        id=555,
        user_id=user_id,
        option_id=option_id,
        option_text="",
        voted_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
    )


def _make_task(task_id: int = 42, teacher_id: int = 9001, topic: str = "Algebra"):
    return SimpleNamespace(
        id=task_id,
        teacher_id=teacher_id,
        topic=topic,
    )


def _session_returning(row):
    """AsyncSession mock whose execute().fetchone() returns ``row``."""
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.fetchone.return_value = row
    session.execute = AsyncMock(return_value=exec_result)
    return session


class PublishPollVoteTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset the module-level client so each test gets a fresh patch.
        realtime_publisher._redis = None

    def test_skip_when_teacher_not_linked(self) -> None:
        """No bot_profiles row for this teacher -> fetchone() returns None."""
        session = _session_returning(None)
        vote = _make_vote()
        task = _make_task()

        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()

        with patch(
            "school_bot.bot.services.realtime_publisher.get_redis",
            new=AsyncMock(return_value=fake_redis),
        ):
            _run(publish_poll_vote(session, vote, task))

        # SQL was issued once, but no Redis publish happened.
        session.execute.assert_awaited_once()
        fake_redis.publish.assert_not_awaited()

    def test_skip_when_school_id_is_null(self) -> None:
        """Row exists but school_id is NULL (teacher row orphaned)."""
        session = _session_returning((None, "T. Aliyev", "Anna", "Karimova"))
        vote = _make_vote()
        task = _make_task()

        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()

        with patch(
            "school_bot.bot.services.realtime_publisher.get_redis",
            new=AsyncMock(return_value=fake_redis),
        ):
            _run(publish_poll_vote(session, vote, task))

        fake_redis.publish.assert_not_awaited()

    def test_publishes_correct_payload(self) -> None:
        """Happy path: row exists, payload shape and rating are correct."""
        session = _session_returning((7, "T. Aliyev", "Anna", "Karimova"))
        # option_id=1 -> "Yaxshi" -> 3 stars (4 - 1).
        vote = _make_vote(option_id=1, user_id=1001)
        task = _make_task(task_id=42, teacher_id=9001, topic="Algebra")

        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()

        with patch(
            "school_bot.bot.services.realtime_publisher.get_redis",
            new=AsyncMock(return_value=fake_redis),
        ):
            _run(publish_poll_vote(session, vote, task))

        fake_redis.publish.assert_awaited_once()
        channel, raw_payload = fake_redis.publish.await_args.args
        self.assertEqual(channel, PUBSUB_CHANNEL)

        payload = json.loads(raw_payload)
        self.assertEqual(payload["school_id"], "7")
        self.assertEqual(payload["task_id"], "42")
        self.assertEqual(payload["teacher_bot_user_id"], "9001")
        self.assertEqual(payload["teacher_name"], "T. Aliyev")
        self.assertEqual(payload["topic"], "Algebra")
        self.assertEqual(payload["voter_bot_user_id"], "1001")
        self.assertEqual(payload["voter_name"], "Anna Karimova")
        self.assertEqual(payload["rating_stars"], 3)
        self.assertEqual(payload["option_id"], 1)
        self.assertEqual(payload["option_text"], "Yaxshi")
        self.assertIsNotNone(payload["voted_at"])

    def test_voter_name_falls_back_when_profile_missing(self) -> None:
        """LEFT JOIN returned NULL names -> 'Noma'lum'."""
        session = _session_returning((7, "T. Aliyev", None, None))
        vote = _make_vote(option_id=3)  # worst -> 1 star
        task = _make_task()

        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock()

        with patch(
            "school_bot.bot.services.realtime_publisher.get_redis",
            new=AsyncMock(return_value=fake_redis),
        ):
            _run(publish_poll_vote(session, vote, task))

        fake_redis.publish.assert_awaited_once()
        _, raw_payload = fake_redis.publish.await_args.args
        payload = json.loads(raw_payload)
        self.assertEqual(payload["voter_name"], "Noma'lum")
        self.assertEqual(payload["rating_stars"], 1)
        self.assertEqual(payload["option_text"], "Umuman tushunmadi")

    def test_redis_failure_is_swallowed(self) -> None:
        """If redis.publish raises, we log and return — never propagate."""
        session = _session_returning((7, "T. Aliyev", "Anna", "Karimova"))
        vote = _make_vote()
        task = _make_task()

        fake_redis = MagicMock()
        fake_redis.publish = AsyncMock(side_effect=ConnectionError("boom"))

        with patch(
            "school_bot.bot.services.realtime_publisher.get_redis",
            new=AsyncMock(return_value=fake_redis),
        ):
            # Must NOT raise.
            _run(publish_poll_vote(session, vote, task))

    def test_sql_failure_is_swallowed(self) -> None:
        """DB error during lookup should also be swallowed."""
        session = MagicMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db gone"))
        vote = _make_vote()
        task = _make_task()

        # Must NOT raise.
        _run(publish_poll_vote(session, vote, task))


class PubsubRedisUrlTests(unittest.TestCase):
    def test_swaps_db_index_to_one(self) -> None:
        self.assertEqual(
            realtime_publisher._pubsub_redis_url("redis://localhost:6379/0"),
            "redis://localhost:6379/1",
        )

    def test_appends_db_index_when_missing(self) -> None:
        self.assertEqual(
            realtime_publisher._pubsub_redis_url("redis://localhost:6379"),
            "redis://localhost:6379/1",
        )

    def test_preserves_auth_and_host(self) -> None:
        self.assertEqual(
            realtime_publisher._pubsub_redis_url("redis://:pass@redis.internal:6380/0"),
            "redis://:pass@redis.internal:6380/1",
        )

    def test_invalid_url_falls_back(self) -> None:
        self.assertEqual(
            realtime_publisher._pubsub_redis_url("not-a-url"),
            "redis://localhost:6379/1",
        )


if __name__ == "__main__":
    unittest.main()
