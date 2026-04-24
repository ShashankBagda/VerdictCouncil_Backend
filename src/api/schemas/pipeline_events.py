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
    mlflow_run_id: str | None = Field(
        None,
        description=(
            "MLflow run UUID for the nested agent run. Populated on the "
            "`completed` phase when MLflow tracing is enabled."
        ),
    )
    mlflow_experiment_id: str | None = Field(
        None,
        description=(
            "MLflow experiment id owning `mlflow_run_id`. Needed to build the "
            "MLflow UI URL because the path includes /experiments/<id>/runs/<run_id>."
        ),
    )


class AgentEvent(BaseModel):
    """Fine-grained agent telemetry: thinking, tool calls, LLM responses."""

    kind: Literal["agent"] = "agent"
    schema_version: Literal[1] = 1
    case_id: str
    agent: str
    event: Literal[
        "thinking", "tool_call", "tool_result", "llm_response", "agent_completed"
    ]
    content: str | None = None
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    result: str | None = None
    ts: str


class HeartbeatEvent(BaseModel):
    """Keepalive frame emitted on each SSE heartbeat tick."""

    kind: Literal["heartbeat"] = "heartbeat"
    schema_version: Literal[1] = 1
    ts: datetime


class AuthExpiringEvent(BaseModel):
    """Emitted ≥60 s before the session cookie expires so the client can redirect."""

    kind: Literal["auth_expiring"] = "auth_expiring"
    schema_version: Literal[1] = 1
    expires_at: datetime


#: Discriminated union of all SSE event shapes on both streaming endpoints.
Event = Annotated[
    PipelineProgressEvent | AgentEvent | HeartbeatEvent | AuthExpiringEvent,
    Field(discriminator="kind"),
]
