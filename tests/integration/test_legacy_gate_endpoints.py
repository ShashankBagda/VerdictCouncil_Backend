"""Sprint 4 4.A3 — legacy /advance + /rerun thin-wrapper contract.

The two legacy endpoints (POST /cases/{id}/gates/{gate}/advance and
.../rerun) now delegate to the same outbox job shape that the unified
``/respond`` endpoint enqueues. The worker reads ``resume_action`` to
choose ``drive_resume`` over ``_run_gate_via_legacy``, so legacy
clients now ride the saver-driven path too.

This suite locks the wrapper's enqueue payload — both shape and the
gate→phase / agent→subagent translations — so future refactors don't
drift the contract back to the pre-cutover legacy keys.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.deps import get_current_user, get_db
from src.models.case import CaseStatus
from src.models.user import User, UserRole

JUDGE_ID = uuid.uuid4()
CASE_ID = uuid.uuid4()


def _make_auth_override():
    mock_user = MagicMock(spec=User)
    mock_user.id = JUDGE_ID
    mock_user.role = UserRole.judge

    async def _override():
        return mock_user

    return _override


def _make_db_case(status: CaseStatus) -> MagicMock:
    db_case = MagicMock()
    db_case.id = CASE_ID
    db_case.status = status
    db_case.created_by = JUDGE_ID
    db_case.gate_state = None
    return db_case


def _override_db(db_case: MagicMock | None):
    async def _gen():
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=db_case)
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock()
        session.commit = AsyncMock()
        yield session

    return _gen


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_advance_enqueues_resume_action_advance() -> None:
    """Legacy /advance includes resume_action='advance' so the worker
    routes through drive_resume rather than _run_gate_via_legacy."""
    db_case = _make_db_case(CaseStatus.awaiting_review_gate1)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
        async with _client() as c:
            r = await c.post(f"/api/v1/cases/{CASE_ID}/gates/gate1/advance", json={})

    assert r.status_code == 202
    enqueue_mock.assert_awaited_once()
    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["gate_name"] == "gate2"
    assert payload["resume_action"] == "advance"


@pytest.mark.asyncio
async def test_rerun_gate2_with_agent_translates_to_subagent() -> None:
    """Legacy gate2 agent_name → unified subagent + phase=research.

    'evidence-analysis' → subagent='evidence', phase='research'.
    """
    db_case = _make_db_case(CaseStatus.awaiting_review_gate2)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/gates/gate2/rerun",
                json={
                    "agent_name": "evidence-analysis",
                    "instructions": "weight matrix is wrong",
                },
            )

    assert r.status_code == 202
    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["gate_name"] == "gate2"
    assert payload["resume_action"] == "rerun"
    assert payload["phase"] == "research"
    assert payload["subagent"] == "evidence"
    # Notes carry the instructions for the saver-driven path; the
    # legacy 'instructions' slot stays for in-flight pre-cutover jobs.
    assert payload["notes"] == "weight matrix is wrong"
    assert payload["instructions"] == "weight matrix is wrong"
    assert payload["start_agent"] == "evidence-analysis"


@pytest.mark.asyncio
async def test_rerun_gate1_phase_only_no_subagent() -> None:
    """gate1 has no per-subagent granularity — phase=intake, no subagent."""
    db_case = _make_db_case(CaseStatus.awaiting_review_gate1)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/gates/gate1/rerun",
                json={"instructions": "redo intake"},
            )

    assert r.status_code == 202
    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["resume_action"] == "rerun"
    assert payload["phase"] == "intake"
    assert "subagent" not in payload


@pytest.mark.asyncio
async def test_rerun_gate3_drops_agent_name_subagent_mapping() -> None:
    """gate3 agent_name (argument-construction etc.) doesn't map to a
    subagent — the unified rerun is phase-level for gate3."""
    db_case = _make_db_case(CaseStatus.awaiting_review_gate3)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/gates/gate3/rerun",
                json={"agent_name": "argument-construction"},
            )

    assert r.status_code == 202
    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["phase"] == "synthesis"
    assert "subagent" not in payload
    assert payload["start_agent"] == "argument-construction"


@pytest.mark.asyncio
async def test_rerun_gate2_accepts_langgraph_agent_id() -> None:
    """LangGraph node IDs (research-evidence, …) are accepted natively
    alongside the legacy display names."""
    db_case = _make_db_case(CaseStatus.awaiting_review_gate2)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/gates/gate2/rerun",
                json={"agent_name": "research-evidence"},
            )

    assert r.status_code == 202
    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["phase"] == "research"
    assert payload["subagent"] == "evidence"
    assert payload["start_agent"] == "research-evidence"


@pytest.mark.asyncio
async def test_rerun_rejects_agent_name_outside_gate() -> None:
    """Agents from a different gate (in either alphabet) are 422."""
    db_case = _make_db_case(CaseStatus.awaiting_review_gate2)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", new=enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/gates/gate2/rerun",
                json={"agent_name": "audit"},
            )

    assert r.status_code == 422
    enqueue_mock.assert_not_awaited()
