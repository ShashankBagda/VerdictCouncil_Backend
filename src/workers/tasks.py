"""arq task wrappers for outbox-dispatched pipeline jobs.

Each task:
  1. Loads its `pipeline_jobs` row and no-ops if already `completed`.
  2. Delegates to the existing helper (`_run_case_pipeline`,
     `_run_whatif_scenario`, `_run_stability_computation`) — the
     helpers already own their own per-scenario/per-case status
     transitions and session lifecycle.
  3. Flips the outbox row to `completed` on success or `failed`
     (with attempts++ and error_message) on exception.

Tasks are idempotent: redelivery (from dispatcher retry or stuck-job
recovery) is safe because step 1 checks the outbox row's status
before doing any work.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from src.models.pipeline_job import PipelineJob, PipelineJobStatus, PipelineJobType
from src.services.database import async_session
from src.workers.outbox import mark_completed, mark_failed

logger = logging.getLogger(__name__)


async def _load_job(job_id: uuid.UUID) -> PipelineJob | None:
    async with async_session() as db:
        return await db.get(PipelineJob, job_id)


async def _complete(job_id: uuid.UUID) -> None:
    async with async_session() as db:
        await mark_completed(db, job_id=job_id)
        await db.commit()


async def _fail(job_id: uuid.UUID, exc: BaseException) -> None:
    async with async_session() as db:
        await mark_failed(db, job_id=job_id, error_message=f"{type(exc).__name__}: {exc}")
        await db.commit()


async def _run_with_outbox(
    job_id_str: str,
    expected_type: PipelineJobType,
    runner: Any,
) -> None:
    job_id = uuid.UUID(job_id_str)
    job = await _load_job(job_id)
    if job is None:
        logger.warning("pipeline_job %s not found — dispatcher/worker skew?", job_id)
        return
    if job.status == PipelineJobStatus.completed:
        return
    if job.job_type != expected_type:
        logger.error(
            "pipeline_job %s type mismatch: expected=%s actual=%s",
            job_id,
            expected_type.value,
            job.job_type.value,
        )
        return

    try:
        await runner(job)
    except Exception as exc:
        logger.exception("pipeline_job %s failed (type=%s)", job_id, expected_type.value)
        await _fail(job_id, exc)
        raise
    await _complete(job_id)


async def run_case_pipeline_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    from src.api.routes.cases import _run_case_pipeline

    await _run_with_outbox(
        job_id,
        PipelineJobType.case_pipeline,
        lambda job: _run_case_pipeline(job.case_id),
    )


async def run_whatif_scenario_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    from src.api.routes.what_if import _run_whatif_scenario

    await _run_with_outbox(
        job_id,
        PipelineJobType.whatif_scenario,
        lambda job: _run_whatif_scenario(job.target_id),
    )


async def run_stability_computation_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    from src.api.routes.what_if import _run_stability_computation

    await _run_with_outbox(
        job_id,
        PipelineJobType.stability_computation,
        lambda job: _run_stability_computation(job.target_id),
    )


TASK_BY_JOB_TYPE: dict[PipelineJobType, str] = {
    PipelineJobType.case_pipeline: run_case_pipeline_job.__name__,
    PipelineJobType.whatif_scenario: run_whatif_scenario_job.__name__,
    PipelineJobType.stability_computation: run_stability_computation_job.__name__,
}
