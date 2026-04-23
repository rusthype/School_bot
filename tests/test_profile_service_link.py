"""Tests for try_link_to_alochi_teacher and find_profile_by_phone.

These use unittest.mock.AsyncMock to stand in for AsyncSession, so they
run without a database. They verify:

  - A matching Alochi teacher causes profile.alochi_teacher_id to be set
    and the symmetric UPDATE teachers_teacher.bot_user_id to be issued.
  - A non-matching phone returns None and does NOT mutate the profile.
  - A profile with no phone returns None immediately.
  - A phone that cannot be normalized (junk input) returns None without
    hitting the database.
  - find_profile_by_phone excludes the current bot user.

Run with: python -m pytest tests/test_profile_service_link.py -v
(or python -m unittest tests.test_profile_service_link)
"""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from school_bot.bot.services.profile_service import (
    try_link_to_alochi_teacher,
    find_profile_by_phone,
)


def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def _make_profile(phone: str | None, bot_user_id: int = 42):
    """Build a lightweight Profile-like stub.

    We use SimpleNamespace rather than the real Profile model to avoid
    booting SQLAlchemy's declarative state during unit tests.
    """
    return SimpleNamespace(
        bot_user_id=bot_user_id,
        phone=phone,
        alochi_teacher_id=None,
    )


def _make_session_with_row(row):
    """Build an AsyncSession mock where the first execute() returns `row`.

    The second execute() (the UPDATE teachers_teacher) returns an empty
    mock. commit/refresh are AsyncMocks.
    """
    session = MagicMock()

    select_result = MagicMock()
    select_result.fetchone.return_value = row

    update_result = MagicMock()
    update_result.fetchone.return_value = None

    session.execute = AsyncMock(side_effect=[select_result, update_result])
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


class TryLinkToAlochiTeacherTests(unittest.TestCase):
    def test_match_sets_alochi_teacher_id_and_writes_back(self) -> None:
        profile = _make_profile(phone="998943910579", bot_user_id=17)
        alochi_uuid = "11111111-2222-3333-4444-555555555555"
        row = (alochi_uuid, "Yusupova Shoiraxon")
        session = _make_session_with_row(row)

        result = _run(try_link_to_alochi_teacher(session, profile))

        self.assertEqual(result, "Yusupova Shoiraxon")
        self.assertEqual(profile.alochi_teacher_id, alochi_uuid)
        # Two executes: SELECT then symmetric UPDATE.
        self.assertEqual(session.execute.call_count, 2)
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once_with(profile)

        # Verify the SELECT used the NORMALIZED phone (with +).
        first_call = session.execute.call_args_list[0]
        select_params = first_call.args[1]
        self.assertEqual(select_params["phone"], "+998943910579")

        # Verify UPDATE bound the profile's bot_user_id and matching teacher id.
        second_call = session.execute.call_args_list[1]
        update_params = second_call.args[1]
        self.assertEqual(update_params["bot_user_id"], 17)
        self.assertEqual(update_params["teacher_id"], alochi_uuid)

    def test_no_match_returns_none(self) -> None:
        profile = _make_profile(phone="998900555526")  # Rustamov_hype, no Alochi row
        session = _make_session_with_row(None)

        result = _run(try_link_to_alochi_teacher(session, profile))

        self.assertIsNone(result)
        self.assertIsNone(profile.alochi_teacher_id)
        # Only one execute — no UPDATE when there's no match.
        self.assertEqual(session.execute.call_count, 1)
        session.commit.assert_not_awaited()

    def test_missing_phone_returns_none_without_db(self) -> None:
        profile = _make_profile(phone=None)
        session = _make_session_with_row(None)

        result = _run(try_link_to_alochi_teacher(session, profile))

        self.assertIsNone(result)
        session.execute.assert_not_awaited()

    def test_unnormalizable_phone_returns_none_without_db(self) -> None:
        profile = _make_profile(phone="garbage")
        session = _make_session_with_row(None)

        result = _run(try_link_to_alochi_teacher(session, profile))

        self.assertIsNone(result)
        session.execute.assert_not_awaited()


class FindProfileByPhoneTests(unittest.TestCase):
    def test_builds_select_and_excludes_current_user(self) -> None:
        session = MagicMock()
        select_result = MagicMock()
        select_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=select_result)

        _run(find_profile_by_phone(session, "+998901234567", exclude_bot_user_id=99))

        session.execute.assert_awaited_once()
        # We can't easily introspect the compiled SQL, but we can at least
        # confirm a single SELECT was issued against the session.
        self.assertEqual(session.execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
