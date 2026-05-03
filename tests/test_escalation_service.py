"""Tests for the daily book-order escalation pipeline.

These tests follow the same mock-based pattern as
``tests/test_profile_service_link.py`` — no real database, no real
Telegram. We rely on ``unittest.mock`` to stand in for AsyncSession,
Bot, and the ORM rows we'd otherwise pull from the DB.

The "skip" cases that would normally need a real DB to be meaningful
(skip-delivered, skip-already-escalated, skip-future-deadline) are
verified instead by inspecting the compiled WHERE clause produced by
``find_overdue_orders`` — that's the only seam where the filtering is
defined, so anchoring assertions there guards against silent filter
removal in a future refactor.

Run with: python3 -m pytest tests/test_escalation_service.py -v
"""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from school_bot.bot.services import escalation_job
from school_bot.bot.services.escalation_service import (
    ESCALATION_COMMENT,
    OPEN_STATUSES,
    EscalationResult,
    escalate_order,
    find_overdue_orders,
    list_superadmin_chat_ids,
)
from school_bot.database.models import BookOrder, OrderStatusHistory


def _run(coro):
    return asyncio.run(coro)


def _make_order(
    *,
    order_id: int = 1,
    status: str = "pending",
    escalated: bool = False,
    delivery_deadline: datetime | None = None,
    teacher: object | None = None,
    librarian: object | None = None,
):
    return SimpleNamespace(
        id=order_id,
        status=status,
        escalated=escalated,
        delivery_deadline=delivery_deadline,
        teacher=teacher,
        librarian=librarian,
    )


def _make_user(*, user_id: int, telegram_id: int, full_name: str | None = None, role: str = "superadmin"):
    return SimpleNamespace(
        id=user_id,
        telegram_id=telegram_id,
        full_name=full_name,
        role=role,
    )


def _session_with_query_results(*results):
    """Each entry is what the next session.execute(...).scalars().all()
    should return. Multiple entries simulate multiple SELECTs."""
    session = MagicMock()
    side_effects = []
    for rows in results:
        r = MagicMock()
        r.scalars.return_value.all.return_value = rows
        side_effects.append(r)
    session.execute = AsyncMock(side_effect=side_effects)
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# find_overdue_orders
# ---------------------------------------------------------------------------

class FindOverdueOrdersTests(unittest.TestCase):
    def test_finds_overdue_order(self) -> None:
        """Positive path: the SELECT returns one overdue order, the
        function relays it back as a list."""
        deadline = datetime.now(timezone.utc) - timedelta(days=2)
        order = _make_order(order_id=10, delivery_deadline=deadline)
        session = _session_with_query_results([order])

        result = _run(find_overdue_orders(session))

        self.assertEqual(result, [order])
        session.execute.assert_awaited_once()

    def test_returns_empty_when_db_empty(self) -> None:
        session = _session_with_query_results([])
        self.assertEqual(_run(find_overdue_orders(session)), [])

    def test_query_filters_required_columns(self) -> None:
        """The compiled SQL must reference status, escalated, and
        delivery_deadline. If a future refactor accidentally drops one
        of those WHERE clauses, this test fails fast."""
        captured: dict[str, str] = {}

        def _capture(stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(find_overdue_orders(session))

        sql = captured["sql"]
        self.assertIn("bot_book_orders.status in", sql)
        self.assertIn("bot_book_orders.escalated", sql)
        self.assertIn("bot_book_orders.delivery_deadline", sql)
        self.assertIn("is not null", sql)

    def test_query_only_selects_open_statuses(self) -> None:
        """Verifies the IN clause only contains pending/processing/
        confirmed — i.e. delivered, rejected, cancelled are excluded.
        Skip-delivered-order coverage."""
        captured: dict[str, str] = {}

        def _capture(stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(find_overdue_orders(session))

        for s in OPEN_STATUSES:
            self.assertIn(f"'{s}'", captured["sql"])
        for s in ("delivered", "rejected", "cancelled"):
            self.assertNotIn(f"'{s}'", captured["sql"])

    def test_query_excludes_already_escalated(self) -> None:
        """The WHERE clause must require escalated IS FALSE so a second
        run doesn't re-fire on the same orders. Skip-already-escalated
        coverage."""
        captured: dict[str, str] = {}

        def _capture(stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(find_overdue_orders(session))
        self.assertIn("escalated", captured["sql"])
        self.assertIn("false", captured["sql"])

    def test_query_excludes_future_deadlines(self) -> None:
        """The deadline comparison must be ``< now``. With a fixed `now`
        bound, the literal in the SQL should match. Skip-future-deadline
        coverage."""
        captured: dict[str, str] = {}
        fixed_now = datetime(2026, 5, 3, 9, 0, 0, tzinfo=timezone.utc)

        def _capture(stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(find_overdue_orders(session, now=fixed_now))
        self.assertIn("delivery_deadline <", captured["sql"])
        self.assertIn("2026-05-03", captured["sql"])


# ---------------------------------------------------------------------------
# escalate_order
# ---------------------------------------------------------------------------

class EscalateOrderTests(unittest.TestCase):
    def test_marks_flag_and_writes_history(self) -> None:
        """Sets order.escalated=True, adds an OrderStatusHistory row
        with the escalation comment, attributes to the first superadmin."""
        admin = _make_user(user_id=99, telegram_id=111111, full_name="Admin Admin")
        order = _make_order(order_id=42, status="pending", escalated=False)

        session = _session_with_query_results([admin])
        # Make flush() set history.id on the added object so
        # EscalationResult exposes it.
        added: list = []

        def fake_add(obj):
            added.append(obj)
            obj.id = 7
        session.add = MagicMock(side_effect=fake_add)

        result = _run(escalate_order(session, order))

        self.assertIsInstance(result, EscalationResult)
        self.assertEqual(result.order_id, 42)
        self.assertEqual(result.history_id, 7)
        self.assertTrue(order.escalated)

        # Exactly one history row was added with correct fields.
        history_rows = [r for r in added if isinstance(r, OrderStatusHistory)]
        self.assertEqual(len(history_rows), 1)
        h = history_rows[0]
        self.assertEqual(h.order_id, 42)
        self.assertEqual(h.old_status, "pending")
        self.assertEqual(h.new_status, "pending")
        self.assertEqual(h.changed_by_id, 99)
        self.assertEqual(h.comment, ESCALATION_COMMENT)
        session.flush.assert_awaited_once()

    def test_skips_history_when_no_superadmin(self) -> None:
        """If no superadmin exists, the flag still flips but the history
        row is skipped (FK would be NULL — better to log + skip)."""
        order = _make_order(order_id=5, status="confirmed", escalated=False)
        session = _session_with_query_results([])  # no superadmins

        result = _run(escalate_order(session, order))

        self.assertEqual(result.order_id, 5)
        self.assertIsNone(result.history_id)
        self.assertTrue(order.escalated)
        session.add.assert_not_called()
        session.flush.assert_not_awaited()


# ---------------------------------------------------------------------------
# list_superadmin_chat_ids
# ---------------------------------------------------------------------------

class ListSuperadminChatIdsTests(unittest.TestCase):
    def test_returns_telegram_ids_only(self) -> None:
        admins = [
            _make_user(user_id=1, telegram_id=10),
            _make_user(user_id=2, telegram_id=20),
            _make_user(user_id=3, telegram_id=None),  # filtered out
        ]
        session = _session_with_query_results(admins)
        self.assertEqual(_run(list_superadmin_chat_ids(session)), [10, 20])


# ---------------------------------------------------------------------------
# run_escalation_check
# ---------------------------------------------------------------------------

class _FakeSessionFactory:
    """A no-arg callable that returns an async-context-manager wrapping
    the same session each time. Mimics async_sessionmaker."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class RunEscalationCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_zero_when_no_overdue(self) -> None:
        session = _session_with_query_results([])  # find_overdue returns []
        bot = MagicMock()
        bot.send_message = AsyncMock()

        result = await escalation_job.run_escalation_check(
            bot, _FakeSessionFactory(session)
        )
        self.assertEqual(result, 0)
        bot.send_message.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_processes_orders_and_notifies(self) -> None:
        teacher = _make_user(user_id=1, telegram_id=1001, full_name="T One", role="teacher")
        librarian = _make_user(user_id=2, telegram_id=2002, full_name="L One", role="librarian")
        admin = _make_user(user_id=99, telegram_id=9999, full_name="Admin")
        deadline = datetime.now(timezone.utc) - timedelta(days=3)
        order = _make_order(
            order_id=42,
            status="pending",
            delivery_deadline=deadline,
            teacher=teacher,
            librarian=librarian,
        )

        # Sequence of session.execute results:
        #   1) find_overdue_orders -> [order]
        #   2) list_superadmin_chat_ids -> [admin]
        #   3) escalate_order -> list_superadmins -> [admin]
        session = _session_with_query_results([order], [admin], [admin])

        added: list = []

        def fake_add(obj):
            added.append(obj)
            obj.id = 1
        session.add = MagicMock(side_effect=fake_add)

        bot = MagicMock()
        bot.send_message = AsyncMock()

        result = await escalation_job.run_escalation_check(
            bot, _FakeSessionFactory(session)
        )

        self.assertEqual(result, 1)
        self.assertTrue(order.escalated)
        # Three sends: librarian + teacher + 1 superadmin.
        self.assertEqual(bot.send_message.await_count, 3)

        chat_ids = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list]
        self.assertIn(1001, chat_ids)  # teacher
        self.assertIn(2002, chat_ids)  # librarian
        self.assertIn(9999, chat_ids)  # superadmin

        session.commit.assert_awaited_once()

    async def test_idempotent_second_run_returns_zero(self) -> None:
        """Simulate the second run: find_overdue_orders returns []
        because the first run flipped escalated=True. The job must short-
        circuit before fetching superadmins or committing."""
        session = _session_with_query_results([])
        bot = MagicMock()
        bot.send_message = AsyncMock()

        first = await escalation_job.run_escalation_check(
            bot, _FakeSessionFactory(session)
        )
        self.assertEqual(first, 0)

        # Reset and run again — same outcome since the DB has no overdue rows.
        session = _session_with_query_results([])
        second = await escalation_job.run_escalation_check(
            bot, _FakeSessionFactory(session)
        )
        self.assertEqual(second, 0)

    async def test_continues_on_per_order_failure(self) -> None:
        """A failing escalate_order on one order must not abort the
        batch — the loop wraps each iteration in try/except and logs."""
        teacher_a = _make_user(user_id=1, telegram_id=1001, role="teacher")
        teacher_b = _make_user(user_id=2, telegram_id=1002, role="teacher")
        deadline = datetime.now(timezone.utc) - timedelta(days=1)
        order_a = _make_order(order_id=1, delivery_deadline=deadline, teacher=teacher_a)
        order_b = _make_order(order_id=2, delivery_deadline=deadline, teacher=teacher_b)

        # find_overdue -> [a, b]; superadmin lookup -> []; per-order
        # escalate_order calls list_superadmins -> [] each (no history row).
        session = _session_with_query_results([order_a, order_b], [], [], [])

        bot = MagicMock()
        bot.send_message = AsyncMock()

        # Patch escalate_order so order_a raises, order_b succeeds.
        original = escalation_job.escalate_order
        calls = {"n": 0}

        async def flaky(sess, order):
            calls["n"] += 1
            if order.id == 1:
                raise RuntimeError("boom")
            order.escalated = True
            return EscalationResult(order_id=order.id, history_id=None)

        escalation_job.escalate_order = flaky
        try:
            result = await escalation_job.run_escalation_check(
                bot, _FakeSessionFactory(session)
            )
        finally:
            escalation_job.escalate_order = original

        self.assertEqual(result, 1)  # only order_b succeeded
        self.assertTrue(order_b.escalated)
        # order_a's send was never reached because escalate raised first
        # in the try-block.
        chat_ids = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list]
        self.assertIn(1002, chat_ids)
        self.assertNotIn(1001, chat_ids)
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
