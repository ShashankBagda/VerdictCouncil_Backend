"""arq task wrappers for outbox-dispatched pipeline jobs.

Each task:
  1. Loads its `pipeline_jobs` row and no-ops if already `completed`.
  2. Re-establishes OTEL trace context from `job.traceparent` so the
     worker's spans (and downstream LangSmith run) inherit the API
     request's trace_id (Sprint 2 2.C1.4).
  3. Delegates to the existing helper (`_run_case_pipeline`,
     `_run_whatif_scenario`, `_run_stability_computation`) — the
     helpers already own their own per-scenario/per-case status
     transitions and session lifecycle.
  4. Flips the outbox row to `completed` on success or `failed`
     (with attempts++ and error_message) on exception.

Tasks are idempotent: redelivery (from dispatcher retry or stuck-job
recovery) is safe because step 1 checks the outbox row's status
before doing any work.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import nullcontext
from typing import Any

from opentelemetry import trace

from src.api.trace_propagation import (
    parse_traceparent,
    remote_span_from_traceparent,
)
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

    # Re-establish OTEL context from the API request that enqueued this job
    # so the worker's spans and the downstream LangSmith run inherit the
    # original trace_id. Legacy queued jobs (pre-0025 migration) lack
    # `traceparent`; we log once and run without trace continuity.
    trace_id: str | None = None
    parent_span = remote_span_from_traceparent(job.traceparent)
    if parent_span is None:
        if job.traceparent:
            logger.warning(
                "pipeline_job %s has malformed traceparent=%r; running without trace continuity",
                job_id,
                job.traceparent,
            )
        context_cm = nullcontext()
    else:
        trace_id = parse_traceparent(job.traceparent).get("trace_id")
        context_cm = trace.use_span(parent_span, end_on_exit=False)

    try:
        with context_cm:
            await runner(job, trace_id=trace_id)
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
        lambda job, *, trace_id=None: _run_case_pipeline(job.case_id, trace_id=trace_id),
    )


async def run_whatif_scenario_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    from src.api.routes.what_if import _run_whatif_scenario

    await _run_with_outbox(
        job_id,
        PipelineJobType.whatif_scenario,
        lambda job, *, trace_id=None: _run_whatif_scenario(job.target_id, trace_id=trace_id),
    )


async def run_stability_computation_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    from src.api.routes.what_if import _run_stability_computation

    await _run_with_outbox(
        job_id,
        PipelineJobType.stability_computation,
        lambda job, *, trace_id=None: _run_stability_computation(job.target_id, trace_id=trace_id),
    )


async def run_gate_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    async def _runner(job: PipelineJob, *, trace_id: str | None = None) -> None:
        payload = job.payload or {}
        if payload.get("resume_action"):
            # Sprint 4 4.A3.5/6 cutover path: drive Command(resume=...) against
            # the saver-checkpointed thread.
            await _run_gate_via_resume(job, trace_id=trace_id)
        else:
            # Pre-cutover queued jobs (no resume_action) — legacy run_gate path.
            await _run_gate_via_legacy(job, trace_id=trace_id)

    await _run_with_outbox(job_id, PipelineJobType.gate_run, _runner)


async def _run_gate_via_resume(job: PipelineJob, *, trace_id: str | None) -> None:
    """Sprint 4 cutover gate runner.

    Translates the /respond job payload into ``Command(resume=...)``,
    invokes the saver-checkpointed graph, persists the resulting case
    state, and publishes either an InterruptEvent (next gate paused)
    or the legacy terminal SSE close event (run reached END or halt).
    """
    from datetime import UTC, datetime

    from src.api.schemas.pipeline_events import PipelineProgressEvent
    from src.db.persist_case_results import persist_case_results
    from src.models.case import Case
    from src.pipeline.graph.resume import drive_resume
    from src.pipeline.graph.runner import GraphPipelineRunner
    from src.services.database import async_session
    from src.services.pipeline_events import publish_interrupt, publish_progress
    from src.shared.case_state import CaseStatusEnum
    from src.tools.exceptions import RetiredDomainError

    payload = job.payload or {}
    case_id = job.case_id
    config: dict[str, Any] = {"configurable": {"thread_id": str(case_id)}}

    # D2 invariant: re-read domain from live DB to catch retirements between
    # gate pause and resume. The saver-side domain_vector_store_id is stale
    # the moment an admin retires the domain, so we refuse the resume and
    # mark the case retryable rather than running with a dead vector store.
    async with async_session() as db:
        from sqlalchemy import select as _select
        from sqlalchemy.orm import joinedload as _joinedload

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
            raise RetiredDomainError(
                f"Domain {live_case.domain_ref.code} retired mid-case; aborting gate resume"
            )

    runner = GraphPipelineRunner()

    # Stamp metadata for LangSmith trace continuity (matches runner._invoke).
    from src.shared.config import settings

    metadata: dict[str, str] = {
        "env": settings.app_env,
        "case_id": str(case_id),
    }
    if trace_id:
        metadata["trace_id"] = trace_id
    invoke_config = {**config, "metadata": metadata}

    outcome, gate, ipayload = await drive_resume(runner._graph, invoke_config, payload)

    # Pull the post-resume CaseState from the saver and reflect the run's
    # logical status. The graph nodes write status="processing" everywhere;
    # only the worker knows whether the run has paused or terminated.
    snapshot = await runner._graph.aget_state(config)
    final_state = snapshot.values["case"]

    if outcome == "chat":
        # Q1.11 chat-steering: synthesis (or another phase, eventually)
        # paused inside an `ask_judge` tool call. The tool already
        # published the question event from inside its body, so the chat
        # panel will render. We must NOT override case.status (the API
        # already set it to `processing` when enqueueing this job) and
        # we must NOT emit a terminal progress event — the SSE stream
        # has to stay alive for the chat reply to flow back through.
        # The post-reply gate3 pause is detected and persisted by
        # `_handle_message_resume` in the API layer.
        logger.info(
            "drive_resume returned chat outcome for case_id=%s — leaving "
            "status=processing while chat panel awaits judge reply",
            case_id,
        )
        return

    if outcome == "interrupt":
        gate_num = int(gate[-1])  # type: ignore[index]
        gate_state_payload = {
            "current_gate": gate_num,
            "awaiting_review": True,
            "rerun_agent": None,
        }
        async with async_session() as db:
            await persist_case_results(
                db, case_id, final_state, gate_state_payload=gate_state_payload
            )
        # publish_interrupt UPSERTs case.status = awaiting_review_gateN
        # and writes case.gate_state. Order matters: it runs after
        # persist_case_results so the legacy compat status is the final write.
        if ipayload is not None and gate is not None:
            await publish_interrupt(case_id, gate, ipayload)
        # Legacy SSE close event for clients still keying off
        # `agent=pipeline + phase=awaiting_review`.
        await publish_progress(
            PipelineProgressEvent(
                case_id=case_id,
                agent="pipeline",
                phase="awaiting_review",
                ts=datetime.now(UTC),
                detail={"gate": gate, "stopped_at": f"awaiting_review_{gate}"},
            )
        )
        return

    # outcome == "terminal" — graph reached END or halted. Reflect that in
    # the persisted CaseState before persistence so the legacy `closed` /
    # `failed` filters in the case-list UI work without a follow-up update.
    halt = snapshot.values.get("halt") or {}
    if halt:
        final_state = final_state.model_copy(update={"status": CaseStatusEnum.failed})
    else:
        final_state = final_state.model_copy(update={"status": CaseStatusEnum.closed})

    async with async_session() as db:
        await persist_case_results(db, case_id, final_state)

    await publish_progress(
        PipelineProgressEvent(
            case_id=case_id,
            agent="pipeline",
            phase="terminal",
            ts=datetime.now(UTC),
            detail={
                "reason": halt.get("reason", "completed") if halt else "completed",
                "stopped_at": halt.get("gate") if halt else "end",
            },
        )
    )


async def _run_gate_via_legacy(job: PipelineJob, *, trace_id: str | None) -> None:
    """Pre-cutover gate runner kept for in-flight queued jobs.

    Any job enqueued before the 4.A3.5 cutover lacks ``resume_action``
    in its payload. Those jobs continue to drive the legacy
    ``runner.run_gate(state, gate_name, start_agent=..., extra_instructions=...)``
    path so the pipeline does not regress mid-rollout. New jobs from
    ``/respond`` always carry ``resume_action`` and route through
    ``_run_gate_via_resume``.
    """
    from datetime import UTC, datetime

    from src.api.schemas.pipeline_events import PipelineProgressEvent
    from src.db.persist_case_results import persist_case_results
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

    runner = GraphPipelineRunner()
    config: dict[str, Any] = {"configurable": {"thread_id": str(case_id)}}
    state: CaseState | None = None
    if gate_num > 1:
        snapshot = await runner._graph.aget_state(config)
        snap_values = getattr(snapshot, "values", None) or {}
        state = snap_values.get("case")

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
            raise RetiredDomainError(
                f"Domain {live_case.domain_ref.code} retired mid-case; aborting gate resume"
            )
        state = state.model_copy(
            update={"domain_vector_store_id": live_case.domain_ref.vector_store_id}
        )

    state = state.model_copy(update={"status": CaseStatusEnum.processing})

    final_state = await runner.run_gate(
        state,
        gate_name,
        start_agent=start_agent,
        extra_instructions=instructions,
        trace_id=trace_id,
    )

    gate_state_payload = {
        "current_gate": gate_num,
        "awaiting_review": True,
        "rerun_agent": None,
    }

    async with async_session() as db:
        if hasattr(runner, "_document_pages_buffer") and runner._document_pages_buffer:
            from sqlalchemy import update as sa_update

            from src.models.case import Document

            for file_id, pages in runner._document_pages_buffer.items():
                await db.execute(
                    sa_update(Document)
                    .where(Document.openai_file_id == file_id)
                    .values(pages=pages)
                )

        await persist_case_results(db, case_id, final_state, gate_state_payload=gate_state_payload)

    await publish_progress(
        PipelineProgressEvent(
            case_id=case_id,
            agent="pipeline",
            phase="awaiting_review",
            ts=datetime.now(UTC),
            detail={"gate": gate_name},
        )
    )


async def run_intake_extraction_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    async def _runner(job: PipelineJob, *, trace_id: str | None = None) -> None:  # noqa: ARG001
        from src.services.database import async_session
        from src.services.intake_extraction import run_intake_extraction

        correction = (job.payload or {}).get("correction")
        async with async_session() as db:
            await run_intake_extraction(db, case_id=job.case_id, correction=correction)

    await _run_with_outbox(job_id, PipelineJobType.intake_extraction, _runner)


async def run_document_parse_job(ctx: dict[str, Any], job_id: str) -> None:  # noqa: ARG001
    """Q2.1 — parse a single uploaded document and cache the result.

    Job carries the document UUID in `target_id`. On success the
    worker writes `documents.parsed_text`. On `parse_document`
    failure the column stays NULL and the runner-side fallback
    (Q2.2) re-parses lazily; the outbox row is marked failed so
    operators can re-enqueue if they want.
    """

    async def _runner(job: PipelineJob, *, trace_id: str | None = None) -> None:  # noqa: ARG001
        from src.services.database import async_session
        from src.services.document_parse import parse_and_persist_document

        if job.target_id is None:
            logger.warning("document_parse job %s has no target_id; skipping", job.id)
            return
        async with async_session() as db:
            await parse_and_persist_document(db, document_id=job.target_id)

    await _run_with_outbox(job_id, PipelineJobType.document_parse, _runner)


TASK_BY_JOB_TYPE: dict[PipelineJobType, str] = {
    PipelineJobType.case_pipeline: run_case_pipeline_job.__name__,
    PipelineJobType.whatif_scenario: run_whatif_scenario_job.__name__,
    PipelineJobType.stability_computation: run_stability_computation_job.__name__,
    PipelineJobType.gate_run: run_gate_job.__name__,
    PipelineJobType.intake_extraction: run_intake_extraction_job.__name__,
    PipelineJobType.document_parse: run_document_parse_job.__name__,
}
