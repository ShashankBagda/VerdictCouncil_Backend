"""Domain request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.models.domain import DomainDocumentStatus


class PublicDomainResponse(BaseModel):
    """Minimal domain info for the public intake dropdown.

    Explicit allowlist — never expose vector_store_id or is_active to judges.
    has_vector_store is a safe derived boolean (no raw ID).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None = None
    has_vector_store: bool = False


class AdminDomainResponse(PublicDomainResponse):
    """Full domain info for admin management surfaces."""

    vector_store_id: str | None = None
    is_active: bool
    provisioning_started_at: datetime | None = None
    provisioning_attempts: int = 0
    created_at: datetime
    updated_at: datetime


class DomainCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$")
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None)


class DomainUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    is_active: bool | None = None


class DomainDocumentResponse(BaseModel):
    """Domain document metadata — no file bytes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    domain_id: UUID
    openai_file_id: str | None = None
    sanitized_file_id: str | None = None
    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None
    sanitized: bool
    status: DomainDocumentStatus
    error_reason: str | None = None
    uploaded_at: datetime


class DomainCapabilitiesResponse(BaseModel):
    uploads_enabled: bool
