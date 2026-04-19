"""Pydantic schemas for knowledge base status endpoint (US-017)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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


class KnowledgeBaseDocument(BaseModel):
    file_id: str
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    uploaded_at: datetime | str | None = None
    preview: str | None = None


class KnowledgeBaseDocumentListResponse(BaseModel):
    initialized: bool
    items: list[KnowledgeBaseDocument]
    total: int


class KnowledgeBaseSearchRequest(BaseModel):
    query: str
    limit: int = 10


class KnowledgeBaseSearchResult(BaseModel):
    file_id: str
    filename: str
    content: str
    score: float


class KnowledgeBaseSearchResponse(BaseModel):
    items: list[KnowledgeBaseSearchResult]
    total: int
