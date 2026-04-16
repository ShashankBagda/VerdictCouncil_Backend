"""Pipeline progress event schema for the SSE status stream (US-002)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class PipelineProgressEvent(BaseModel):
    """A single agent-level progress event emitted while a case is being processed."""

    case_id: UUID
    agent: str = Field(
        ...,
        description=(
            "One of the 9 agent names: case-processing, complexity-routing, "
            "evidence-analysis, fact-reconstruction, witness-analysis, "
            "legal-knowledge, argument-construction, deliberation, "
            "governance-verdict"
        ),
    )
    phase: Literal["started", "completed", "failed"]
    step: int = Field(..., ge=1, le=9, description="Position in AGENT_ORDER (1-indexed)")
    total: int = 9
    ts: datetime
    error: str | None = Field(None, description="Truncated error message when phase=failed")
