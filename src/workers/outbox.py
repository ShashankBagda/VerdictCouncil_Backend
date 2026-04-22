"""Outbox DB operations for `pipeline_jobs`.

Writers call `enqueue_outbox_job` inside the same transaction that
flips case / scenario / stability status — the INSERT and the state
flip share a tx, so a post-commit crash cannot leave the state flipped
without a matching outbox row. The dispatcher reads rows via
`claim_pending_jobs` (FOR UPDATE SKIP LOCKED) and flips them to
`dispatched` after successful enqueue. Stuck `dispatched` rows (worker
crashed between claim and completion) are flipped back to `pending` by
`recover_stuck_jobs`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.pipeline_job import PipelineJob, PipelineJobStatus, PipelineJobType


async def enqueue_outbox_job(
    db: AsyncSession,
    *,
    case_id: uuid.UUID,
    job_type: PipelineJobType,
    target_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> PipelineJob:
    """Insert a `pending` pipeline_jobs row. Caller owns the commit.

    This MUST run inside the same transaction that flips the
    corresponding case / scenario / stability status row — otherwise
    the outbox guarantee (state flip ↔ dispatch) is broken.
    """
    job = PipelineJob(
        case_id=case_id,
        job_type=job_type,
        target_id=target_id,
        payload=payload,
        status=PipelineJobStatus.pending,
    )
    db.add(job)
    await db.flush()
    return job


# Claim a batch of pending jobs with row-level locks so concurrent
# dispatcher instances (rolling deploy) never double-dispatch. Rows
# stay locked until the caller commits or rolls back.
_CLAIM_SQL = text(
    """
    SELECT id, job_type, case_id, target_id
    FROM pipeline_jobs
    WHERE status = 'pending'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT :batch_size
    """
)


async def claim_pending_jobs(
    db: AsyncSession,
    *,
    batch_size: int,
) -> list[tuple[uuid.UUID, str, uuid.UUID, uuid.UUID | None]]:
    """Lock and return up to `batch_size` pending rows. Commit or
    rollback released the locks — dispatcher commits after flipping
    claimed rows to `dispatched`."""
    result = await db.execute(_CLAIM_SQL, {"batch_size": batch_size})
    return [(row[0], row[1], row[2], row[3]) for row in result.all()]


_MARK_DISPATCHED_SQL = text(
    """
    UPDATE pipeline_jobs
    SET status = 'dispatched', dispatched_at = NOW()
    WHERE id = ANY(:ids) AND status = 'pending'
    """
)


async def mark_dispatched(
    db: AsyncSession,
    *,
    job_ids: list[uuid.UUID],
) -> None:
    """Flip claimed rows to `dispatched` in the same tx that locked them."""
    if not job_ids:
        return
    await db.execute(_MARK_DISPATCHED_SQL, {"ids": job_ids})


_MARK_COMPLETED_SQL = text(
    """
    UPDATE pipeline_jobs
    SET status = 'completed', completed_at = NOW(), error_message = NULL
    WHERE id = :job_id
    """
)


async def mark_completed(db: AsyncSession, *, job_id: uuid.UUID) -> None:
    await db.execute(_MARK_COMPLETED_SQL, {"job_id": job_id})


_MARK_FAILED_SQL = text(
    """
    UPDATE pipeline_jobs
    SET status = 'failed',
        attempts = attempts + 1,
        error_message = :error_message,
        completed_at = NOW()
    WHERE id = :job_id
    """
)


async def mark_failed(
    db: AsyncSession,
    *,
    job_id: uuid.UUID,
    error_message: str,
) -> None:
    await db.execute(
        _MARK_FAILED_SQL,
        {"job_id": job_id, "error_message": error_message[:1000]},
    )


# Workers can crash between claim and status update — rows stay in
# `dispatched` forever without this recovery tick. The threshold must
# exceed any legitimate pipeline runtime; the 9-agent mesh tops out
# around 5–7 min wall clock, so 20 min is conservative headroom.
_RECOVER_STUCK_SQL = text(
    """
    UPDATE pipeline_jobs
    SET status = 'pending', dispatched_at = NULL
    WHERE status = 'dispatched'
      AND dispatched_at < NOW() - make_interval(secs => :threshold_secs)
    RETURNING id
    """
)


async def recover_stuck_jobs(
    db: AsyncSession,
    *,
    threshold_secs: int,
) -> list[uuid.UUID]:
    """Flip `dispatched` rows older than threshold back to `pending`."""
    result = await db.execute(_RECOVER_STUCK_SQL, {"threshold_secs": threshold_secs})
    return [row[0] for row in result.all()]
