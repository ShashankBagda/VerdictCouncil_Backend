"""Unit tests for arq task wrappers in src.workers.tasks.

These cover the idempotency + status-transition contract without
requiring Postgres or Redis:
  - task no-ops when job row is already `completed`
  - task calls `mark_completed` on success
  - task calls `mark_failed` (attempts++) on exception and re-raises
  - task aborts with a log if job_type mismatches the arq function
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.models.pipeline_job import PipelineJob, PipelineJobStatus, PipelineJobType
from src.workers import tasks


def _make_job(
    *,
    job_type: PipelineJobType = PipelineJobType.case_pipeline,
    status: PipelineJobStatus = PipelineJobStatus.dispatched,
) -> PipelineJob:
    job = PipelineJob(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        job_type=job_type,
        target_id=None,
        status=status,
        attempts=0,
    )
    return job


@pytest.mark.asyncio
async def test_task_noops_when_job_already_completed():
    job = _make_job(status=PipelineJobStatus.completed)

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()) as mock_complete,
        patch.object(tasks, "_fail", new=AsyncMock()) as mock_fail,
        patch("src.api.routes.cases._run_case_pipeline", new=AsyncMock()) as mock_run,
    ):
        await tasks.run_case_pipeline_job({}, str(job.id))

    mock_run.assert_not_called()
    mock_complete.assert_not_called()
    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_task_marks_completed_on_success():
    job = _make_job()

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()) as mock_complete,
        patch.object(tasks, "_fail", new=AsyncMock()) as mock_fail,
        patch("src.api.routes.cases._run_case_pipeline", new=AsyncMock()) as mock_run,
    ):
        await tasks.run_case_pipeline_job({}, str(job.id))

    mock_run.assert_awaited_once_with(job.case_id)
    mock_complete.assert_awaited_once_with(job.id)
    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_task_marks_failed_and_reraises_on_exception():
    job = _make_job()
    boom = RuntimeError("pipeline explode")

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()) as mock_complete,
        patch.object(tasks, "_fail", new=AsyncMock()) as mock_fail,
        patch(
            "src.api.routes.cases._run_case_pipeline",
            new=AsyncMock(side_effect=boom),
        ),
        pytest.raises(RuntimeError, match="pipeline explode"),
    ):
        await tasks.run_case_pipeline_job({}, str(job.id))

    mock_fail.assert_awaited_once_with(job.id, boom)
    mock_complete.assert_not_called()


@pytest.mark.asyncio
async def test_task_aborts_on_job_type_mismatch():
    # Stability function invoked against a case-pipeline row → must not run
    # either helper, and must not flip the row's status.
    job = _make_job(job_type=PipelineJobType.case_pipeline)

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()) as mock_complete,
        patch.object(tasks, "_fail", new=AsyncMock()) as mock_fail,
        patch("src.api.routes.what_if._run_stability_computation", new=AsyncMock()) as mock_run,
    ):
        await tasks.run_stability_computation_job({}, str(job.id))

    mock_run.assert_not_called()
    mock_complete.assert_not_called()
    mock_fail.assert_not_called()


@pytest.mark.asyncio
async def test_task_noops_when_job_row_missing():
    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=None)),
        patch.object(tasks, "_complete", new=AsyncMock()) as mock_complete,
        patch.object(tasks, "_fail", new=AsyncMock()) as mock_fail,
        patch("src.api.routes.cases._run_case_pipeline", new=AsyncMock()) as mock_run,
    ):
        await tasks.run_case_pipeline_job({}, str(uuid.uuid4()))

    mock_run.assert_not_called()
    mock_complete.assert_not_called()
    mock_fail.assert_not_called()
