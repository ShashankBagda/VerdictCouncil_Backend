"""Outbox dispatcher + stuck-job recovery loops.

Both loops are spawned from arq's `on_startup`, so one process runs
both the arq task executor and the dispatcher. Scaling horizontally
is safe: `FOR UPDATE SKIP LOCKED` in `claim_pending_jobs` guarantees
only one dispatcher instance ever claims a given row.

Failure modes:
  - arq enqueue raises: the SELECT-FOR-UPDATE tx is rolled back, row
    locks release, rows stay `pending`, next tick retries. arq tasks
    are idempotent so duplicate enqueues are safe even if the Redis
    side succeeded on a prior attempt.
  - Worker crashes between `mark_dispatched` and `mark_completed`:
    the row sits in `dispatched` forever until `recover_stuck_jobs`
    flips it back to `pending` after the threshold.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.pipeline_job import PipelineJobType
from src.services.database import async_session
from src.workers.outbox import (
    claim_pending_jobs,
    mark_dispatched,
    recover_stuck_jobs,
)
from src.workers.tasks import TASK_BY_JOB_TYPE

logger = logging.getLogger(__name__)

# --- tunables ---------------------------------------------------------------
DISPATCH_POLL_INTERVAL_SECONDS = 1.0
DISPATCH_BATCH_SIZE = 10
# After 20 min in `dispatched`, we assume the worker crashed. Mesh
# pipeline tops out ~5–7 min, so 20 min leaves generous headroom for
# contention-induced slowdowns.
STUCK_THRESHOLD_SECONDS = 20 * 60
STUCK_RECOVERY_INTERVAL_SECONDS = 60.0
# Linear sleep grows after consecutive errors to avoid thrashing the
# DB during an outage. Capped so we still notice recovery quickly.
ERROR_BACKOFF_BASE_SECONDS = 2.0
ERROR_BACKOFF_MAX_SECONDS = 30.0


async def _dispatch_batch(db: AsyncSession, arq_redis: ArqRedis) -> int:
    """Claim → enqueue → mark_dispatched → commit. Returns jobs dispatched.

    Holds row locks for the entire cycle: enqueue failure rolls the
    tx back, releasing locks, and the next tick re-attempts.
    """
    claimed = await claim_pending_jobs(db, batch_size=DISPATCH_BATCH_SIZE)
    if not claimed:
        return 0

    enqueued_ids: list[uuid.UUID] = []
    try:
        for job_id, job_type_str, _case_id, _target_id in claimed:
            job_type = PipelineJobType(job_type_str)
            task_name = TASK_BY_JOB_TYPE[job_type]
            # _job_id pins the arq job key to our outbox UUID so arq
            # deduplicates re-enqueues of the same row (e.g. on retry
            # after a partial-batch rollback).
            await arq_redis.enqueue_job(task_name, str(job_id), _job_id=str(job_id))
            enqueued_ids.append(job_id)
    except Exception:
        # Enqueue failed partway. Roll back the SELECT FOR UPDATE so
        # locks release and all claimed rows revert to `pending`. The
        # rows we already enqueued have a fixed _job_id so arq
        # deduplicates them on the next dispatch cycle.
        await db.rollback()
        logger.exception("arq enqueue failed; %d rows reverted", len(claimed))
        raise

    await mark_dispatched(db, job_ids=enqueued_ids)
    await db.commit()
    return len(enqueued_ids)


async def dispatcher_loop(arq_redis: ArqRedis) -> None:
    """Long-running dispatch loop. Polls for pending jobs, enqueues into arq."""
    consecutive_errors = 0
    while True:
        try:
            async with async_session() as db:
                dispatched = await _dispatch_batch(db, arq_redis)
            consecutive_errors = 0
            if dispatched == 0:
                await asyncio.sleep(DISPATCH_POLL_INTERVAL_SECONDS)
            # If we dispatched a full batch, loop immediately to drain the
            # backlog — no sleep.
        except asyncio.CancelledError:
            raise
        except Exception:
            consecutive_errors += 1
            sleep_s = min(
                ERROR_BACKOFF_BASE_SECONDS * (2 ** (consecutive_errors - 1)),
                ERROR_BACKOFF_MAX_SECONDS,
            )
            logger.exception(
                "dispatcher_loop tick failed (consecutive=%d), sleeping %.1fs",
                consecutive_errors,
                sleep_s,
            )
            await asyncio.sleep(sleep_s)


async def stuck_recovery_loop() -> None:
    """Long-running loop that flips stuck `dispatched` rows back to `pending`."""
    while True:
        try:
            async with async_session() as db:
                recovered = await recover_stuck_jobs(db, threshold_secs=STUCK_THRESHOLD_SECONDS)
                await db.commit()
            if recovered:
                logger.warning(
                    "stuck_recovery: reverted %d dispatched→pending: %s",
                    len(recovered),
                    [str(jid) for jid in recovered],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("stuck_recovery_loop tick failed")
        await asyncio.sleep(STUCK_RECOVERY_INTERVAL_SECONDS)


async def startup(ctx: dict[str, Any]) -> None:
    """arq on_startup hook — spawn dispatcher + recovery as background tasks.

    Both tasks run for the lifetime of the worker process and are
    cancelled by arq during shutdown.
    """
    from src.pipeline.observability import configure_mlflow

    configure_mlflow()

    arq_redis: ArqRedis = ctx["redis"]
    ctx["_dispatcher_task"] = asyncio.create_task(dispatcher_loop(arq_redis))
    ctx["_stuck_recovery_task"] = asyncio.create_task(stuck_recovery_loop())
    logger.info("pipeline outbox dispatcher + stuck-recovery loops started")


async def shutdown(ctx: dict[str, Any]) -> None:
    """arq on_shutdown hook — cancel dispatcher + recovery cleanly."""
    for key in ("_dispatcher_task", "_stuck_recovery_task"):
        task: asyncio.Task[None] | None = ctx.get(key)
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("error cancelling %s", key)
    logger.info("pipeline outbox dispatcher + stuck-recovery loops stopped")
