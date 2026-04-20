"""Schemas for case reopen workflow endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ReopenRequestCreateRequest(BaseModel):
    reason: str = Field(..., max_length=50)
    justification: str = Field(..., min_length=1, max_length=5000)


class ReopenRequestReviewRequest(BaseModel):
    approve: bool = Field(..., description="Approve or reject the reopen request")
    review_notes: str | None = Field(default=None, max_length=2000)


class ReopenRequestResponse(BaseModel):
    id: UUID
    case_id: UUID
    requested_by: UUID
    reason: str
    justification: str
    status: str
    reviewed_by: UUID | None = None
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ReopenRequestListResponse(BaseModel):
    items: list[ReopenRequestResponse] = Field(default_factory=list)
    total: int = 0
