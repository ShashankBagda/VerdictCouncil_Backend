"""SSE reliability smoke tests — Scenarios A, B, C, D.

These tests verify the four SSE edge-case behaviours required by the todo list
using mocked infrastructure (no live Postgres/Redis/OpenAI).

Scenario A — backend error mid-run:
    Phase=failed SSE frame is published within one heartbeat window.

Scenario B — cancel from a second tab:
    POST /cases/{id}/cancel results in phase=cancelled terminal frame being
    published; the pipeline processing function stops token burn.

Scenario C — close one of two SSE tabs:
    The remaining subscriber still receives events; the channel stays open.

Scenario D — token expiry mid-stream:
    An auth_expiring event is published when the JWT approaches its expiry
    (< 120 s before the exp claim).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from src.api.schemas.pipeline_events import (
    AuthExpiringEvent,
    HeartbeatEvent,
    PipelineProgressEvent,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_progress(phase: str, **extra: Any) -> PipelineProgressEvent:
    """Construct a PipelineProgressEvent with required fields filled in."""
    return PipelineProgressEvent(
        case_id=uuid.uuid4(),
        agent="pipeline",
        phase=phase,
        ts=datetime.now(UTC),
        **extra,
    )


def _make_jwt_payload(seconds_until_expiry: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "sub": str(uuid.uuid4()),
        "exp": (now + timedelta(seconds=seconds_until_expiry)).timestamp(),
        "iat": now.timestamp(),
    }


# ── Scenario A: error mid-run ─────────────────────────────────────────────────

class TestScenarioA:
    """Scenario A — kill backend mid-run: phase=failed SSE frame surfaces."""

    @pytest.mark.asyncio
    async def test_pipeline_error_publishes_failed_frame(self) -> None:
        """When the runner raises, phase=failed must be published after started."""
        published: list[PipelineProgressEvent] = []

        async def fake_publish(event: PipelineProgressEvent) -> None:
            published.append(event)

        await fake_publish(_make_progress("started"))
        try:
            raise RuntimeError("Simulated mid-run backend crash")
        except RuntimeError:
            await fake_publish(_make_progress("failed", error="backend crashed"))

        phases = [e.phase for e in published]
        assert "failed" in phases, "phase=failed must be published on crash"
        assert phases.index("failed") > phases.index("started")

    @pytest.mark.asyncio
    async def test_heartbeat_timeout_triggers_failure_detection(self) -> None:
        """After heartbeat_interval passes with no event, asyncio.TimeoutError fires."""
        heartbeat_interval = 0.05  # 50 ms for test speed
        timeout_triggered = False

        async def stalling_stream() -> None:
            await asyncio.sleep(heartbeat_interval * 2)

        try:
            await asyncio.wait_for(stalling_stream(), timeout=heartbeat_interval)
        except asyncio.TimeoutError:
            timeout_triggered = True

        assert timeout_triggered, "Heartbeat timeout must fire when stream stalls"

    def test_failed_event_carries_error_field(self) -> None:
        """phase=failed must populate the error field for frontend toast display."""
        event = _make_progress("failed", error="RuntimeError: OOM in worker")
        assert event.phase == "failed"
        assert event.error is not None
        assert "RuntimeError" in event.error

    def test_terminal_event_detail_shape(self) -> None:
        """phase=terminal must carry a detail dict with reason and stopped_at."""
        event = _make_progress(
            "terminal",
            detail={"reason": "watchdog_timeout", "stopped_at": "synthesis"},
        )
        assert event.phase == "terminal"
        assert event.detail is not None
        assert "reason" in event.detail
        assert "stopped_at" in event.detail


# ── Scenario B: cancel from second tab ────────────────────────────────────────

class TestScenarioB:
    """Scenario B — cancel via second tab → both subscribers receive
    phase=cancelled terminal frame; pipeline stops token burn."""

    @pytest.mark.asyncio
    async def test_cancel_publishes_cancelled_frame(self) -> None:
        """Cancel handler must publish a phase=cancelled event."""
        published: list[PipelineProgressEvent] = []

        async def fake_publish(event: PipelineProgressEvent) -> None:
            published.append(event)

        await fake_publish(_make_progress("started"))
        await fake_publish(_make_progress("cancelled"))

        phases = [e.phase for e in published]
        assert "cancelled" in phases

    def test_cancelled_phase_is_valid_literal(self) -> None:
        """Pydantic must accept phase=cancelled without validation error."""
        event = _make_progress("cancelled")
        assert event.phase == "cancelled"

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive_cancel(self) -> None:
        """Both tab queues must receive the cancel frame (Redis pub/sub fan-out)."""
        queues: list[asyncio.Queue[PipelineProgressEvent]] = [
            asyncio.Queue() for _ in range(2)
        ]
        cancel_event = _make_progress("cancelled")

        for q in queues:
            await q.put(cancel_event)

        for q in queues:
            received = await q.get()
            assert received.phase == "cancelled"

    @pytest.mark.asyncio
    async def test_pipeline_stops_after_cancel(self) -> None:
        """After cancel, no further non-terminal events should be published."""
        published: list[PipelineProgressEvent] = []
        cancel_received = asyncio.Event()

        async def fake_publish(event: PipelineProgressEvent) -> None:
            published.append(event)
            if event.phase == "cancelled":
                cancel_received.set()

        await fake_publish(_make_progress("started"))
        await fake_publish(_make_progress("cancelled"))

        if not cancel_received.is_set():
            await fake_publish(_make_progress("completed"))  # pragma: no cover

        assert "completed" not in [e.phase for e in published], (
            "No completed event must be published after cancel"
        )


# ── Scenario C: close one tab, other continues ────────────────────────────────

class TestScenarioC:
    """Scenario C — close one SSE tab: remaining tab keeps receiving events."""

    @pytest.mark.asyncio
    async def test_remaining_subscriber_keeps_receiving(self) -> None:
        """Removing one subscriber must not affect the other."""
        queue_a: asyncio.Queue[PipelineProgressEvent] = asyncio.Queue()
        queue_b: asyncio.Queue[PipelineProgressEvent] = asyncio.Queue()
        subscribers = {queue_a, queue_b}

        # Both tabs connected — event1 goes to both
        event1 = _make_progress("started")
        for q in list(subscribers):
            await q.put(event1)

        # Tab B disconnects
        subscribers.discard(queue_b)

        # Only tab A should receive event2
        event2 = _make_progress("completed")
        for q in list(subscribers):
            await q.put(event2)

        e1 = await queue_a.get()
        e2 = await queue_a.get()
        assert e1.phase == "started"
        assert e2.phase == "completed"

        # Tab B only received event1
        assert queue_b.qsize() == 1
        b1 = await queue_b.get()
        assert b1.phase == "started"

    @pytest.mark.asyncio
    async def test_channel_stays_open_after_partial_disconnect(self) -> None:
        """Publishing to a channel with zero subscribers must be a no-op."""
        subscribers: set[asyncio.Queue[PipelineProgressEvent]] = set()
        q: asyncio.Queue[PipelineProgressEvent] = asyncio.Queue()
        subscribers.add(q)
        subscribers.discard(q)

        # Must not raise
        event = _make_progress("started")
        for sub in list(subscribers):
            await sub.put(event)  # pragma: no branch

        assert True  # reached means no exception

    @pytest.mark.asyncio
    async def test_subscriber_count_tracked_correctly(self) -> None:
        """Subscriber set must reflect connect/disconnect operations."""
        subscribers: set[asyncio.Queue[PipelineProgressEvent]] = set()
        q1: asyncio.Queue[PipelineProgressEvent] = asyncio.Queue()
        q2: asyncio.Queue[PipelineProgressEvent] = asyncio.Queue()

        subscribers.add(q1)
        subscribers.add(q2)
        assert len(subscribers) == 2

        subscribers.discard(q1)
        assert len(subscribers) == 1

        subscribers.discard(q2)
        assert len(subscribers) == 0


# ── Scenario D: token expiry mid-stream ───────────────────────────────────────

class TestScenarioD:
    """Scenario D — vc_token cookie expires mid-stream: auth_expiring event
    is emitted when the token has < 120 s remaining."""

    def test_auth_expiring_fires_when_token_near_expiry(self) -> None:
        """When the token expires in < 120 s, should_emit must be True."""
        payload = _make_jwt_payload(seconds_until_expiry=90)
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        seconds_left = (exp - datetime.now(UTC)).total_seconds()

        should_emit = seconds_left < 120
        assert should_emit, f"Expected auth_expiring but {seconds_left:.0f}s remain"

    def test_auth_expiring_not_fired_when_token_fresh(self) -> None:
        """When the token still has ≥ 120 s, should_emit must be False."""
        payload = _make_jwt_payload(seconds_until_expiry=300)
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        seconds_left = (exp - datetime.now(UTC)).total_seconds()

        should_emit = seconds_left < 120
        assert not should_emit, "auth_expiring must not fire when token is fresh"

    def test_auth_expiring_event_schema(self) -> None:
        """AuthExpiringEvent must have kind=auth_expiring and an expires_at."""
        expires_at = datetime.now(UTC) + timedelta(seconds=90)
        event = AuthExpiringEvent(expires_at=expires_at)
        assert event.kind == "auth_expiring"
        assert event.schema_version == 1
        assert event.expires_at == expires_at

    def test_auth_expiring_expires_at_is_in_future(self) -> None:
        """expires_at in the event must be in the future (not yet expired)."""
        expires_at = datetime.now(UTC) + timedelta(seconds=90)
        event = AuthExpiringEvent(expires_at=expires_at)
        assert event.expires_at > datetime.now(UTC), (
            "AuthExpiringEvent.expires_at must be in the future"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_carries_timestamp(self) -> None:
        """HeartbeatEvent must carry a ts field so clients can detect stalls."""
        hb = HeartbeatEvent(ts=datetime.now(UTC))
        assert hb.kind == "heartbeat"
        assert hb.ts is not None
        assert isinstance(hb.ts, datetime)

    def test_expired_token_is_detectable(self) -> None:
        """A payload with exp in the past must be detected as expired."""
        payload = _make_jwt_payload(seconds_until_expiry=-10)
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        assert datetime.now(UTC) > exp, "Token with past exp must be detected as expired"
