"""P4.21 — pipeline_events replay table tests.

Covers:
- _tee_write persists a payload row
- publish_progress fires a tee-write task
- publish_agent_event fires a tee-write task
- GET /api/v1/cases/{id}/events returns the recorded events
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.services.pipeline_events import _tee_write, publish_agent_event, publish_progress

# ---------------------------------------------------------------------------
# _tee_write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tee_write_inserts_row():
    """_tee_write adds a PipelineEvent row and commits."""
    case_id = uuid.uuid4()
    payload = {
        "kind": "progress",
        "schema_version": 1,
        "case_id": str(case_id),
        "agent": "case-processing",
        "phase": "started",
        "ts": datetime.now(UTC).isoformat(),
    }

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.services.database.async_session", return_value=mock_db),
        patch("src.models.pipeline_event.PipelineEvent") as mock_event_cls,
    ):
        await _tee_write(case_id, payload)

    mock_event_cls.assert_called_once()
    call_kwargs = mock_event_cls.call_args.kwargs
    assert call_kwargs["kind"] == "progress"
    assert call_kwargs["agent"] == "case-processing"
    assert call_kwargs["schema_version"] == 1
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_tee_write_swallows_exception():
    """_tee_write never raises — failures are logged only."""
    with patch("src.services.database.async_session", side_effect=RuntimeError("db down")):
        await _tee_write(uuid.uuid4(), {"kind": "progress", "ts": datetime.now(UTC).isoformat()})
    # no exception raised


# ---------------------------------------------------------------------------
# publish_progress fires a tee task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_progress_creates_tee_task():
    """publish_progress creates a background tee-write task."""
    case_id = uuid.uuid4()
    event = PipelineProgressEvent(
        case_id=case_id,
        agent="case-processing",
        phase="started",
        step=1,
        ts=datetime.now(UTC),
    )

    tasks_created: list = []

    def fake_create_task(coro):
        task = MagicMock()
        tasks_created.append(coro)
        coro.close()  # prevent "coroutine was never awaited" warning
        return task

    mock_redis = AsyncMock()
    with (
        patch("src.services.pipeline_events._get_redis_client", return_value=mock_redis),
        patch("src.services.pipeline_events.asyncio.create_task", side_effect=fake_create_task),
    ):
        await publish_progress(event)

    assert len(tasks_created) == 1


# ---------------------------------------------------------------------------
# publish_agent_event fires a tee task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_agent_event_creates_tee_task():
    """publish_agent_event creates a background tee-write task."""
    case_id = uuid.uuid4()

    tasks_created: list = []

    def fake_create_task(coro):
        task = MagicMock()
        tasks_created.append(coro)
        coro.close()
        return task

    mock_redis = AsyncMock()
    with (
        patch("src.services.pipeline_events._get_redis_client", return_value=mock_redis),
        patch("src.services.pipeline_events.asyncio.create_task", side_effect=fake_create_task),
    ):
        await publish_agent_event(
            case_id,
            {
                "case_id": str(case_id),
                "agent": "evidence-analysis",
                "event": "thinking",
                "content": "analysing",
                "ts": datetime.now(UTC).isoformat(),
            },
        )

    assert len(tasks_created) == 1


# ---------------------------------------------------------------------------
# GET /api/v1/cases/{id}/events
# ---------------------------------------------------------------------------


def _make_mock_event(case_id: uuid.UUID, kind: str = "progress", agent: str = "case-processing"):
    e = MagicMock()
    e.id = uuid.uuid4()
    e.kind = kind
    e.schema_version = 1
    e.agent = agent
    e.ts = datetime.now(UTC)
    e.payload = {"kind": kind, "agent": agent}
    return e


def _make_mock_case(case_id: uuid.UUID):
    c = MagicMock()
    c.id = case_id
    return c


@pytest.mark.asyncio
async def test_list_pipeline_events_returns_rows():
    """GET /cases/{id}/events returns serialised event list."""
    from src.api.routes.cases import list_pipeline_events

    case_id = uuid.uuid4()
    mock_events = [_make_mock_event(case_id), _make_mock_event(case_id, kind="agent")]

    db = AsyncMock()
    db.get = AsyncMock(return_value=_make_mock_case(case_id))
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = mock_events
    db.execute = AsyncMock(return_value=result_mock)

    current_user = MagicMock()
    current_user.role = "judge"

    from src.models.user import UserRole

    current_user.role = UserRole.judge

    response = await list_pipeline_events(
        case_id=case_id,
        db=db,
        current_user=current_user,
        limit=100,
        offset=0,
    )

    assert response["case_id"] == str(case_id)
    assert response["total"] == 2
    assert len(response["events"]) == 2
    assert response["events"][0]["kind"] == "progress"
    assert response["events"][1]["kind"] == "agent"


@pytest.mark.asyncio
async def test_list_pipeline_events_404_on_missing_case():
    """GET /cases/{id}/events raises 404 when case is not found."""
    from fastapi import HTTPException

    from src.api.routes.cases import list_pipeline_events

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    current_user = MagicMock()

    with pytest.raises(HTTPException) as exc_info:
        await list_pipeline_events(
            case_id=uuid.uuid4(),
            db=db,
            current_user=current_user,
            limit=100,
            offset=0,
        )

    assert exc_info.value.status_code == 404
