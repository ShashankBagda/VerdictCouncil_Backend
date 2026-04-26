"""Pipeline SSE event schema — versioned discriminated union (P2.13).

All events carry ``kind`` (discriminator) and ``schema_version`` so the
frontend can branch on type and forward-compatible clients can ignore
unknown versions.

Generate the JSON Schema doc with::

    python -c "
    import json
    from src.api.schemas.pipeline_events import Event
    from pydantic import TypeAdapter
    print(json.dumps(TypeAdapter(Event).json_schema(), indent=2))
    " > docs/sse-schema.json
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PipelineProgressEvent(BaseModel):
    """Per-agent lifecycle and pipeline-level terminal events.

    Two shapes share this model:

    - **Per-agent progress** — ``agent`` is one of the 9 agent names,
      ``phase`` is ``"started"``/``"completed"``/``"failed"``, and
      ``step`` is the 1-indexed position in ``AGENT_ORDER``.
    - **Pipeline-level terminal** — ``agent == "pipeline"`` with
      ``phase == "terminal"`` is emitted on every halt path (L1
      escalation, L2 barrier timeout, governance halt, orchestrator
      exception, watchdog timeout). ``step`` is omitted; ``detail``
      carries ``{"reason": ..., "stopped_at": ...}``.
    """

    kind: Literal["progress"] = "progress"
    schema_version: Literal[1] = 1
    case_id: UUID
    agent: str = Field(
        ...,
        description=(
            "Per-agent events: one of the 9 agent names (case-processing, "
            "complexity-routing, evidence-analysis, fact-reconstruction, "
            "witness-analysis, legal-knowledge, argument-construction, "
            "hearing-analysis, hearing-governance). Terminal events use "
            "'pipeline'."
        ),
    )
    phase: Literal["started", "completed", "failed", "terminal", "awaiting_review", "cancelled"]
    step: int | None = Field(
        None,
        ge=1,
        le=9,
        description="1-indexed AGENT_ORDER position; omitted for terminal events.",
    )
    total: int = 9
    ts: datetime
    error: str | None = Field(None, description="Truncated error message when phase=failed")
    detail: dict[str, Any] | None = Field(
        None,
        description=(
            "Extra payload for terminal events: {'reason': <halt reason>, 'stopped_at': <stage>}."
        ),
    )
    trace_id: str | None = Field(
        None,
        description=(
            "W3C OTEL trace id (32 lowercase hex chars). Sprint 2 2.C1.5; "
            "consumers tolerate absence for backward compat."
        ),
    )


class AgentEvent(BaseModel):
    """Fine-grained agent telemetry: thinking, tool calls, LLM responses."""

    kind: Literal["agent"] = "agent"
    schema_version: Literal[1] = 1
    case_id: str
    agent: str
    event: Literal["thinking", "tool_call", "tool_result", "llm_response", "agent_completed"]
    content: str | None = None
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    result: str | None = None
    ts: str
    trace_id: str | None = None


class AgentFailedEvent(BaseModel):
    """Q1.2 — terminal failure of an agent's stream after at least one
    chunk was emitted. Frontend consumers render this as a red error
    card; no retry happens at the SSE layer. Only the error CLASS is
    carried — the original message may contain prompt PII and is
    explicitly suppressed."""

    kind: Literal["agent"] = "agent"
    schema_version: Literal[1] = 1
    case_id: str
    agent: str
    event: Literal["agent_failed"]
    error_class: str = Field(
        ..., description="Python exception class name. NO error message — PII risk."
    )
    ts: str
    trace_id: str | None = None


class NarrationEvent(BaseModel):
    """Natural-language summary chunk emitted by an agent after its analysis."""

    kind: Literal["narration"] = "narration"
    schema_version: Literal[1] = 1
    case_id: str
    agent: str
    content: str
    chunk_index: int = 0
    ts: str
    trace_id: str | None = None


class InterruptEvent(BaseModel):
    """Gate-pause interrupt — judge must respond via /cases/{id}/respond.

    Emitted by `publish_interrupt(...)` (Sprint 4 4.A3.7) whenever the
    LangGraph pipeline pauses at a gate. The ``phase_output`` carries
    the per-gate review payload (intake / research / synthesis / audit
    output for gates 1-4 respectively). ``audit_summary`` is gate4-only
    and surfaces the optional `recommend_send_back` recommendation
    from the auditor (4.A3.14).
    """

    kind: Literal["interrupt"] = "interrupt"
    schema_version: Literal[1] = 1
    case_id: UUID
    gate: Literal["gate1", "gate2", "gate3", "gate4"]
    actions: list[str]
    phase_output: dict[str, Any] | None = None
    audit_summary: dict[str, Any] | None = None
    trace_id: str | None = None
    ts: datetime


class HeartbeatEvent(BaseModel):
    """Keepalive frame emitted on each SSE heartbeat tick."""

    kind: Literal["heartbeat"] = "heartbeat"
    schema_version: Literal[1] = 1
    ts: datetime
    trace_id: str | None = None


class AuthExpiringEvent(BaseModel):
    """Emitted ≥60 s before the session cookie expires so the client can redirect."""

    kind: Literal["auth_expiring"] = "auth_expiring"
    schema_version: Literal[1] = 1
    expires_at: datetime


#: Discriminated union of all SSE event shapes on both streaming endpoints.
#: `AgentEvent` and `AgentFailedEvent` share `kind="agent"` — narrow further
#: on the `event` field at consumer side.
Event = Annotated[
    PipelineProgressEvent
    | AgentEvent
    | AgentFailedEvent
    | NarrationEvent
    | InterruptEvent
    | HeartbeatEvent
    | AuthExpiringEvent,
    Field(discriminator="kind"),
]
