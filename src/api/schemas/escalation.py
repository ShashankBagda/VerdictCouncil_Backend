"""Pydantic schemas for escalated case handling endpoints (US-024)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.case import CaseDomain, CaseStatus


class EscalationAction(str, enum.Enum):
    add_notes = "add_notes"
    return_to_pipeline = "return_to_pipeline"
    close = "close"


class EscalationActionRequest(BaseModel):
    action: EscalationAction = Field(..., description="Action to take on the escalated case")
    notes: str | None = Field(None, description="Judge's review notes", max_length=2000)


class WorkflowHistoryEntryResponse(BaseModel):
    action: str
    reason: str | None = None
    actor: str | None = None
    created_at: datetime | None = None
    details: dict[str, Any] | None = None


class EscalatedCaseResponse(BaseModel):
    id: str
    case_id: UUID
    item_type: str
    case_title: str | None = None
    domain: CaseDomain
    status: CaseStatus
    route: str | None = None
    complexity: str | None = None
    originating_judge: str | None = None
    reason: str | None = None
    priority: str = "high"
    submitted_at: datetime | None = None
    preview: str | None = None
    history: list[WorkflowHistoryEntryResponse] = Field(default_factory=list)


class EscalatedCaseListResponse(BaseModel):
    items: list[EscalatedCaseResponse]
    total: int
    page: int
    per_page: int


class EscalationActionResponse(BaseModel):
    case_id: UUID
    action: EscalationAction
    previous_status: CaseStatus
    new_status: CaseStatus
    message: str
