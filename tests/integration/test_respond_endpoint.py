"""Sprint 4 4.A3.15 — POST /cases/{id}/respond endpoint contract.

Exercises the four ``ResumePayload.action`` paths plus authorization
and current-gate validation. The worker-level Command(resume=...)
rewrite is 4.A3.5 follow-up; this suite locks the API contract.
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


def _make_auth_override(user_id: uuid.UUID = JUDGE_ID):
    mock_user = MagicMock(spec=User)
    mock_user.id = user_id
    mock_user.role = UserRole.judge

    async def _override():
        return mock_user

    return _override


def _make_db_case(status: CaseStatus, *, owner_id: uuid.UUID = JUDGE_ID) -> MagicMock:
    db_case = MagicMock()
    db_case.id = CASE_ID
    db_case.status = status
    db_case.created_by = owner_id
    db_case.gate_state = None
    return db_case


def _override_db(db_case: MagicMock | None):
    """Return an override yielding a session whose query returns ``db_case``."""

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


# ---------------------------------------------------------------------------
# Authorization + 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respond_404_when_case_missing() -> None:
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(None)

    async with _client() as c:
        r = await c.post(f"/api/v1/cases/{CASE_ID}/respond", json={"action": "advance"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_respond_404_when_judge_not_owner() -> None:
    other = uuid.uuid4()
    app.dependency_overrides[get_current_user] = _make_auth_override(user_id=other)
    app.dependency_overrides[get_db] = _override_db(
        _make_db_case(CaseStatus.awaiting_review_gate1, owner_id=JUDGE_ID)
    )

    async with _client() as c:
        r = await c.post(f"/api/v1/cases/{CASE_ID}/respond", json={"action": "advance"})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 409 — current gate / action mismatches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respond_409_when_case_not_paused() -> None:
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(_make_db_case(CaseStatus.processing))

    async with _client() as c:
        r = await c.post(f"/api/v1/cases/{CASE_ID}/respond", json={"action": "advance"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_respond_409_advance_at_gate4() -> None:
    """Gate4 advance is invalid — judge must record a decision instead."""
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(_make_db_case(CaseStatus.awaiting_review_gate4))

    async with _client() as c:
        r = await c.post(f"/api/v1/cases/{CASE_ID}/respond", json={"action": "advance"})
    assert r.status_code == 409
    assert "final gate" in r.json()["detail"].lower() or "decision" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respond_advance_gate1_enqueues_gate2() -> None:
    db_case = _make_db_case(CaseStatus.awaiting_review_gate1)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/respond",
                json={"action": "advance", "notes": "looks good"},
            )

    assert r.status_code == 202
    assert "gate2" in r.json()["message"].lower()
    assert enqueue_mock.await_count == 1
    job_kwargs = enqueue_mock.await_args.kwargs
    assert job_kwargs["payload"]["gate_name"] == "gate2"
    assert job_kwargs["payload"]["resume_action"] == "advance"
    assert job_kwargs["payload"]["notes"] == "looks good"
    assert db_case.status == CaseStatus.processing


@pytest.mark.asyncio
async def test_respond_rerun_synthesis_with_field_corrections() -> None:
    db_case = _make_db_case(CaseStatus.awaiting_review_gate3)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/respond",
                json={
                    "action": "rerun",
                    "phase": "synthesis",
                    "notes": "tighten Q3",
                    "field_corrections": {
                        "synthesis_output": {"judicial_questions": ["Q1?", "Q2?"]}
                    },
                },
            )

    assert r.status_code == 202
    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["phase"] == "synthesis"
    assert payload["resume_action"] == "rerun"
    assert payload["field_corrections"]["synthesis_output"]["judicial_questions"][0] == "Q1?"


@pytest.mark.asyncio
async def test_respond_rerun_research_subagent() -> None:
    db_case = _make_db_case(CaseStatus.awaiting_review_gate2)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    enqueue_mock = AsyncMock()
    with patch("src.workers.outbox.enqueue_outbox_job", enqueue_mock):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/respond",
                json={
                    "action": "rerun",
                    "phase": "research",
                    "subagent": "evidence",
                    "notes": "weight matrix is wrong",
                },
            )

    assert r.status_code == 202
    payload = enqueue_mock.await_args.kwargs["payload"]
    assert payload["phase"] == "research"
    assert payload["subagent"] == "evidence"


@pytest.mark.asyncio
async def test_respond_halt_terminates() -> None:
    db_case = _make_db_case(CaseStatus.awaiting_review_gate2)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    async with _client() as c:
        r = await c.post(
            f"/api/v1/cases/{CASE_ID}/respond",
            json={"action": "halt", "notes": "case withdrawn"},
        )
    assert r.status_code == 202
    assert "halted" in r.json()["message"].lower()
    assert db_case.status == CaseStatus.failed


@pytest.mark.asyncio
async def test_respond_send_back_drives_rewind_and_repauses() -> None:
    """4.A3.14 — send_back routes through send_back_to_phase and bumps status.

    The endpoint:
    - Calls send_back_to_phase with the helper to fork the thread.
    - Updates case.status to the gate the rewind re-paused at.
    - Audit-logs the action with from_gate/to_phase/new_pause_gate.
    """
    db_case = _make_db_case(CaseStatus.awaiting_review_gate4)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    send_back_mock = AsyncMock(return_value="gate3")
    fake_runner = MagicMock()
    fake_runner._graph = MagicMock()

    with (
        patch(
            "src.pipeline.graph.resume.send_back_to_phase",
            new=send_back_mock,
        ),
        patch(
            "src.pipeline.graph.runner.GraphPipelineRunner",
            return_value=fake_runner,
        ),
    ):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/respond",
                json={
                    "action": "send_back",
                    "to_phase": "synthesis",
                    "notes": "rewrite",
                },
            )

    assert r.status_code == 202
    assert "gate3" in r.json()["message"]
    assert db_case.status == CaseStatus.awaiting_review_gate3
    send_back_mock.assert_awaited_once()
    kwargs = send_back_mock.await_args.kwargs
    assert kwargs["to_phase"] == "synthesis"
    assert kwargs["notes"] == "rewrite"


@pytest.mark.asyncio
async def test_respond_send_back_409_when_no_history() -> None:
    """If the helper raises RuntimeError (no matching history), the
    endpoint surfaces a 409 rather than a 500 — same-status return on
    a programming-or-state error is more useful than a stack trace."""
    db_case = _make_db_case(CaseStatus.awaiting_review_gate4)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    send_back_mock = AsyncMock(
        side_effect=RuntimeError("send_back: no interrupted checkpoint at 'gate3_pause'")
    )
    fake_runner = MagicMock()
    fake_runner._graph = MagicMock()

    with (
        patch(
            "src.pipeline.graph.resume.send_back_to_phase",
            new=send_back_mock,
        ),
        patch(
            "src.pipeline.graph.runner.GraphPipelineRunner",
            return_value=fake_runner,
        ),
    ):
        async with _client() as c:
            r = await c.post(
                f"/api/v1/cases/{CASE_ID}/respond",
                json={
                    "action": "send_back",
                    "to_phase": "synthesis",
                    "notes": "rewrite",
                },
            )

    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Schema enforcement at the FastAPI boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_respond_extra_field_rejected_422() -> None:
    db_case = _make_db_case(CaseStatus.awaiting_review_gate1)
    app.dependency_overrides[get_current_user] = _make_auth_override()
    app.dependency_overrides[get_db] = _override_db(db_case)

    async with _client() as c:
        r = await c.post(
            f"/api/v1/cases/{CASE_ID}/respond",
            json={"action": "advance", "agent": "evidence-analysis"},  # extra
        )
    assert r.status_code == 422
