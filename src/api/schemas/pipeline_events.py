"""Pipeline progress event schema for the SSE status stream (US-002)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PipelineProgressEvent(BaseModel):
    """A single progress event emitted while a case is being processed.

    The event covers two shapes:

    - **Per-agent progress** — ``agent`` is one of the 9 agent names,
      ``phase`` is ``"started"``/``"completed"``/``"failed"``, and
      ``step`` is the 1-indexed position in ``AGENT_ORDER``.
    - **Pipeline-level terminal** — ``agent == "pipeline"`` with
      ``phase == "terminal"`` is emitted on every halt path (L1
      escalation, L2 barrier timeout, governance halt, orchestrator
      exception, watchdog timeout). The subscriber uses this as the
      authoritative close signal. ``step`` is omitted; ``detail``
      carries ``{"reason": ..., "stopped_at": ...}`` so downstream
      analytics can attribute the halt to the correct stage without
      mislabelling it as a governance-verdict failure.
    """

    case_id: UUID
    agent: str = Field(
        ...,
        description=(
            "Per-agent events: one of the 9 agent names (case-processing, "
            "complexity-routing, evidence-analysis, fact-reconstruction, "
            "witness-analysis, legal-knowledge, argument-construction, "
            "deliberation, governance-verdict). Terminal events use "
            "'pipeline'."
        ),
    )
    phase: Literal["started", "completed", "failed", "terminal"]
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
            "Extra payload for terminal events: "
            "{'reason': <halt reason>, 'stopped_at': <mesh stage label>}."
        ),
    )
