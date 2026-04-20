"""Schemas for hearing pack and hearing notes endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class HearingPackResponse(BaseModel):
    case_id: UUID = Field(..., description="Case ID")
    case_title: str = Field(..., description="Case title")
    domain: str = Field(..., description="Case domain")
    status: str = Field(..., description="Case status")
    parties: list[dict] = Field(default_factory=list)
    facts: list[dict] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    witnesses: list[dict] = Field(default_factory=list)
    legal_framework: list[dict] = Field(default_factory=list)
    arguments: dict = Field(default_factory=dict)
    current_verdict: dict | None = Field(default=None)
    created_at: datetime | None = Field(default=None)
    last_updated: datetime | None = Field(default=None)


class HearingNoteCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)
    section_reference: str | None = Field(None, max_length=255)
    note_type: str = Field(default="observation", max_length=50)


class HearingNoteUpdateRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=5000)
    section_reference: str | None = Field(default=None, max_length=255)
    note_type: str | None = Field(default=None, max_length=50)


class HearingNoteResponse(BaseModel):
    id: UUID
    case_id: UUID
    judge_id: UUID
    content: str
    section_reference: str | None = None
    note_type: str
    is_locked: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class HearingNoteListResponse(BaseModel):
    items: list[HearingNoteResponse] = Field(default_factory=list)
    total: int = 0
