"""Pydantic schemas for ad-hoc precedent search endpoint (US-016)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PrecedentSearchRequest(BaseModel):
    query: str = Field(..., description="Free-text search query", min_length=3, max_length=500)
    jurisdiction: str = Field(default="small_claims", description="Legal domain / jurisdiction")
    max_results: int = Field(default=5, ge=1, le=20, description="Maximum results to return")


class PrecedentSearchResultItem(BaseModel):
    citation: str
    court: str | None = None
    outcome: str | None = None
    reasoning_summary: str | None = None
    similarity_score: float | None = None
    url: str | None = None
    source: str | None = None


class PrecedentSearchMetadata(BaseModel):
    source_failed: bool = False
    fallback_used: bool = False
    pair_status: str = "ok"


class PrecedentSearchResponse(BaseModel):
    results: list[PrecedentSearchResultItem]
    metadata: PrecedentSearchMetadata
    total: int
