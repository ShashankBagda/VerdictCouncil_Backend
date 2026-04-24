"""Workflow schemas for supplementary uploads and senior inbox."""

from __future__ import annotations

import enum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from src.api.schemas.cases import DocumentResponse
from src.models.case import CaseStatus


class SupplementaryUploadRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=5000)


class SupplementaryUploadResponse(BaseModel):
    case_id: UUID
    documents: list[DocumentResponse] = Field(default_factory=list)
    retriggered_stages: list[str] = Field(default_factory=list)
    preserved_stages: list[str] = Field(default_factory=list)
    status: CaseStatus
    message: str


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
