"""Pydantic schemas for escalated case handling endpoints (US-024)."""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.case import CaseDomain, CaseStatus


class EscalationAction(str, enum.Enum):
    add_notes = "add_notes"
    return_to_pipeline = "return_to_pipeline"
    manual_decision = "manual_decision"
    reject = "reject"


class EscalationActionRequest(BaseModel):
    action: EscalationAction = Field(..., description="Action to take on the escalated case")
    notes: str | None = Field(None, description="Judge's review notes", max_length=2000)
    final_order: str | None = Field(
        None,
        description="Final order text — required when action is manual_decision",
        max_length=5000,
    )


class EscalatedCaseResponse(BaseModel):
    id: UUID
    domain: CaseDomain
    description: str | None = None
    status: CaseStatus
    route: str | None = None
    complexity: str | None = None
    created_by: UUID

    model_config = {"from_attributes": True}


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
