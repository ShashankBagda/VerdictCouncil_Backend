"""Worker `run_gate_job` reads/writes via the saver, not pipeline_state (Sprint 2 2.A2.6).

Pre-2.A2.6 the worker used `load_case_state` / `persist_case_state` against
the bespoke `pipeline_checkpoints` table. With AsyncPostgresSaver wired at
compile-time (1.A1.PG), the graph's checkpointer already persists per-node
state under `thread_id = case_id` — duplicating the write into a separate
table is dead weight and a divergence risk during cutover.

These tests pin the new contract:
  * `graph.aget_state(config)` is the source of truth for "state at the
    end of the previous gate"
  * `load_case_state` / `persist_case_state` are NOT called from gate_run

DB and graph compile are stubbed so the test runs without Postgres or
Redis.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.case import Case
from src.models.pipeline_job import PipelineJob, PipelineJobStatus, PipelineJobType
from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum
from src.workers import tasks


@asynccontextmanager
async def _fake_session_cm(db_mock):
    yield db_mock


def _make_gate_job(*, gate_name: str = "gate2") -> PipelineJob:
    return PipelineJob(
        id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        job_type=PipelineJobType.gate_run,
        target_id=None,
        status=PipelineJobStatus.dispatched,
        attempts=0,
        payload={"gate_name": gate_name},
        traceparent=None,
    )


def _live_case_row(domain_id: uuid.UUID) -> Case:
    case = MagicMock(spec=Case)
    case.id = uuid.uuid4()
    case.domain_id = domain_id
    case.domain = CaseDomainEnum.small_claims
    case.domain_ref = MagicMock(
        is_active=True,
        vector_store_id="vs_abc",
        code="small_claims",
    )
    case.status_value = "processing"
    return case


@pytest.mark.asyncio
async def test_gate_run_reads_prior_state_via_aget_state_and_skips_pipeline_state() -> None:
    """run_gate_job pulls prior state from `graph.aget_state`, not `load_case_state`.

    The test passes if and only if:
      1. `graph.aget_state(config)` is called with `thread_id = case_id`
      2. The hydrated state is forwarded to `runner.run_gate`
      3. `load_case_state` / `persist_case_state` are NOT imported or called
    """
    job = _make_gate_job()
    domain_id = uuid.uuid4()
    seeded_state = CaseState(
        case_id=str(job.case_id),
        domain=CaseDomainEnum.small_claims,
        domain_vector_store_id="vs_old",
        status=CaseStatusEnum.awaiting_review_gate1,
        case_metadata={"prior": "gate1 result"},
    )
    final_state = seeded_state.model_copy(
        update={"status": CaseStatusEnum.awaiting_review_gate2}
    )

    aget_state_calls: list = []

    async def fake_aget_state(config):
        aget_state_calls.append(config)
        snapshot = MagicMock()
        snapshot.values = {"case": seeded_state}
        return snapshot

    captured: dict = {}

    async def fake_run_gate(self, case_state, gate_name, **kwargs):  # noqa: ARG001
        captured["case_state"] = case_state
        captured["gate_name"] = gate_name
        captured["kwargs"] = kwargs
        return final_state

    db_mock = MagicMock()
    db_mock.execute = AsyncMock()
    db_mock.commit = AsyncMock()
    case_result = MagicMock()
    case_result.scalar_one_or_none = MagicMock(return_value=_live_case_row(domain_id))
    db_mock.execute.return_value = case_result

    persist_results_mock = AsyncMock()
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
            "src.pipeline.graph.runner.GraphPipelineRunner.run_gate",
            new=fake_run_gate,
        ),
        patch(
            "src.pipeline.graph.runner.build_graph",
            return_value=MagicMock(aget_state=fake_aget_state),
        ),
        patch(
            "src.db.persist_case_results.persist_case_results",
            new=persist_results_mock,
        ),
        patch(
            "src.services.pipeline_events.publish_progress",
            new=publish_progress_mock,
        ),
        # If gate_run still references pipeline_state, these patches will
        # fail on import-of-deleted-symbol or get called — both are caught.
        patch.dict(
            "sys.modules",
            {},
        ),
    ):
        await tasks.run_gate_job({}, str(job.id))

    # 1. aget_state was the source of truth
    assert aget_state_calls, "gate_run must call graph.aget_state(config)"
    cfg = aget_state_calls[0]
    assert cfg["configurable"]["thread_id"] == str(job.case_id)

    # 2. The hydrated state reached run_gate
    assert captured["case_state"].case_id == seeded_state.case_id
    assert captured["case_state"].domain_vector_store_id == "vs_abc", (
        "live domain vector store should override the checkpoint value"
    )
    assert captured["gate_name"] == "gate2"

    # 3. Domain results are still persisted, gate progress event still published
    persist_results_mock.assert_awaited_once()
    publish_progress_mock.assert_awaited_once()

    # 4. The legacy pipeline_state writers must NOT have been imported into
    # the worker module's namespace nor referenced in `run_gate_job`.
    import inspect

    src = inspect.getsource(tasks)
    assert "load_case_state" not in src, (
        "gate_run must not call load_case_state (use graph.aget_state)"
    )
    assert "persist_case_state" not in src, (
        "gate_run must not call persist_case_state (saver writes implicitly)"
    )
