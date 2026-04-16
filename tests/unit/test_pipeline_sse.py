"""Unit tests for the SSE pipeline status stream endpoint (US-002)."""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.models.case import Case
from src.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(**overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Justice Bao",
        "email": "bao@example.com",
        "role": UserRole.judge,
        "password_hash": "hashed",
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def _make_case(case_id: uuid.UUID, created_by: uuid.UUID) -> MagicMock:
    case = MagicMock(spec=Case)
    case.id = case_id
    case.created_by = created_by
    return case


def _build_mock_session(case: MagicMock | None) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = case
    session.execute = AsyncMock(return_value=result)
    return session


def _app_with_overrides(mock_db, mock_user):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


def _fake_subscribe_factory(events: list[str]):
    """Return an async generator factory that yields the given pre-built JSON events."""

    async def _fake_subscribe(case_id):
        for event in events:
            yield event

    return _fake_subscribe


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamPipelineStatus:
    async def test_streams_events_until_governance_verdict_terminal(self, monkeypatch):
        """SSE response contains data: lines, one per event from the subscriber."""
        user = _make_user()
        case_id = uuid.uuid4()
        case = _make_case(case_id, user.id)
        mock_db = _build_mock_session(case)

        events = [
            PipelineProgressEvent(
                case_id=case_id,
                agent="case-processing",
                phase="started",
                step=1,
                ts=datetime.now(UTC),
            ).model_dump_json(),
            PipelineProgressEvent(
                case_id=case_id,
                agent="case-processing",
                phase="completed",
                step=1,
                ts=datetime.now(UTC),
            ).model_dump_json(),
            PipelineProgressEvent(
                case_id=case_id,
                agent="governance-verdict",
                phase="completed",
                step=9,
                ts=datetime.now(UTC),
            ).model_dump_json(),
        ]

        # Patch the symbol where it is *used* in the routes module
        from src.api.routes import cases as cases_module

        monkeypatch.setattr(
            cases_module,
            "subscribe_pipeline_events",
            _fake_subscribe_factory(events),
        )

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/status/stream")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        body = resp.text
        # SSE wire format: each event becomes "data: <json>\r\n\r\n"
        data_lines = [line for line in body.splitlines() if line.startswith("data:")]
        assert len(data_lines) == 3

        first_payload = json.loads(data_lines[0][len("data: ") :])
        assert first_payload["agent"] == "case-processing"
        assert first_payload["phase"] == "started"

        last_payload = json.loads(data_lines[-1][len("data: ") :])
        assert last_payload["agent"] == "governance-verdict"
        assert last_payload["phase"] == "completed"

    async def test_returns_404_when_case_missing(self, monkeypatch):
        """Endpoint mirrors get_case 404 behaviour for unknown case ids."""
        user = _make_user()
        mock_db = _build_mock_session(case=None)

        from src.api.routes import cases as cases_module

        monkeypatch.setattr(
            cases_module,
            "subscribe_pipeline_events",
            _fake_subscribe_factory([]),
        )

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{uuid.uuid4()}/status/stream")

        assert resp.status_code == 404

    async def test_clerk_cannot_stream_other_users_case(self, monkeypatch):
        """A clerk who doesn't own the case gets 403 (mirrors get_case rules)."""
        owner = _make_user(role=UserRole.clerk, email="owner@example.com")
        intruder = _make_user(role=UserRole.clerk, email="intruder@example.com")

        case_id = uuid.uuid4()
        case = _make_case(case_id, owner.id)
        mock_db = _build_mock_session(case)

        from src.api.routes import cases as cases_module

        monkeypatch.setattr(
            cases_module,
            "subscribe_pipeline_events",
            _fake_subscribe_factory([]),
        )

        app = _app_with_overrides(mock_db, intruder)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/status/stream")

        assert resp.status_code == 403


class TestPipelineEventsHelper:
    """Verify the pub/sub generator closes once governance-verdict reaches terminal phase."""

    async def test_subscribe_closes_on_governance_verdict_completed(self, monkeypatch):
        from src.services import pipeline_events as pe

        case_id = "case-123"

        events = [
            json.dumps({"agent": "case-processing", "phase": "started"}),
            json.dumps({"agent": "governance-verdict", "phase": "completed"}),
            # Anything after the terminal event must NOT be yielded
            json.dumps({"agent": "case-processing", "phase": "started"}),
        ]

        # Build a fake pubsub.listen() async iterator
        async def _fake_listen():
            for payload in events:
                yield {"type": "message", "data": payload}

        fake_pubsub = MagicMock()
        fake_pubsub.subscribe = AsyncMock()
        fake_pubsub.unsubscribe = AsyncMock()
        fake_pubsub.aclose = AsyncMock()
        fake_pubsub.listen = _fake_listen

        fake_redis = MagicMock()
        fake_redis.pubsub = MagicMock(return_value=fake_pubsub)

        async def _fake_get_client():
            return fake_redis

        monkeypatch.setattr(pe, "_get_redis_client", _fake_get_client)

        collected = []
        async for payload in pe.subscribe(case_id):
            collected.append(payload)

        assert len(collected) == 2
        assert json.loads(collected[-1])["phase"] == "completed"
        fake_pubsub.unsubscribe.assert_awaited_once()
        fake_pubsub.aclose.assert_awaited_once()


@pytest.mark.parametrize(
    "phase",
    ["started", "completed", "failed"],
)
def test_pipeline_progress_event_validates_phase(phase):
    """The Pydantic model accepts the 3 documented phases."""
    PipelineProgressEvent(
        case_id=uuid.uuid4(),
        agent="case-processing",
        phase=phase,
        step=1,
        ts=datetime.now(UTC),
    )
