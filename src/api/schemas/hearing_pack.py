"""Hearing pack manifest schema (US-020)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class HearingPackManifest(BaseModel):
    """Top-level metadata embedded as ``manifest.json`` inside the zip."""

    case_id: UUID
    domain: str
    status: str
    generated_at: datetime
    files: list[str] = Field(..., description="Names of files included in this pack, in order")
    counts: dict[str, int] = Field(..., description="Per-section row counts (parties, evidence, facts, arguments)")
