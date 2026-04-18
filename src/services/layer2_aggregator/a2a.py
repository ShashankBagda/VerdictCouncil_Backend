"""A2A (JSON-RPC 2.0) envelope helpers for the Layer-2 aggregator.

The aggregator receives `SendTaskResponse` envelopes from L2 agents on
`verdictcouncil/a2a/v1/agent/response/layer2-aggregator/<sub_task_id>`
and, when the barrier fires, emits a `SendTaskResponse` to the mesh
runner's reply-to topic.

These helpers keep the parse/build logic isolated so the component
focuses on orchestration. The SAM reference types live at
`solace_agent_mesh/common/types.py` — we mirror only the subset we
touch (shape, not schema validation).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

DATA_PART_TYPE = "data"
TEXT_PART_TYPE = "text"


def parse_send_task_response(raw: Any) -> dict:
    """Extract the domain payload from an inbound `SendTaskResponse`.

    Accepts the message body as dict, bytes, or str. Returns the first
    DataPart's ``data`` dict from ``result.status.message.parts``. If
    no DataPart is present, falls back to parsing the first TextPart's
    ``text`` field as JSON. Returns {} if neither is usable so the
    caller can log-and-drop rather than crash.
    """
    envelope = _coerce(raw)
    result = envelope.get("result") or {}
    status = result.get("status") or {}
    message = status.get("message") or {}
    parts = message.get("parts") or []

    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == DATA_PART_TYPE:
            data = part.get("data")
            if isinstance(data, dict):
                return data
        elif part_type == TEXT_PART_TYPE:
            text = part.get("text")
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
    return {}


def build_send_task_response(
    task_id: str,
    session_id: str | None,
    merged_state: dict,
) -> dict:
    """Build a `SendTaskResponse` envelope carrying the merged CaseState.

    Used when the L2 barrier fires and the aggregator emits the final
    result back to the mesh runner. The merged state rides as a single
    DataPart so the runner can round-trip it without string parsing.
    """
    return {
        "jsonrpc": "2.0",
        "id": task_id,
        "result": {
            "id": task_id,
            "sessionId": session_id,
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"type": DATA_PART_TYPE, "data": merged_state}],
                },
                "timestamp": datetime.now(UTC).isoformat(),
            },
            "artifacts": None,
            "history": None,
            "metadata": None,
        },
    }


def sub_task_id_from_topic(topic: str) -> str:
    """Return the trailing `/`-segment of the response topic.

    Response topics look like
    ``verdictcouncil/a2a/v1/agent/response/layer2-aggregator/<sub_task_id>``;
    the aggregator uses the final segment to look up its Redis
    correlation mapping.
    """
    if not topic:
        return ""
    return topic.rsplit("/", 1)[-1]


def _coerce(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}
