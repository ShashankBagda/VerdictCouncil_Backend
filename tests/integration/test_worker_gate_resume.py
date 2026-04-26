"""Sprint 4 4.A3.5/4.A3.6 — `_run_gate_via_resume` worker glue contract.

Locks the call sequence the Sprint-4 cutover worker performs around the
LangGraph saver. Three things matter:

1. Persist-then-publish ordering: ``persist_case_results`` writes
   ``case.status`` from the saver's CaseState (still ``"processing"``
   on a freshly-paused thread). ``publish_interrupt`` then UPSERTs
   the legacy ``awaiting_review_gateN`` status. If the order ever
   reverses the case row will end up at ``processing`` after the
   resume — silently — and the case-list filter will hide it.
2. Halt → ``failed`` status: when the run terminates with a halt the
   worker stamps ``CaseStatusEnum.failed`` onto the persisted state
   so the case-list "halted" surface still works without a follow-up
   write. Non-halt terminal goes to ``closed``.
3. Domain-retirement re-check: an admin retiring the case's domain
   between gate pause and resume must abort the resume cleanly
   rather than re-running with a stale vector store id.

The graph + saver are stubbed so the test runs without Postgres or
Redis; what's exercised is the worker's own glue.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.case import Case
from src.models.pipeline_job import PipelineJob, PipelineJobStatus, PipelineJobType
from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum
from src.tools.exceptions import RetiredDomainError
from src.workers import tasks

# ---------------------------------------------------------------------------
# Fixtures — minimal mocks that mirror the legacy test's shape
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _fake_session_cm(db_mock):
    yield db_mock


def _make_resume_job(*, action: str = "advance") -> PipelineJob:
    """gate_run job carrying a resume_action payload (the cutover shape)."""
    return PipelineJob(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        job_type=PipelineJobType.gate_run,
        target_id=None,
        status=PipelineJobStatus.dispatched,
        attempts=0,
        payload={"gate_name": "gate2", "resume_action": action, "notes": "ok"},
        traceparent=None,
    )


def _live_case_row() -> Case:
    case = MagicMock(spec=Case)
    case.id = uuid.uuid4()
    case.domain_id = uuid.uuid4()
    case.domain = CaseDomainEnum.small_claims
    case.domain_ref = MagicMock(is_active=True, vector_store_id="vs_abc", code="small_claims")
    case.status_value = "processing"
    return case


def _make_state(*, halt: dict | None = None) -> CaseState:
    return CaseState(
        case_id=str(uuid.uuid4()),
        domain=CaseDomainEnum.small_claims,
        domain_vector_store_id="vs_abc",
        status=CaseStatusEnum.processing,
    )


def _db_mock_with_case(case_row: Case | None) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=case_row)
    db.execute.return_value = result
    return db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_path_persists_then_publishes_interrupt() -> None:
    """Ordering invariant: persist runs before publish_interrupt.

    persist_case_results writes case.status from the saver's CaseState
    ("processing"); publish_interrupt overwrites it with
    awaiting_review_gateN. Reversing that order silently strands the
    case at "processing" after every gate pause.
    """
    job = _make_resume_job(action="advance")
    state = _make_state()

    # Saver returns "processing" state with a pending gate2 interrupt.
    snapshot = MagicMock()
    snapshot.values = {"case": state, "halt": None}
    graph_mock = MagicMock()
    graph_mock.aget_state = AsyncMock(return_value=snapshot)

    drive_resume_mock = AsyncMock(
        return_value=("interrupt", "gate2", {"gate": "gate2", "actions": ["advance"]})
    )

    db_mock = _db_mock_with_case(_live_case_row())
    persist_mock = AsyncMock()
    publish_interrupt_mock = AsyncMock()
    publish_progress_mock = AsyncMock()

    call_order: list[str] = []
    persist_mock.side_effect = lambda *a, **kw: call_order.append("persist")
    publish_interrupt_mock.side_effect = lambda *a, **kw: call_order.append("publish_interrupt")
    publish_progress_mock.side_effect = lambda *a, **kw: call_order.append("publish_progress")

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()),
        patch.object(tasks, "_fail", new=AsyncMock()),
        patch(
            "src.services.database.async_session",
            lambda: _fake_session_cm(db_mock),
        ),
        patch(
            "src.pipeline.graph.runner.build_graph",
            return_value=graph_mock,
        ),
        patch("src.pipeline.graph.resume.drive_resume", new=drive_resume_mock),
        patch("src.db.persist_case_results.persist_case_results", new=persist_mock),
        patch(
            "src.services.pipeline_events.publish_interrupt",
            new=publish_interrupt_mock,
        ),
        patch(
            "src.services.pipeline_events.publish_progress",
            new=publish_progress_mock,
        ),
    ):
        await tasks.run_gate_job({}, str(job.id))

    drive_resume_mock.assert_awaited_once()
    persist_mock.assert_awaited_once()
    publish_interrupt_mock.assert_awaited_once()

    # Order: persist before publish_interrupt — otherwise the legacy
    # awaiting_review_gateN status gets clobbered by persist's status write.
    persist_idx = call_order.index("persist")
    interrupt_idx = call_order.index("publish_interrupt")
    assert persist_idx < interrupt_idx, (
        f"persist_case_results must run before publish_interrupt; got call_order={call_order!r}"
    )

    # gate_state_payload reflects the gate the saver paused at, not the
    # request payload's gate_name (which is the next-gate hint).
    persist_kwargs = persist_mock.await_args.kwargs
    gsp = persist_kwargs.get("gate_state_payload") or {}
    assert gsp.get("current_gate") == 2
    assert gsp.get("awaiting_review") is True


@pytest.mark.asyncio
async def test_resume_path_terminal_halt_persists_failed_status() -> None:
    """Halt branch: persisted state.status must be failed, not processing."""
    job = _make_resume_job(action="halt")
    state = _make_state()
    halt_value = {"reason": "judge_halt", "gate": "gate2", "notes": "withdrawn"}

    snapshot = MagicMock()
    snapshot.values = {"case": state, "halt": halt_value}
    graph_mock = MagicMock()
    graph_mock.aget_state = AsyncMock(return_value=snapshot)

    drive_resume_mock = AsyncMock(return_value=("terminal", None, None))
    db_mock = _db_mock_with_case(_live_case_row())
    persist_mock = AsyncMock()
    publish_interrupt_mock = AsyncMock()
    publish_progress_mock = AsyncMock()

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()),
        patch.object(tasks, "_fail", new=AsyncMock()),
        patch(
            "src.services.database.async_session",
            lambda: _fake_session_cm(db_mock),
        ),
        patch(
            "src.pipeline.graph.runner.build_graph",
            return_value=graph_mock,
        ),
        patch("src.pipeline.graph.resume.drive_resume", new=drive_resume_mock),
        patch("src.db.persist_case_results.persist_case_results", new=persist_mock),
        patch(
            "src.services.pipeline_events.publish_interrupt",
            new=publish_interrupt_mock,
        ),
        patch(
            "src.services.pipeline_events.publish_progress",
            new=publish_progress_mock,
        ),
    ):
        await tasks.run_gate_job({}, str(job.id))

    publish_interrupt_mock.assert_not_awaited()
    persist_mock.assert_awaited_once()
    persisted_state = persist_mock.await_args.args[2]
    assert persisted_state.status == CaseStatusEnum.failed, (
        f"halt terminal must persist status=failed; got {persisted_state.status!r}"
    )

    publish_progress_mock.assert_awaited_once()
    progress_event = publish_progress_mock.await_args.args[0]
    assert progress_event.phase == "terminal"


@pytest.mark.asyncio
async def test_resume_path_terminal_no_halt_persists_closed_status() -> None:
    """Non-halt terminal (gate4 advance to END) persists status=closed.

    The /respond API blocks gate4 advance with 409, so this branch is
    rarely hit in production — but the worker still has to do something
    sensible if it ever lands. ``closed`` is the closest-to-correct
    terminal state without a judicial decision recorded.
    """
    job = _make_resume_job(action="advance")
    state = _make_state()

    snapshot = MagicMock()
    snapshot.values = {"case": state, "halt": None}
    graph_mock = MagicMock()
    graph_mock.aget_state = AsyncMock(return_value=snapshot)

    drive_resume_mock = AsyncMock(return_value=("terminal", None, None))
    db_mock = _db_mock_with_case(_live_case_row())
    persist_mock = AsyncMock()

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()),
        patch.object(tasks, "_fail", new=AsyncMock()),
        patch(
            "src.services.database.async_session",
            lambda: _fake_session_cm(db_mock),
        ),
        patch(
            "src.pipeline.graph.runner.build_graph",
            return_value=graph_mock,
        ),
        patch("src.pipeline.graph.resume.drive_resume", new=drive_resume_mock),
        patch("src.db.persist_case_results.persist_case_results", new=persist_mock),
        patch("src.services.pipeline_events.publish_interrupt", new=AsyncMock()),
        patch("src.services.pipeline_events.publish_progress", new=AsyncMock()),
    ):
        await tasks.run_gate_job({}, str(job.id))

    persist_mock.assert_awaited_once()
    persisted_state = persist_mock.await_args.args[2]
    assert persisted_state.status == CaseStatusEnum.closed


@pytest.mark.asyncio
async def test_resume_path_aborts_when_domain_retired_between_gates() -> None:
    """D2 invariant carried into the cutover path.

    An admin retiring the domain after gate pause must abort the resume
    rather than running with a stale vector_store_id from the saver.
    The worker also writes the case to ``failed_retryable`` so the FE
    surfaces "Pipeline interrupted, retry?" rather than burying the
    failure.
    """
    job = _make_resume_job(action="advance")
    retired_case = MagicMock(spec=Case)
    retired_case.id = job.case_id
    retired_case.domain_id = uuid.uuid4()
    retired_case.domain = CaseDomainEnum.small_claims
    retired_case.domain_ref = MagicMock(is_active=False, vector_store_id=None, code="small_claims")
    retired_case.status_value = "processing"
    db_mock = _db_mock_with_case(retired_case)

    drive_resume_mock = AsyncMock()
    persist_mock = AsyncMock()
    publish_interrupt_mock = AsyncMock()

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()),
        patch.object(tasks, "_fail", new=AsyncMock()),
        patch(
            "src.services.database.async_session",
            lambda: _fake_session_cm(db_mock),
        ),
        patch(
            "src.pipeline.graph.runner.build_graph",
            return_value=MagicMock(),
        ),
        patch("src.pipeline.graph.resume.drive_resume", new=drive_resume_mock),
        patch("src.db.persist_case_results.persist_case_results", new=persist_mock),
        patch(
            "src.services.pipeline_events.publish_interrupt",
            new=publish_interrupt_mock,
        ),
        patch("src.services.pipeline_events.publish_progress", new=AsyncMock()),
        pytest.raises(RetiredDomainError),
    ):
        await tasks.run_gate_job({}, str(job.id))

    drive_resume_mock.assert_not_awaited()
    persist_mock.assert_not_awaited()
    publish_interrupt_mock.assert_not_awaited()
    assert retired_case.status_value == "failed_retryable"
    db_mock.commit.assert_awaited()


@pytest.mark.asyncio
async def test_legacy_path_taken_when_no_resume_action() -> None:
    """Pre-cutover queued jobs (no resume_action) take the legacy branch.

    Pinned via the absence of a drive_resume call — the legacy runner
    has its own integration test (``test_worker_gate_run.py``) that
    verifies the read-via-aget_state contract. Here we just prove the
    routing.
    """
    job = PipelineJob(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        job_type=PipelineJobType.gate_run,
        target_id=None,
        status=PipelineJobStatus.dispatched,
        attempts=0,
        payload={"gate_name": "gate2"},  # no resume_action
        traceparent=None,
    )
    drive_resume_mock = AsyncMock()
    legacy_mock = AsyncMock()

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()),
        patch.object(tasks, "_fail", new=AsyncMock()),
        patch.object(tasks, "_run_gate_via_legacy", new=legacy_mock),
        patch.object(tasks, "_run_gate_via_resume", new=drive_resume_mock),
    ):
        await tasks.run_gate_job({}, str(job.id))

    legacy_mock.assert_awaited_once()
    drive_resume_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_path_taken_when_resume_action_present() -> None:
    job = _make_resume_job(action="advance")
    drive_resume_mock = AsyncMock()
    legacy_mock = AsyncMock()

    with (
        patch.object(tasks, "_load_job", new=AsyncMock(return_value=job)),
        patch.object(tasks, "_complete", new=AsyncMock()),
        patch.object(tasks, "_fail", new=AsyncMock()),
        patch.object(tasks, "_run_gate_via_legacy", new=legacy_mock),
        patch.object(tasks, "_run_gate_via_resume", new=drive_resume_mock),
    ):
        await tasks.run_gate_job({}, str(job.id))

    drive_resume_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()
