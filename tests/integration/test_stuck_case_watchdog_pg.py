"""Integration tests for src.services.stuck_case_watchdog against real Postgres.

Skipped in CI unless ``INTEGRATION_TESTS=1`` is set. Locally, run after
``make infra-up`` and ``make migrate`` so the DB schema is current.

These tests exercise the SQL semantics that the unit tests can't cover:
the ``WHERE`` clause's status filter, the ``COALESCE(updated_at, created_at)``
fallback for fresh rows, and the threshold cutoff arithmetic.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from src.models.case import Case, CaseDomain, CaseStatus
from src.models.user import User, UserRole
from src.services.database import async_session
from src.services.stuck_case_watchdog import find_and_mark_stuck_cases

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Integration tests require infrastructure (set INTEGRATION_TESTS=1)",
)


async def _make_user(session) -> User:
    user = User(
        id=uuid.uuid4(),
        name="Watchdog Test Judge",
        email=f"watchdog-{uuid.uuid4()}@example.com",
        role=UserRole.judge,
        password_hash="x",
    )
    session.add(user)
    await session.flush()
    return user


async def _make_case(
    session,
    user_id: uuid.UUID,
    *,
    status: CaseStatus,
    created_at: datetime,
    updated_at: datetime | None = None,
) -> Case:
    case = Case(
        id=uuid.uuid4(),
        domain=CaseDomain.traffic_violation,
        status=status,
        created_by=user_id,
        created_at=created_at,
        updated_at=updated_at,
    )
    session.add(case)
    await session.flush()
    return case


class TestStuckCaseWatchdogIntegration:
    @pytest.mark.asyncio
    async def test_marks_case_stuck_via_created_at_when_updated_at_is_null(self):
        """A fresh `processing` case has updated_at IS NULL — must still be caught."""
        async with async_session() as session:
            user = await _make_user(session)
            old = datetime.now(UTC) - timedelta(hours=1)
            stuck = await _make_case(session, user.id, status=CaseStatus.processing, created_at=old)
            await session.commit()

            try:
                marked = await find_and_mark_stuck_cases(session, threshold_seconds=1800)
                assert str(stuck.id) in marked

                refreshed = await session.get(Case, stuck.id)
                assert refreshed.status == CaseStatus.failed_retryable
            finally:
                await session.execute(text("DELETE FROM cases WHERE id = :i"), {"i": stuck.id})
                await session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
                await session.commit()

    @pytest.mark.asyncio
    async def test_recent_processing_case_is_left_alone(self):
        async with async_session() as session:
            user = await _make_user(session)
            recent = datetime.now(UTC) - timedelta(seconds=30)
            fresh = await _make_case(
                session, user.id, status=CaseStatus.processing, created_at=recent
            )
            await session.commit()

            try:
                marked = await find_and_mark_stuck_cases(session, threshold_seconds=1800)
                assert str(fresh.id) not in marked

                refreshed = await session.get(Case, fresh.id)
                assert refreshed.status == CaseStatus.processing
            finally:
                await session.execute(text("DELETE FROM cases WHERE id = :i"), {"i": fresh.id})
                await session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
                await session.commit()

    @pytest.mark.asyncio
    async def test_old_non_processing_case_is_left_alone(self):
        """A `decided` case sitting around for hours is not 'stuck'."""
        async with async_session() as session:
            user = await _make_user(session)
            old = datetime.now(UTC) - timedelta(hours=2)
            done = await _make_case(session, user.id, status=CaseStatus.decided, created_at=old)
            await session.commit()

            try:
                marked = await find_and_mark_stuck_cases(session, threshold_seconds=1800)
                assert str(done.id) not in marked

                refreshed = await session.get(Case, done.id)
                assert refreshed.status == CaseStatus.decided
            finally:
                await session.execute(text("DELETE FROM cases WHERE id = :i"), {"i": done.id})
                await session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
                await session.commit()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_mutate(self):
        async with async_session() as session:
            user = await _make_user(session)
            old = datetime.now(UTC) - timedelta(hours=1)
            stuck = await _make_case(session, user.id, status=CaseStatus.processing, created_at=old)
            await session.commit()

            try:
                marked = await find_and_mark_stuck_cases(
                    session, threshold_seconds=1800, dry_run=True
                )
                assert str(stuck.id) in marked

                refreshed = await session.get(Case, stuck.id)
                assert refreshed.status == CaseStatus.processing  # unchanged
            finally:
                await session.execute(text("DELETE FROM cases WHERE id = :i"), {"i": stuck.id})
                await session.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
                await session.commit()
