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
      mislabelling it as a hearing-governance failure.
    """

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
    phase: Literal["started", "completed", "failed", "terminal", "awaiting_review"]
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
            "Extra payload for terminal events: {'reason': <halt reason>, 'stopped_at': <mesh stage label>}."  # noqa: E501
        ),
    )
    mlflow_run_id: str | None = Field(
        None,
        description=(
            "MLflow run UUID for the nested agent run. Populated on the "
            "`completed` phase when MLflow tracing is enabled; the frontend "
            "uses it to link the building-simulation cards to the MLflow UI."
        ),
    )
    mlflow_experiment_id: str | None = Field(
        None,
        description=(
            "MLflow experiment id owning `mlflow_run_id`. Needed to build the "
            "MLflow UI URL because the path includes /experiments/<id>/runs/<run_id>."
        ),
    )
