"""Workflow schemas for rejection review, supplementary uploads, amendments, and senior inbox."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.api.schemas.cases import DocumentResponse
from src.models.case import CaseStatus


class RejectedCaseAction(str, enum.Enum):
    override = "override"
    close = "close"


class RejectionReviewRequest(BaseModel):
    action: RejectedCaseAction
    justification: str = Field(..., min_length=1, max_length=5000)


class RejectionReviewResponse(BaseModel):
    case_id: UUID
    action: RejectedCaseAction
    status: CaseStatus
    rejection_reason: str | None = None
    resumed_from_stage: str | None = None
    message: str


class SupplementaryUploadRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=5000)


class SupplementaryUploadResponse(BaseModel):
    case_id: UUID
    documents: list[DocumentResponse] = Field(default_factory=list)
    retriggered_stages: list[str] = Field(default_factory=list)
    preserved_stages: list[str] = Field(default_factory=list)
    status: CaseStatus
    message: str


class AmendmentType(str, enum.Enum):
    clerical_correction = "clerical_correction"
    post_hearing_update = "post_hearing_update"
    error_correction = "error_correction"


class DecisionAmendmentRequest(BaseModel):
    amendment_type: AmendmentType
    reason: str = Field(..., min_length=1, max_length=5000)
    final_order: str = Field(..., min_length=1, max_length=10000)
    notes: str | None = Field(default=None, max_length=5000)


class DecisionAmendmentResponse(BaseModel):
    case_id: UUID
    amendment_request_id: UUID | None = None
    amendment_type: AmendmentType
    status: str
    message: str
    amended_verdict_id: UUID | None = None


class SeniorInboxAction(str, enum.Enum):
    approve = "approve"
    reject = "reject"
    reassign = "reassign"
    request_more_info = "request_more_info"


class SeniorInboxActionRequest(BaseModel):
    action: SeniorInboxAction
    reason: str | None = Field(default=None, max_length=5000)
    assignee: str | None = Field(default=None, max_length=255)


class SeniorInboxActionResponse(BaseModel):
    item_id: str
    action: SeniorInboxAction
    status: str
    message: str
    assignee: str | None = None
    reviewed_at: datetime
