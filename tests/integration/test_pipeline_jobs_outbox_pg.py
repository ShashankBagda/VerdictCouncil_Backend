"""Integration tests for the pipeline_jobs outbox helpers against real Postgres.

Skipped in CI unless ``INTEGRATION_TESTS=1`` is set. Locally, run after
``make infra-up`` and ``make migrate`` so migration 0013 is applied.

These tests exercise the SQL the unit tests cannot cover:
  - `FOR UPDATE SKIP LOCKED` semantics of `claim_pending_jobs`.
  - `mark_dispatched` status transition.
  - `recover_stuck_jobs` threshold arithmetic.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

from src.models.case import Case, CaseDomain, CaseStatus
from src.models.pipeline_job import PipelineJob, PipelineJobStatus, PipelineJobType
from src.models.user import User, UserRole
from src.services.database import async_session
from src.workers.outbox import (
    claim_pending_jobs,
    enqueue_outbox_job,
    mark_completed,
    mark_dispatched,
    mark_failed,
    recover_stuck_jobs,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Integration tests require infrastructure (set INTEGRATION_TESTS=1)",
)


async def _make_user_and_case(session) -> Case:
    user = User(
        id=uuid.uuid4(),
        name="Outbox Test Judge",
        email=f"outbox-{uuid.uuid4()}@example.com",
        role=UserRole.judge,
        password_hash="x",
    )
    session.add(user)
    await session.flush()
    case = Case(
        id=uuid.uuid4(),
        domain=CaseDomain.traffic_violation,
        status=CaseStatus.processing,
        created_by=user.id,
    )
    session.add(case)
    await session.flush()
    return case


class TestOutboxHelpers:
    @pytest.mark.asyncio
    async def test_enqueue_inserts_pending_row(self):
        async with async_session() as session:
            case = await _make_user_and_case(session)
            job = await enqueue_outbox_job(
                session,
                case_id=case.id,
                job_type=PipelineJobType.case_pipeline,
            )
            await session.commit()

            try:
                refreshed = await session.get(PipelineJob, job.id)
                assert refreshed is not None
                assert refreshed.status == PipelineJobStatus.pending
                assert refreshed.job_type == PipelineJobType.case_pipeline
                assert refreshed.attempts == 0
                assert refreshed.dispatched_at is None
            finally:
                await session.delete(refreshed)
                await session.execute(text("DELETE FROM cases WHERE id = :id"), {"id": case.id})
                await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": case.created_by}
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_claim_and_mark_dispatched_flow(self):
        async with async_session() as session:
            case = await _make_user_and_case(session)
            job = await enqueue_outbox_job(
                session,
                case_id=case.id,
                job_type=PipelineJobType.case_pipeline,
            )
            await session.commit()

            try:
                claimed = await claim_pending_jobs(session, batch_size=10)
                claimed_ids = {row[0] for row in claimed}
                assert job.id in claimed_ids

                await mark_dispatched(session, job_ids=[job.id])
                await session.commit()

                refreshed = await session.get(PipelineJob, job.id)
                assert refreshed.status == PipelineJobStatus.dispatched
                assert refreshed.dispatched_at is not None
            finally:
                await session.execute(
                    text("DELETE FROM pipeline_jobs WHERE id = :id"), {"id": job.id}
                )
                await session.execute(text("DELETE FROM cases WHERE id = :id"), {"id": case.id})
                await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": case.created_by}
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_mark_completed_and_failed(self):
        async with async_session() as session:
            case = await _make_user_and_case(session)
            ok_job = await enqueue_outbox_job(
                session, case_id=case.id, job_type=PipelineJobType.case_pipeline
            )
            bad_job = await enqueue_outbox_job(
                session, case_id=case.id, job_type=PipelineJobType.case_pipeline
            )
            await session.commit()

            try:
                await mark_completed(session, job_id=ok_job.id)
                await mark_failed(session, job_id=bad_job.id, error_message="boom")
                await session.commit()

                ok = await session.get(PipelineJob, ok_job.id)
                bad = await session.get(PipelineJob, bad_job.id)
                assert ok.status == PipelineJobStatus.completed
                assert ok.completed_at is not None
                assert bad.status == PipelineJobStatus.failed
                assert bad.attempts == 1
                assert bad.error_message == "boom"
            finally:
                for jid in (ok_job.id, bad_job.id):
                    await session.execute(
                        text("DELETE FROM pipeline_jobs WHERE id = :id"), {"id": jid}
                    )
                await session.execute(text("DELETE FROM cases WHERE id = :id"), {"id": case.id})
                await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": case.created_by}
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_recover_stuck_reverts_old_dispatched_rows(self):
        async with async_session() as session:
            case = await _make_user_and_case(session)
            job = await enqueue_outbox_job(
                session, case_id=case.id, job_type=PipelineJobType.case_pipeline
            )
            # Manually flip to dispatched with a stale timestamp (2h ago).
            await session.execute(
                text(
                    """
                    UPDATE pipeline_jobs
                    SET status = 'dispatched',
                        dispatched_at = NOW() - INTERVAL '2 hours'
                    WHERE id = :id
                    """
                ),
                {"id": job.id},
            )
            await session.commit()

            try:
                recovered = await recover_stuck_jobs(session, threshold_secs=60)
                await session.commit()
                assert job.id in recovered

                refreshed = await session.get(PipelineJob, job.id)
                assert refreshed.status == PipelineJobStatus.pending
                assert refreshed.dispatched_at is None
            finally:
                await session.execute(
                    text("DELETE FROM pipeline_jobs WHERE id = :id"), {"id": job.id}
                )
                await session.execute(text("DELETE FROM cases WHERE id = :id"), {"id": case.id})
                await session.execute(
                    text("DELETE FROM users WHERE id = :id"), {"id": case.created_by}
                )
                await session.commit()

    @pytest.mark.asyncio
    async def test_claim_skips_rows_locked_by_other_tx(self):
        """FOR UPDATE SKIP LOCKED must hide rows locked by another session."""
        async with async_session() as s1:
            case = await _make_user_and_case(s1)
            job = await enqueue_outbox_job(
                s1, case_id=case.id, job_type=PipelineJobType.case_pipeline
            )
            await s1.commit()

            # s1 claims the row (holds the lock open).
            claim_a = await claim_pending_jobs(s1, batch_size=10)
            assert job.id in {row[0] for row in claim_a}

            # s2 must see zero pending rows because s1 holds the lock.
            try:
                async with async_session() as s2:
                    claim_b = await claim_pending_jobs(s2, batch_size=10)
                    assert job.id not in {row[0] for row in claim_b}
            finally:
                await s1.rollback()  # release lock
                async with async_session() as cleanup:
                    await cleanup.execute(
                        text("DELETE FROM pipeline_jobs WHERE id = :id"), {"id": job.id}
                    )
                    await cleanup.execute(text("DELETE FROM cases WHERE id = :id"), {"id": case.id})
                    await cleanup.execute(
                        text("DELETE FROM users WHERE id = :id"), {"id": case.created_by}
                    )
                    await cleanup.commit()
