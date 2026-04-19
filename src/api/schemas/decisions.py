"""Judge decision request/response schemas."""

import enum
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
    message: str = Field(..., description="Confirmation message")


class AmendmentRequest(BaseModel):
    recommendation_type: str = Field(..., description="New recommendation type")
    recommended_outcome: str = Field(..., description="Updated outcome text")
    amendment_reason: str = Field(..., description="Reason for the amendment")


class VerdictHistoryResponse(BaseModel):
    verdict_id: UUID = Field(..., description="Verdict ID")
    recommendation_type: str = Field(..., description="Recommendation type")
    recommended_outcome: str = Field(..., description="Recommended outcome")
    confidence_score: int | None = Field(None, description="Optional confidence score")
    amendment_of: UUID | None = Field(None, description="Original verdict id if amended")
    amendment_reason: str | None = Field(None, description="Amendment reason")
    amended_by: UUID | None = Field(None, description="User id that amended verdict")


class DecisionHistoryListResponse(BaseModel):
    case_id: UUID = Field(..., description="Case ID")
    items: list[VerdictHistoryResponse] = Field(default_factory=list, description="Verdict history")
