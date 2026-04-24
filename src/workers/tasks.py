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


async def run_gate_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    async def _runner(job: PipelineJob) -> None:
        from datetime import UTC, datetime

        from src.api.schemas.pipeline_events import PipelineProgressEvent
        from src.db.persist_case_results import persist_case_results
        from src.db.pipeline_state import (
            CheckpointCorruptError,
            CheckpointSchemaMismatchError,
            load_case_state,
            persist_case_state,
        )
        from src.models.case import Case
        from src.pipeline.graph.runner import GraphPipelineRunner
        from src.services.database import async_session
        from src.services.pipeline_events import publish_progress
        from src.shared.case_state import CaseState, CaseStatusEnum

        payload = job.payload or {}
        gate_name = payload.get("gate_name")
        start_agent = payload.get("start_agent")
        instructions = payload.get("instructions")

        if not gate_name:
            raise ValueError(f"gate_run job {job.id} missing gate_name in payload")

        case_id = job.case_id
        gate_num = int(gate_name[-1])
        prev_gate_num = gate_num - 1

        # Load previous gate checkpoint; fall back to minimal state on any failure
        state: CaseState | None = None
        if prev_gate_num >= 1:
            prev_run_id = f"{case_id}-gate{prev_gate_num}"
            try:
                async with async_session() as db:
                    state = await load_case_state(db, case_id=case_id, run_id=prev_run_id)
            except (CheckpointSchemaMismatchError, CheckpointCorruptError):
                logger.warning(
                    "gate checkpoint for case_id=%s gate%s unreadable; reinitialising from DB",
                    case_id,
                    prev_gate_num,
                )

        if state is None:
            async with async_session() as db:
                from sqlalchemy import select as _select
                from sqlalchemy.orm import joinedload as _joinedload

                case_result = await db.execute(
                    _select(Case).where(Case.id == case_id).options(_joinedload(Case.domain_ref))
                )
                case = case_result.scalar_one_or_none()
                if case is None:
                    raise ValueError(f"Case {case_id} not found for gate_run job {job.id}")
                state = CaseState(
                    case_id=str(case_id),
                    domain=case.domain.value if case.domain else None,
                    status=CaseStatusEnum.processing,
                )

        # D2: Always re-read domain from live DB to catch retirements after gate 1
        async with async_session() as db:
            from sqlalchemy import select as _select
            from sqlalchemy.orm import joinedload as _joinedload

            from src.tools.exceptions import RetiredDomainError

            case_result = await db.execute(
                _select(Case).where(Case.id == case_id).options(_joinedload(Case.domain_ref))
            )
            live_case = case_result.scalar_one_or_none()
            if live_case is None:
                raise ValueError(f"Case {case_id} disappeared before gate resume")

            if live_case.domain_id is None or live_case.domain_ref is None:
                raise RetiredDomainError("Case has no linked domain; cannot resume")

            if not live_case.domain_ref.is_active or not live_case.domain_ref.vector_store_id:
                live_case.status_value = "failed_retryable"
                await db.commit()
                raise RetiredDomainError(f"Domain {live_case.domain_ref.code} retired mid-case; aborting gate resume")

            # Always overwrite from live DB — never use stale checkpoint value
            state = state.model_copy(update={"domain_vector_store_id": live_case.domain_ref.vector_store_id})

        # Force status to processing before handing to run_gate
        state = state.model_copy(update={"status": CaseStatusEnum.processing})

        runner = GraphPipelineRunner()
        final_state = await runner.run_gate(
            state,
            gate_name,
            start_agent=start_agent,
            extra_instructions=instructions,
        )

        gate_state_payload = {
            "current_gate": gate_num,
            "awaiting_review": True,
            "rerun_agent": None,
        }
        gate_run_id = f"{case_id}-{gate_name}"

        async with async_session() as db:
            # Flush parsed document pages if any parse_document tool calls ran (US-008)
            if hasattr(runner, "_document_pages_buffer") and runner._document_pages_buffer:
                from sqlalchemy import update as sa_update

                from src.models.case import Document

                for file_id, pages in runner._document_pages_buffer.items():
                    await db.execute(sa_update(Document).where(Document.openai_file_id == file_id).values(pages=pages))

            await persist_case_results(db, case_id, final_state, gate_state_payload=gate_state_payload)

        async with async_session() as db:
            await persist_case_state(
                db,
                case_id=case_id,
                run_id=gate_run_id,
                agent_name="gate_complete",
                state=final_state,
            )

        event = PipelineProgressEvent(
            case_id=case_id,
            agent="pipeline",
            phase="awaiting_review",
            ts=datetime.now(UTC),
            detail={"gate": gate_name},
        )
        await publish_progress(event)

    await _run_with_outbox(job_id, PipelineJobType.gate_run, _runner)


async def run_intake_extraction_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    async def _runner(job: PipelineJob) -> None:
        from src.services.database import async_session
        from src.services.intake_extraction import run_intake_extraction

        correction = (job.payload or {}).get("correction")
        async with async_session() as db:
            await run_intake_extraction(db, case_id=job.case_id, correction=correction)

    await _run_with_outbox(job_id, PipelineJobType.intake_extraction, _runner)


TASK_BY_JOB_TYPE: dict[PipelineJobType, str] = {
    PipelineJobType.case_pipeline: run_case_pipeline_job.__name__,
    PipelineJobType.whatif_scenario: run_whatif_scenario_job.__name__,
    PipelineJobType.stability_computation: run_stability_computation_job.__name__,
    PipelineJobType.gate_run: run_gate_job.__name__,
    PipelineJobType.intake_extraction: run_intake_extraction_job.__name__,
}
