"""Sprint 1 1.A1.1 — capture SSE wire-format golden fixtures.

Records the byte-exact JSON payloads + SSE-framed text the current
pipeline emits for every event type and the two key fan-out scenarios.
Subsequent Sprint 1 tasks (1.A1.2 middleware, 1.A1.3 stream adapter,
1.A1.4-7 topology rewrite) must keep these wire shapes stable so the
frontend SSE reader keeps working through the migration.

Production paths used:
- `PipelineProgressEvent.model_dump_json()` — `publish_progress`
  serialization in `services/pipeline_events.py:90`.
- `json.dumps({**dict_event}, default=str)` — `publish_agent_event` /
  `publish_narration` serialization in the same file.
- SSE framing `event: <kind>\\ndata: <json>\\n\\n` — `cases.py` stream
  handlers (e.g. line 1251 generic dispatch, 854 heartbeat).

Run with `uv run python scripts/capture_sse_goldens.py`. Fixtures land
under `tests/fixtures/sse_wire_format/`. Datetimes are pinned to fixed
ISO strings so re-running the script produces identical output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.api.schemas.pipeline_events import (
    AuthExpiringEvent,
    HeartbeatEvent,
    PipelineProgressEvent,
)

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sse_wire_format"

# Pinned timestamps so fixtures are byte-stable across runs.
FROZEN_TS = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)
FROZEN_TS_ISO = FROZEN_TS.isoformat()
FROZEN_CASE_ID = "11111111-1111-1111-1111-111111111111"
FROZEN_RUN_ID = "22222222-2222-2222-2222-222222222222"


def _sse_frame(kind: str, payload: str) -> str:
    """Match the framing used in src/api/routes/cases.py:1251 (generic dispatch)."""
    return f"event: {kind}\ndata: {payload}\n\n"


def _write(name: str, fixture: dict) -> Path:
    target = OUT_DIR / f"{name}.json"
    target.write_text(json.dumps(fixture, indent=2, sort_keys=False) + "\n")
    return target


# ---------------------------------------------------------------------------
# PipelineProgressEvent — published via `event.model_dump_json()`.
# Three shapes: per-agent started/completed plus pipeline-level terminal.
# ---------------------------------------------------------------------------


def fixture_progress_agent_started() -> dict:
    event = PipelineProgressEvent(
        case_id=FROZEN_CASE_ID,
        agent="evidence-analysis",
        phase="started",
        step=3,
        ts=FROZEN_TS,
    )
    payload = event.model_dump_json()
    return {
        "event_type": "progress",
        "scenario": "agent_started",
        "pydantic_class": "PipelineProgressEvent",
        "publisher": "publish_progress (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("progress", payload),
    }


def fixture_progress_agent_completed() -> dict:
    event = PipelineProgressEvent(
        case_id=FROZEN_CASE_ID,
        agent="evidence-analysis",
        phase="completed",
        step=3,
        ts=FROZEN_TS,
        mlflow_run_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        mlflow_experiment_id="0",
    )
    payload = event.model_dump_json()
    return {
        "event_type": "progress",
        "scenario": "agent_completed",
        "pydantic_class": "PipelineProgressEvent",
        "publisher": "publish_progress (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("progress", payload),
    }


def fixture_progress_pipeline_terminal() -> dict:
    event = PipelineProgressEvent(
        case_id=FROZEN_CASE_ID,
        agent="pipeline",
        phase="terminal",
        step=None,
        ts=FROZEN_TS,
        detail={"reason": "completed", "stopped_at": "hearing-governance"},
    )
    payload = event.model_dump_json()
    return {
        "event_type": "progress",
        "scenario": "pipeline_terminal",
        "pydantic_class": "PipelineProgressEvent",
        "publisher": "publish_progress (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("progress", payload),
    }


# ---------------------------------------------------------------------------
# AgentEvent — published via `json.dumps({...}, default=str)` in
# `publish_agent_event`. The publisher injects `kind` + `schema_version`,
# so the fixtures match what subscribers actually see on the wire.
# ---------------------------------------------------------------------------


def _agent_event(extra: dict) -> dict:
    """Replicate `publish_agent_event`'s stamping (services/pipeline_events.py:112)."""
    base = {
        "kind": "agent",
        "schema_version": 1,
        "case_id": FROZEN_CASE_ID,
        "ts": FROZEN_TS_ISO,
    }
    return {**base, **extra}


def fixture_agent_thinking() -> dict:
    event = _agent_event(
        {
            "agent": "evidence-analysis",
            "event": "thinking",
            "content": "→ gpt-5 · tools=2",
        }
    )
    payload = json.dumps(event, default=str)
    return {
        "event_type": "agent",
        "scenario": "thinking",
        "pydantic_class": "AgentEvent",
        "publisher": "publish_agent_event (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("agent", payload),
    }


def fixture_agent_tool_call() -> dict:
    event = _agent_event(
        {
            "agent": "legal-knowledge",
            "event": "tool_call",
            "tool_name": "search_precedents",
            "args": {"query": "fair use of copyrighted material", "top_k": 3},
        }
    )
    payload = json.dumps(event, default=str)
    return {
        "event_type": "agent",
        "scenario": "tool_call",
        "pydantic_class": "AgentEvent",
        "publisher": "publish_agent_event (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("agent", payload),
    }


def fixture_agent_tool_result() -> dict:
    event = _agent_event(
        {
            "agent": "legal-knowledge",
            "event": "tool_result",
            "tool_name": "search_precedents",
            "result": "[{'case_name': 'Smith v Jones', 'year': 2018, 'score': 0.83}]",
        }
    )
    payload = json.dumps(event, default=str)
    return {
        "event_type": "agent",
        "scenario": "tool_result",
        "pydantic_class": "AgentEvent",
        "publisher": "publish_agent_event (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("agent", payload),
    }


def fixture_agent_llm_response() -> dict:
    event = _agent_event(
        {
            "agent": "evidence-analysis",
            "event": "llm_response",
            "content": "Identified 3 supporting exhibits and 1 contradiction.",
        }
    )
    payload = json.dumps(event, default=str)
    return {
        "event_type": "agent",
        "scenario": "llm_response",
        "pydantic_class": "AgentEvent",
        "publisher": "publish_agent_event (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("agent", payload),
    }


# ---------------------------------------------------------------------------
# NarrationEvent — published via `json.dumps({...}, default=str)` in
# `publish_narration` (services/pipeline_events.py:142).
# ---------------------------------------------------------------------------


def fixture_narration() -> dict:
    event = {
        "kind": "narration",
        "schema_version": 1,
        "case_id": FROZEN_CASE_ID,
        "agent": "argument-construction",
        "content": (
            "Drafting the prosecution's opening based on the analyzed "
            "evidence and witness testimony."
        ),
        "chunk_index": 0,
        "ts": FROZEN_TS_ISO,
    }
    payload = json.dumps(event, default=str)
    return {
        "event_type": "narration",
        "scenario": "single_chunk",
        "pydantic_class": "NarrationEvent",
        "publisher": "publish_narration (services/pipeline_events.py)",
        "wire_payload": payload,
        "sse_frame": _sse_frame("narration", payload),
    }


# ---------------------------------------------------------------------------
# HeartbeatEvent — emitted directly inside the SSE handler, not via Redis.
# Wire format from `cases.py:854` (status stream) and `1231` (events stream).
# ---------------------------------------------------------------------------


def fixture_heartbeat() -> dict:
    heartbeat_dict = {
        "kind": "heartbeat",
        "schema_version": 1,
        "ts": FROZEN_TS_ISO,
    }
    payload = json.dumps(heartbeat_dict)
    pydantic_payload = HeartbeatEvent(ts=FROZEN_TS).model_dump_json()
    return {
        "event_type": "heartbeat",
        "scenario": "idle_keepalive",
        "pydantic_class": "HeartbeatEvent",
        "publisher": "inline in cases.py SSE generator (no Redis hop)",
        "wire_payload": payload,
        "wire_payload_via_pydantic": pydantic_payload,
        "sse_frame": _sse_frame("heartbeat", payload),
    }


# ---------------------------------------------------------------------------
# AuthExpiringEvent — emitted directly inside the SSE handler when the
# session cookie is within 60s of expiry (cases.py:1243).
# ---------------------------------------------------------------------------


def fixture_auth_expiring() -> dict:
    expires_at = datetime(2026, 4, 25, 13, 0, 0, tzinfo=UTC)
    auth_dict = {
        "kind": "auth_expiring",
        "schema_version": 1,
        "expires_at": expires_at.isoformat(),
    }
    payload = json.dumps(auth_dict)
    pydantic_payload = AuthExpiringEvent(expires_at=expires_at).model_dump_json()
    return {
        "event_type": "auth_expiring",
        "scenario": "session_60s_warning",
        "pydantic_class": "AuthExpiringEvent",
        "publisher": "inline in cases.py SSE generator (no Redis hop)",
        "wire_payload": payload,
        "wire_payload_via_pydantic": pydantic_payload,
        "sse_frame": _sse_frame("auth_expiring", payload),
    }


# ---------------------------------------------------------------------------
# Scenario fixtures — multi-event sequences that exercise fan-out + tool loops.
# These are the regression targets the breakdown calls out: "Includes a
# multi-tool-call run AND a Gate 2 (4 parallel agents) run for fan-out coverage".
# ---------------------------------------------------------------------------


def fixture_scenario_multi_tool_call() -> dict:
    """One agent making two tool calls before producing its final response."""
    sequence: list[dict] = []
    agent = "legal-knowledge"

    sequence.append(
        _agent_event({"agent": agent, "event": "thinking", "content": "→ gpt-5 · tools=2"})
    )
    sequence.append(
        _agent_event(
            {
                "agent": agent,
                "event": "tool_call",
                "tool_name": "search_precedents",
                "args": {"query": "negligence threshold", "top_k": 5},
            }
        )
    )
    sequence.append(
        _agent_event(
            {
                "agent": agent,
                "event": "tool_result",
                "tool_name": "search_precedents",
                "result": "[3 results]",
            }
        )
    )
    sequence.append(
        _agent_event(
            {
                "agent": agent,
                "event": "tool_call",
                "tool_name": "search_domain_guidance",
                "args": {"query": "small claims threshold limits"},
            }
        )
    )
    sequence.append(
        _agent_event(
            {
                "agent": agent,
                "event": "tool_result",
                "tool_name": "search_domain_guidance",
                "result": "[1 result]",
            }
        )
    )
    sequence.append(
        _agent_event(
            {
                "agent": agent,
                "event": "llm_response",
                "content": "Two relevant precedents and the SCT threshold both apply.",
            }
        )
    )

    frames = [_sse_frame("agent", json.dumps(ev, default=str)) for ev in sequence]
    return {
        "event_type": "agent",
        "scenario": "multi_tool_call",
        "pydantic_class": "AgentEvent",
        "publisher": "publish_agent_event (services/pipeline_events.py)",
        "sequence": sequence,
        "sse_frame_concatenated": "".join(frames),
    }


def fixture_scenario_gate2_fanout() -> dict:
    """Gate 2 dispatch fans out to 4 parallel agents — capture all 4 starts.

    Order in the current implementation is fixed by `builder.py` static edges;
    this fixture pins the expected order so any reordering by the topology
    rewrite (1.A1.4 / 1.A1.5) is detected.
    """
    gate2_agents = [
        "evidence-analysis",
        "fact-reconstruction",
        "witness-analysis",
        "legal-knowledge",
    ]
    sequence: list[dict] = []
    for idx, agent in enumerate(gate2_agents, start=3):  # AGENT_ORDER positions 3..6
        event = PipelineProgressEvent(
            case_id=FROZEN_CASE_ID,
            agent=agent,
            phase="started",
            step=idx,
            ts=FROZEN_TS,
        )
        sequence.append(json.loads(event.model_dump_json()))

    frames = [_sse_frame("progress", json.dumps(ev)) for ev in sequence]
    return {
        "event_type": "progress",
        "scenario": "gate2_fanout",
        "pydantic_class": "PipelineProgressEvent",
        "publisher": "publish_progress (services/pipeline_events.py)",
        "sequence": sequence,
        "sse_frame_concatenated": "".join(frames),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


FIXTURES: dict[str, callable] = {
    "progress_agent_started": fixture_progress_agent_started,
    "progress_agent_completed": fixture_progress_agent_completed,
    "progress_pipeline_terminal": fixture_progress_pipeline_terminal,
    "agent_thinking": fixture_agent_thinking,
    "agent_tool_call": fixture_agent_tool_call,
    "agent_tool_result": fixture_agent_tool_result,
    "agent_llm_response": fixture_agent_llm_response,
    "narration": fixture_narration,
    "heartbeat": fixture_heartbeat,
    "auth_expiring": fixture_auth_expiring,
    "scenario_multi_tool_call": fixture_scenario_multi_tool_call,
    "scenario_gate2_fanout": fixture_scenario_gate2_fanout,
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, factory in FIXTURES.items():
        path = _write(name, factory())
        print(f"wrote {path.relative_to(OUT_DIR.parent.parent.parent)}")


if __name__ == "__main__":
    main()
