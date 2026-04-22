"""Judge decision request/response schemas."""

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.case import CaseStatus


class DecisionAction(str, enum.Enum):
    accept = "accept"
    modify = "modify"
    reject = "reject"


class DecisionRequest(BaseModel):
    """Judge decision on a case verdict."""

    action: DecisionAction = Field(..., description="Decision action", examples=["accept"])
    notes: str | None = Field(None, description="Optional notes from the judge")
    final_order: str | None = Field(None, description="Final order text if modifying the verdict")


class DecisionResponse(BaseModel):
    """Confirmation of recorded decision."""

    case_id: UUID = Field(..., description="Case ID")
    action: DecisionAction = Field(..., description="Decision action taken")
    status: CaseStatus = Field(..., description="New case status after decision")
    decision_type: str = Field(..., description="Recorded decision type")
    reason: str | None = Field(None, description="Judge reason for modify or reject decisions")
    final_order: str | None = Field(None, description="Final order text when supplied")
    recorded_at: datetime | None = Field(None, description="Timestamp for the decision record")
    recorded_by: str | None = Field(None, description="Judge identifier")
    message: str = Field(..., description="Confirmation message")
