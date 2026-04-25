"""Unit tests for the SSE pipeline status stream endpoint (US-002)."""

import asyncio
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
    case.status = MagicMock()
    case.status.value = "processing"
    case.gate_state = None
    return case


def _build_mock_session(case: MagicMock | None) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = case
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=case)
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
    async def test_streams_events_until_hearing_governance_terminal(self, monkeypatch):
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
                agent="hearing-governance",
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
        # Snapshot-on-connect adds one extra event at the front (phase="case.status")
        assert len(data_lines) == 4

        snap_payload = json.loads(data_lines[0][len("data: ") :])
        assert snap_payload["phase"] == "case.status"
        assert snap_payload["agent"] == "pipeline"

        first_payload = json.loads(data_lines[1][len("data: ") :])
        assert first_payload["agent"] == "case-processing"
        assert first_payload["phase"] == "started"

        last_payload = json.loads(data_lines[-1][len("data: ") :])
        assert last_payload["agent"] == "hearing-governance"
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

    async def test_judge_can_stream_any_case(self, monkeypatch):
        """In single-judge model, any judge can stream any case's pipeline status."""
        owner = _make_user(role=UserRole.judge, email="owner@example.com")
        intruder = _make_user(role=UserRole.judge, email="intruder@example.com")

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

        assert resp.status_code == 200


class TestPipelineEventsHelper:
    """Verify the pub/sub generator closes once hearing-governance reaches terminal phase."""

    async def test_subscribe_closes_on_hearing_governance_completed(self, monkeypatch):
        from src.services import pipeline_events as pe

        case_id = "case-123"

        events = [
            json.dumps({"agent": "case-processing", "phase": "started"}),
            json.dumps({"agent": "hearing-governance", "phase": "completed"}),
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
    """The Pydantic model accepts the 3 documented per-agent phases."""
    PipelineProgressEvent(
        case_id=uuid.uuid4(),
        agent="case-processing",
        phase=phase,
        step=1,
        ts=datetime.now(UTC),
    )


def test_pipeline_progress_event_accepts_terminal_shape():
    """The schema must accept the run-level terminal event: agent='pipeline',
    phase='terminal', no step, detail carries reason + stopped_at.
    """
    event = PipelineProgressEvent(
        case_id=uuid.uuid4(),
        agent="pipeline",
        phase="terminal",
        step=None,
        ts=datetime.now(UTC),
        detail={"reason": "complexity_escalation", "stopped_at": "complexity-routing"},
    )
    assert event.step is None
    assert event.detail["reason"] == "complexity_escalation"


class TestTerminalCloseCondition:
    """Subscriber must close on either the hearing-governance happy
    path or the new pipeline/terminal halt signal — both are authoritative.
    """

    async def test_subscribe_closes_on_pipeline_terminal_event(self, monkeypatch):
        from src.services import pipeline_events as pe

        case_id = "case-pipeline-terminal"
        events = [
            json.dumps({"agent": "case-processing", "phase": "started"}),
            json.dumps(
                {
                    "agent": "pipeline",
                    "phase": "terminal",
                    "detail": {
                        "reason": "complexity_escalation",
                        "stopped_at": "complexity-routing",
                    },
                }
            ),
            # Must NOT be yielded — subscriber closed on the terminal above.
            json.dumps({"agent": "case-processing", "phase": "started"}),
        ]

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
        assert json.loads(collected[-1])["agent"] == "pipeline"
        fake_pubsub.unsubscribe.assert_awaited_once()
        fake_pubsub.aclose.assert_awaited_once()


class TestSSEStreamHeartbeatAndDisconnect:
    """Wire-level behaviour: heartbeats on idle, clean teardown on client
    disconnect. No real Redis — we feed a controlled async generator into
    the route's subscribe hook and drive timing with asyncio.sleep.
    """

    async def test_emits_keepalive_comment_on_idle(self, monkeypatch):
        """If no events arrive within SSE_HEARTBEAT_SECONDS, the stream emits
        a named `heartbeat` event with a JSON payload instead of hanging.
        """
        user = _make_user()
        case_id = uuid.uuid4()
        case = _make_case(case_id, user.id)
        mock_db = _build_mock_session(case)

        event_payload = PipelineProgressEvent(
            case_id=case_id,
            agent="hearing-governance",
            phase="completed",
            step=9,
            ts=datetime.now(UTC),
        ).model_dump_json()

        # Yield nothing for an interval longer than the heartbeat (forces the
        # TimeoutError branch), then one terminal event so the subscriber
        # closes cleanly and the test doesn't run until the watchdog trips.
        async def _slow_subscribe(case_id):
            await asyncio.sleep(0.15)
            yield event_payload

        from src.api.routes import cases as cases_module

        monkeypatch.setattr(cases_module, "subscribe_pipeline_events", _slow_subscribe)
        # Shrink heartbeat so the test finishes in <1s.
        monkeypatch.setattr(cases_module, "SSE_HEARTBEAT_SECONDS", 0.05)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/status/stream")

        assert resp.status_code == 200
        body = resp.text
        assert "event: heartbeat" in body, (
            f"expected `event: heartbeat` line in SSE body, got: {body!r}"
        )
        assert '"kind": "heartbeat"' in body
        assert "data: " in body

    async def test_watchdog_emits_synthetic_terminal_event(self, monkeypatch):
        """A runaway subscriber that never produces a terminal event must
        be closed by the watchdog with a synthetic pipeline/terminal event
        so the client can stop waiting.
        """
        user = _make_user()
        case_id = uuid.uuid4()
        case = _make_case(case_id, user.id)
        mock_db = _build_mock_session(case)

        async def _never_terminates(case_id):
            # Sleep longer than the (shrunk) watchdog horizon — the wait_for
            # heartbeat timeout will trip first, then the watchdog closes
            # the generator.
            await asyncio.sleep(3600)
            yield "unreachable"

        from src.api.routes import cases as cases_module

        monkeypatch.setattr(cases_module, "subscribe_pipeline_events", _never_terminates)
        # Heartbeat small; watchdog just above it so we get at least one
        # heartbeat-loop pass before the watchdog trips.
        monkeypatch.setattr(cases_module, "SSE_HEARTBEAT_SECONDS", 0.02)
        monkeypatch.setattr(cases_module, "SSE_WATCHDOG_SECONDS", 0.05)

        app = _app_with_overrides(mock_db, user)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/cases/{case_id}/status/stream")

        assert resp.status_code == 200
        body = resp.text
        data_lines = [line for line in body.splitlines() if line.startswith("data: ")]
        terminals = [json.loads(line[len("data: ") :]) for line in data_lines]
        watchdog = [
            e
            for e in terminals
            if e.get("agent") == "pipeline"
            and e.get("phase") == "terminal"
            and e.get("detail", {}).get("reason") == "watchdog_timeout"
        ]
        assert len(watchdog) == 1
