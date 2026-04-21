"""Pydantic schemas for the knowledge base endpoints (status + per-judge store)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class VectorStoreStatus(BaseModel):
    configured: bool
    store_id: str | None = None
    status: str  # "healthy" | "unavailable" | "not_configured"
    error: str | None = None


class PairApiStatus(BaseModel):
    service: str
    state: str
    failure_count: int
    failure_threshold: int | None = None
    recovery_timeout_seconds: int | None = None
    opened_at: float | None = None
    error: str | None = None


class KnowledgeBaseStatusResponse(BaseModel):
    pair_api: PairApiStatus
    vector_store: VectorStoreStatus
    last_checked: datetime
    # Per-judge fields (populated from the current user's vector store, if any).
    initialized: bool = False
    documents_count: int | None = None
    # Always null: OpenAI does not expose a total chunk count via the SDK.
    chunks_count: int | None = None
    last_updated_at: datetime | None = None


class KnowledgeBaseInitializeResponse(BaseModel):
    vector_store_id: str
    created: bool


class KnowledgeBaseDocument(BaseModel):
    id: str
    filename: str
    status: str
    bytes: int | None = None
    created_at: int | None = None


class KnowledgeBaseUploadResponse(BaseModel):
    id: str
    filename: str
    status: str


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseDocument]
    total: int


class KnowledgeBaseDeleteResponse(BaseModel):
    id: str
    deleted: bool


class KnowledgeBaseSearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=1000)
    limit: int | None = Field(default=5, ge=1, le=20)


class KnowledgeBaseSearchHit(BaseModel):
    file_id: str
    filename: str | None = None
    content: str
    score: float


class KnowledgeBaseSearchResponse(BaseModel):
    items: list[KnowledgeBaseSearchHit]
