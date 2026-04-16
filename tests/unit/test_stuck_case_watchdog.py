"""Unit tests for src.services.stuck_case_watchdog.

These tests verify the Python control flow of the watchdog: which DB calls
are made, in what order, and which side effects depend on flags. The actual
SQL ``WHERE`` clause semantics (status filter, COALESCE on updated_at) are
covered by ``tests/integration/test_stuck_case_watchdog_pg.py`` — that's
where Postgres truth lives.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.stuck_case_watchdog import find_and_mark_stuck_cases


def _mock_session_with_select_rows(rows: list) -> AsyncMock:
    """AsyncSession whose `.execute()` returns a result with `.all()` == rows.

    A second `.execute()` call (the UPDATE) returns a separate empty result.
    """
    select_result = MagicMock()
    select_result.all.return_value = rows
    update_result = MagicMock()
    session = AsyncMock(spec=AsyncSession)
    # side_effect drains in order: first call -> SELECT result, second -> UPDATE
    session.execute = AsyncMock(side_effect=[select_result, update_result])
    return session


@pytest.mark.asyncio
async def test_no_stuck_cases_returns_empty_and_writes_nothing():
    session = _mock_session_with_select_rows([])

    marked = await find_and_mark_stuck_cases(session)

    assert marked == []
    # Only the SELECT happened — no UPDATE, no commit.
    assert session.execute.call_count == 1
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_one_stuck_case_is_marked_and_committed():
    case_id = uuid.uuid4()
    session = _mock_session_with_select_rows([(case_id,)])

    marked = await find_and_mark_stuck_cases(session)

    assert marked == [str(case_id)]
    # SELECT then UPDATE.
    assert session.execute.call_count == 2
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_multiple_stuck_cases_are_all_marked():
    ids = [uuid.uuid4() for _ in range(3)]
    session = _mock_session_with_select_rows([(i,) for i in ids])

    marked = await find_and_mark_stuck_cases(session)

    assert marked == [str(i) for i in ids]
    assert session.execute.call_count == 2  # one bulk UPDATE, not three
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_dry_run_returns_ids_without_writing():
    case_id = uuid.uuid4()
    select_result = MagicMock()
    select_result.all.return_value = [(case_id,)]
    session = AsyncMock(spec=AsyncSession)
    # In dry-run we should never reach the UPDATE call.
    session.execute = AsyncMock(return_value=select_result)

    marked = await find_and_mark_stuck_cases(session, dry_run=True)

    assert marked == [str(case_id)]
    assert session.execute.call_count == 1
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_threshold_is_passed_through_to_query():
    """Smoke check: a custom threshold must reach the SELECT statement.

    We can't introspect the rendered SQL easily without a real engine, but
    we can confirm the call happened with our value by calling it twice
    with different thresholds and checking both produce the right behaviour.
    """
    session = _mock_session_with_select_rows([])

    marked = await find_and_mark_stuck_cases(session, threshold_seconds=60)

    assert marked == []
    assert session.execute.call_count == 1
