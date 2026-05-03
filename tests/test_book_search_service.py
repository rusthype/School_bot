"""Tests for search_available_books in book_service.py.

Pattern follows tests/test_escalation_service.py — no real DB, no Telegram.
AsyncSession is replaced by unittest.mock.AsyncMock / MagicMock.

Run with:
    cd /Users/max/PycharmProjects/School_bot && python3 -m pytest -q
"""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from school_bot.bot.services.book_service import (
    _escape_ilike,
    search_available_books,
)


def _run(coro):
    return asyncio.run(coro)


def _make_book(*, book_id: int = 1, title: str = "Test", author: str | None = None,
               is_available: bool = True, category_name: str = "1-sinf"):
    category = SimpleNamespace(name=category_name)
    return SimpleNamespace(
        id=book_id,
        title=title,
        author=author,
        is_available=is_available,
        category=category,
    )


def _session_returning(books: list) -> MagicMock:
    """Return a mock session whose .execute() resolves to books."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = books
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return session


# ---------------------------------------------------------------------------
# _escape_ilike unit tests (pure function — no DB needed)
# ---------------------------------------------------------------------------

class EscapeIlikeTests(unittest.TestCase):
    def test_escapes_percent(self) -> None:
        self.assertEqual(_escape_ilike("100%"), "100\\%")

    def test_escapes_underscore(self) -> None:
        self.assertEqual(_escape_ilike("a_b"), "a\\_b")

    def test_escapes_backslash_first(self) -> None:
        # A literal backslash must not become a double-escape of a
        # subsequent % or _ — the backslash escape must run first.
        self.assertEqual(_escape_ilike("a\\%"), "a\\\\\\%")

    def test_plain_query_unchanged(self) -> None:
        self.assertEqual(_escape_ilike("Alibobo"), "Alibobo")


# ---------------------------------------------------------------------------
# search_available_books — result content
# ---------------------------------------------------------------------------

class SearchAvailableBooksResultTests(unittest.TestCase):

    def test_returns_books_matching_title(self) -> None:
        """A book whose title contains the query should appear in results."""
        book = _make_book(title="Alibobo va qirq qaroqchi", author="Xalq ertagi")
        session = _session_returning([book])

        results = _run(search_available_books(session, "alibobo"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Alibobo va qirq qaroqchi")
        session.execute.assert_awaited_once()

    def test_returns_books_matching_author(self) -> None:
        """A book whose author contains the query should appear in results."""
        book = _make_book(title="Evgeniy Onegin", author="Aleksandr Pushkin")
        session = _session_returning([book])

        results = _run(search_available_books(session, "pushkin"))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].author, "Aleksandr Pushkin")

    def test_excludes_unavailable_books(self) -> None:
        """Books with is_available=False must never be returned.

        The filtering is in the DB query (WHERE is_available IS TRUE),
        so here we verify the SQL clause rather than mock-filtering after
        the fact. We capture the compiled statement and assert the clause
        is present.
        """
        captured: dict[str, str] = {}

        def _capture(stmt):
            captured["sql"] = str(
                stmt.compile(compile_kwargs={"literal_binds": True})
            ).lower()
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(search_available_books(session, "test"))

        self.assertIn("is_available", captured["sql"])
        # The IS TRUE clause is expressed as "is true" or "= true" depending
        # on the SQLAlchemy dialect/compiler; both are acceptable.
        self.assertTrue(
            "is true" in captured["sql"] or "= true" in captured["sql"],
            msg=f"Expected IS TRUE filter not found in SQL: {captured['sql']}",
        )

    def test_empty_query_returns_empty_list(self) -> None:
        """Empty string must short-circuit before hitting the DB."""
        session = _session_returning([])

        results = _run(search_available_books(session, ""))

        self.assertEqual(results, [])
        session.execute.assert_not_awaited()

    def test_whitespace_only_query_returns_empty_list(self) -> None:
        """Whitespace-only query should be treated the same as empty."""
        session = _session_returning([])

        results = _run(search_available_books(session, "   "))

        self.assertEqual(results, [])
        session.execute.assert_not_awaited()

    def test_short_query_still_reaches_db(self) -> None:
        """The service does NOT enforce a minimum length — that's the
        handler's responsibility.  A 1-char query must still call the DB.
        """
        session = _session_returning([])

        results = _run(search_available_books(session, "a"))

        self.assertEqual(results, [])
        session.execute.assert_awaited_once()

    def test_percent_query_does_not_behave_as_wildcard(self) -> None:
        """A query containing '%' must be escaped so it matches the
        literal character, not every row.

        We compile the generated SQLAlchemy statement against the
        default (generic) dialect and assert:
          1. The ESCAPE clause is present, confirming .ilike(escape='\\')
             was used.
          2. The pattern bound value contains the escaped percent (\\%)
             rather than a bare %, which would be a wildcard.
        """
        captured: dict[str, object] = {}

        def _capture(stmt):
            # Compile without literal_binds so we can inspect params dict.
            compiled = stmt.compile()
            captured["sql"] = str(compiled).lower()
            captured["params"] = compiled.params
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(search_available_books(session, "100%"))

        sql = captured.get("sql", "")
        params = captured.get("params", {})

        # The ESCAPE clause must be in the compiled SQL, confirming
        # the escape= kwarg was forwarded to the ILIKE expression.
        self.assertIn("escape", sql, msg="ESCAPE clause missing — escape= kwarg not applied")

        # At least one bound parameter must contain the escaped percent.
        # _escape_ilike("100%") -> "100\\%", wrapped -> "%100\\%%"
        escaped_found = any(
            "\\%" in str(v)
            for v in params.values()
        )
        self.assertTrue(
            escaped_found,
            msg=f"No escaped \\% found in query params: {params}",
        )

    def test_limit_is_applied_in_query(self) -> None:
        """LIMIT must be part of the SQL query, not a Python slice.

        We capture the compiled SQL and assert the LIMIT clause is present
        with a value <= 20 (the default cap).
        """
        captured: dict[str, str] = {}

        def _capture(stmt):
            captured["sql"] = str(
                stmt.compile(compile_kwargs={"literal_binds": True})
            ).lower()
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(search_available_books(session, "kitob"))

        self.assertIn("limit", captured["sql"], msg="LIMIT clause missing from query")
        self.assertIn("20", captured["sql"], msg="Default LIMIT value 20 missing from query")

    def test_custom_limit_is_respected(self) -> None:
        """Passing limit=5 should place LIMIT 5 in the SQL."""
        captured: dict[str, str] = {}

        def _capture(stmt):
            captured["sql"] = str(
                stmt.compile(compile_kwargs={"literal_binds": True})
            ).lower()
            r = MagicMock()
            r.scalars.return_value.all.return_value = []
            return r

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        _run(search_available_books(session, "kitob", limit=5))

        self.assertIn("5", captured["sql"])


if __name__ == "__main__":
    unittest.main()
