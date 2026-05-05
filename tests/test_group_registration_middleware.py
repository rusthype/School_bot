"""Tests for GroupRegistrationMiddleware.

Pattern mirrors tests/test_book_search_service.py — no real DB, no Telegram.
AsyncSession is replaced by unittest.mock.AsyncMock / MagicMock.

Run with:
    cd /Users/max/PycharmProjects/School_bot && python3 -m pytest tests/test_group_registration_middleware.py -v
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message
from sqlalchemy.exc import IntegrityError


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(*, chat_type: str, chat_id: int = -1001234567890,
                  chat_title: str | None = "56-maktab 1-A") -> MagicMock:
    """Build a minimal aiogram Message mock.

    Must use spec=Message so that isinstance(msg, Message) is True,
    which is what GroupRegistrationMiddleware checks internally.
    """
    chat = SimpleNamespace(
        type=chat_type,
        id=chat_id,
        title=chat_title,
    )
    msg = MagicMock(spec=Message)
    msg.chat = chat
    return msg


def _make_session(*, row_exists: bool = False) -> MagicMock:
    """Return a mock AsyncSession.

    If row_exists=True, execute() returns a scalar_one_or_none() of 42
    (simulating an existing group id), otherwise None.
    """
    scalar_value = 42 if row_exists else None
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scalar_value

    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _noop_handler(event, data):
    """Minimal async handler that just returns None."""
    async def _inner():
        return None
    return _inner()


# ---------------------------------------------------------------------------
# Reload the module fresh for each test to reset the process-local cache.
# ---------------------------------------------------------------------------

def _fresh_middleware():
    """Import (or reload) the middleware module and return a new instance.

    The module-level _known_chat_ids set persists between test runs in the
    same process.  Reloading the module resets it to an empty set so each
    test starts clean.
    """
    mod_name = "school_bot.bot.middlewares.group_registration"
    if mod_name in sys.modules:
        mod = importlib.reload(sys.modules[mod_name])
    else:
        import school_bot.bot.middlewares.group_registration as mod
    return mod.GroupRegistrationMiddleware()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class GroupRegistrationMiddlewareTests(unittest.TestCase):

    def test_message_from_unknown_group_creates_pending_row(self) -> None:
        """chat.type='supergroup', chat.id not in DB or cache ->
        session.add called once with status='pending' and alochi_group_id=None,
        and handler still runs.
        """
        middleware = _fresh_middleware()
        session = _make_session(row_exists=False)
        message = _make_message(chat_type="supergroup", chat_id=-100111222333)

        handler_called = []

        async def fake_handler(event, data):
            handler_called.append(True)

        data = {"session": session}
        _run(middleware(fake_handler, message, data))

        # session.add must have been called exactly once
        session.add.assert_called_once()
        added_group = session.add.call_args[0][0]

        from school_bot.database.models import Group
        self.assertIsInstance(added_group, Group)
        self.assertEqual(added_group.status, "pending")
        self.assertIsNone(added_group.alochi_group_id)
        self.assertIsNone(added_group.school_id)
        self.assertEqual(added_group.chat_id, -100111222333)

        # commit must have been called
        session.commit.assert_awaited_once()

        # handler must still run
        self.assertEqual(handler_called, [True])

    def test_message_from_known_group_skips_insert(self) -> None:
        """chat.id already in the process-local cache -> no DB query, no
        session.add, handler runs.
        """
        middleware = _fresh_middleware()
        # Pre-populate the module-level cache
        import school_bot.bot.middlewares.group_registration as mod
        chat_id = -100999888777
        mod._known_chat_ids.add(chat_id)

        session = _make_session(row_exists=False)
        message = _make_message(chat_type="group", chat_id=chat_id)

        handler_called = []

        async def fake_handler(event, data):
            handler_called.append(True)

        data = {"session": session}
        _run(middleware(fake_handler, message, data))

        session.execute.assert_not_awaited()
        session.add.assert_not_called()
        session.commit.assert_not_awaited()
        self.assertEqual(handler_called, [True])

    def test_private_chat_message_skips_middleware(self) -> None:
        """chat.type='private' -> handler called directly, no DB touch."""
        middleware = _fresh_middleware()
        session = _make_session(row_exists=False)
        message = _make_message(chat_type="private", chat_id=987654321)

        handler_called = []

        async def fake_handler(event, data):
            handler_called.append(True)

        data = {"session": session}
        _run(middleware(fake_handler, message, data))

        session.execute.assert_not_awaited()
        session.add.assert_not_called()
        self.assertEqual(handler_called, [True])

    def test_concurrent_first_messages_handle_race_via_integrity_error(self) -> None:
        """Simulate IntegrityError on commit (another worker INSERTed first).

        rollback must be called, handler must still run, and no exception
        is propagated.
        """
        middleware = _fresh_middleware()

        # session.execute says row does NOT exist (our SELECT ran before the race)
        session = _make_session(row_exists=False)
        # But commit raises IntegrityError (the other worker committed first)
        session.commit = AsyncMock(side_effect=IntegrityError("unique", {}, None))

        message = _make_message(chat_type="supergroup", chat_id=-100777666555)

        handler_called = []

        async def fake_handler(event, data):
            handler_called.append(True)

        data = {"session": session}
        # Must not raise
        _run(middleware(fake_handler, message, data))

        session.rollback.assert_awaited_once()
        self.assertEqual(handler_called, [True])

    def test_chat_with_no_title_uses_fallback_name(self) -> None:
        """chat.title is None -> Group.name is set to 'Unknown group {chat_id}'."""
        middleware = _fresh_middleware()
        session = _make_session(row_exists=False)
        chat_id = -100444333222
        message = _make_message(
            chat_type="group",
            chat_id=chat_id,
            chat_title=None,
        )

        async def fake_handler(event, data):
            pass

        data = {"session": session}
        _run(middleware(fake_handler, message, data))

        session.add.assert_called_once()
        added_group = session.add.call_args[0][0]
        self.assertEqual(added_group.name, f"Unknown group {chat_id}")


if __name__ == "__main__":
    unittest.main()
