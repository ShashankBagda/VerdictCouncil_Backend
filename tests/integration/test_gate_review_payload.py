"""Sprint 4 4.A3.3 — gate-pause review payload + 4.A3.7 InterruptEvent.

Locks two contracts:

1. ``make_gate_pause`` enriches the ``interrupt()`` payload with the
   per-gate phase output snapshot, the active OTEL trace id, and (for
   gate4) an audit summary including any auditor `recommend_send_back`
   recommendation. Per-gate phase mapping is fixed in ``GATE_PHASE_SLOT``
   so callers can't drift.
2. ``publish_interrupt`` writes the legacy ``cases.status =
   awaiting_review_gateN`` and ``cases.gate_state`` JSONB fields used
   by case-list filters and watchdog queries — keeping the saver-driven
   pipeline backwards-compatible during the cutover.

The DB-write path is exercised via mocks; the actual SQL is covered by
``test_audit_schema.py`` and the existing legacy filter tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest import mock
from uuid import UUID

import pytest
from langgraph.errors import GraphInterrupt
from langgraph.types import Interrupt

from src.api.schemas.pipeline_events import InterruptEvent
from src.pipeline.graph.nodes.gates import (
    GATE_PHASE_SLOT,
    make_gate_pause,
)
from src.shared.case_state import CaseState

# ---------------------------------------------------------------------------
# 4.A3.3 — pause payload enrichment
# ---------------------------------------------------------------------------


def _capture_interrupt_payload(state: dict, gate: str) -> dict:
    pause = make_gate_pause(gate)
    captured: list[dict] = []

    def fake_interrupt(value):
        captured.append(value)
        raise GraphInterrupt((Interrupt(value=value, id=f"{gate}-test"),))

    with (
        mock.patch(
            "src.pipeline.graph.nodes.gates.interrupt",
            side_effect=fake_interrupt,
        ),
        pytest.raises(GraphInterrupt),
    ):
        pause(state)

    assert captured, "interrupt() did not fire"
    return captured[0]


def test_pause_omits_phase_output_when_slot_unset() -> None:
    """No phase output yet → payload contains only the minimum keys."""
    state = {
        "case": CaseState(case_id="00000000-0000-0000-0000-000000000abc"),
    }
    payload = _capture_interrupt_payload(state, "gate1")
    assert payload["gate"] == "gate1"
    assert payload["actions"] == ["advance", "rerun", "halt"]
    assert "phase_output" not in payload
    assert "audit_summary" not in payload


def test_pause_includes_phase_output_when_present() -> None:
    """When the per-gate phase slot has a Pydantic model, it is serialised."""

    class FakePhaseOutput:
        def model_dump(self, mode: str = "python") -> dict:
            return {"summary": "intake done", "domain": "criminal"}

    state = {
        "case": CaseState(case_id="00000000-0000-0000-0000-000000000abc"),
        GATE_PHASE_SLOT["gate1"]: FakePhaseOutput(),
    }
    payload = _capture_interrupt_payload(state, "gate1")
    assert payload["phase_output"] == {"summary": "intake done", "domain": "criminal"}


def test_gate4_audit_summary_extracts_recommend_send_back() -> None:
    """Gate4 surfaces `recommend_send_back` for the frontend dropdown."""

    class FakeAuditOutput:
        def model_dump(self, mode: str = "python") -> dict:
            return {
                "recommend_send_back": {
                    "to_phase": "synthesis",
                    "reason": "uncertainty flag on conclusion 2",
                },
                "should_rerun": False,
                "target_phase": None,
                "reason": None,
            }

    state = {
        "case": CaseState(case_id="00000000-0000-0000-0000-000000000abc"),
        GATE_PHASE_SLOT["gate4"]: FakeAuditOutput(),
    }
    payload = _capture_interrupt_payload(state, "gate4")
    assert payload["audit_summary"]["recommend_send_back"] == {
        "to_phase": "synthesis",
        "reason": "uncertainty flag on conclusion 2",
    }


def test_pause_replays_produce_identical_payload_with_phase_output() -> None:
    """Idempotency invariant (4.A3.2) holds with the enriched payload."""

    class FakePhaseOutput:
        def model_dump(self, mode: str = "python") -> dict:
            return {"summary": "intake done"}

    state = {
        "case": CaseState(case_id="00000000-0000-0000-0000-000000000abc"),
        GATE_PHASE_SLOT["gate1"]: FakePhaseOutput(),
    }
    pause = make_gate_pause("gate1")
    captured: list[dict] = []

    def fake_interrupt(value):
        captured.append(value)
        raise GraphInterrupt((Interrupt(value=value, id="x"),))

    with mock.patch(
        "src.pipeline.graph.nodes.gates.interrupt",
        side_effect=fake_interrupt,
    ):
        for _ in range(3):
            with pytest.raises(GraphInterrupt):
                pause(state)

    assert all(p == captured[0] for p in captured), (
        "Enriched payload must remain replay-stable to satisfy 4.A3.2"
    )


# ---------------------------------------------------------------------------
# 4.A3.7 — publish_interrupt
# ---------------------------------------------------------------------------


def test_interrupt_event_schema_round_trip() -> None:
    """InterruptEvent serialises and deserialises cleanly."""
    event = InterruptEvent(
        case_id=UUID("00000000-0000-0000-0000-000000000abc"),
        gate="gate2",
        actions=["advance", "rerun", "halt"],
        phase_output={"items": []},
        trace_id="0123456789abcdef0123456789abcdef",
        ts=datetime.now(UTC),
    )
    payload_json = event.model_dump_json()
    parsed = InterruptEvent.model_validate_json(payload_json)
    assert parsed.kind == "interrupt"
    assert parsed.gate == "gate2"
    assert parsed.audit_summary is None


@pytest.mark.asyncio
async def test_publish_interrupt_upserts_legacy_status(monkeypatch) -> None:
    """publish_interrupt writes case.status = awaiting_review_gate2 + gate_state."""
    from src.services import pipeline_events

    case_uuid = UUID("00000000-0000-0000-0000-000000000abc")

    # Stub Redis side
    fake_redis = mock.AsyncMock()
    fake_redis.publish = mock.AsyncMock(return_value=1)
    monkeypatch.setattr(
        pipeline_events,
        "_get_redis_client",
        mock.AsyncMock(return_value=fake_redis),
    )
    # Suppress the fire-and-forget tee-write task creation
    monkeypatch.setattr(
        pipeline_events,
        "_tee_write",
        mock.AsyncMock(return_value=None),
    )

    # Stub DB session — capture the case mutation
    captured_status = []
    captured_gate_state = []

    class FakeCase:
        def __init__(self):
            self.status = None
            self.gate_state = None

    fake_case = FakeCase()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, model, key):
            assert key == case_uuid
            return fake_case

        async def commit(self):
            captured_status.append(fake_case.status)
            captured_gate_state.append(fake_case.gate_state)

    monkeypatch.setattr(
        "src.services.database.async_session",
        lambda: FakeSession(),
    )

    await pipeline_events.publish_interrupt(
        case_uuid,
        "gate2",
        {
            "actions": ["advance", "rerun", "halt"],
            "phase_output": {"items": []},
            "trace_id": "0123456789abcdef0123456789abcdef",
        },
    )

    assert fake_redis.publish.await_count == 1
    assert captured_status, "expected DB commit to fire"
    assert str(captured_status[-1].value) == "awaiting_review_gate2"
    assert captured_gate_state[-1]["current_gate"] == 2
    assert captured_gate_state[-1]["awaiting_review"] is True


@pytest.mark.asyncio
async def test_publish_interrupt_idempotent_on_replay(monkeypatch) -> None:
    """Re-firing publish_interrupt for the same (case, gate) is safe.

    The DB write is a UPDATE-WHERE-id (idempotent UPSERT). Calling twice
    with identical args produces identical post-state — no duplicate rows,
    no schema drift.
    """
    from src.services import pipeline_events

    case_uuid = UUID("00000000-0000-0000-0000-000000000abc")

    fake_redis = mock.AsyncMock()
    fake_redis.publish = mock.AsyncMock(return_value=1)
    monkeypatch.setattr(
        pipeline_events,
        "_get_redis_client",
        mock.AsyncMock(return_value=fake_redis),
    )
    monkeypatch.setattr(
        pipeline_events,
        "_tee_write",
        mock.AsyncMock(return_value=None),
    )

    class FakeCase:
        status = None
        gate_state = None

    fake_case = FakeCase()
    commits: list[tuple] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def get(self, _model, _key):
            return fake_case

        async def commit(self):
            commits.append((fake_case.status, dict(fake_case.gate_state or {})))

    monkeypatch.setattr(
        "src.services.database.async_session",
        lambda: FakeSession(),
    )

    payload = {"actions": ["advance", "rerun", "halt"]}
    for _ in range(3):
        await pipeline_events.publish_interrupt(case_uuid, "gate3", payload)

    assert len(commits) == 3
    # Each commit produced identical post-state
    assert all(c == commits[0] for c in commits)
