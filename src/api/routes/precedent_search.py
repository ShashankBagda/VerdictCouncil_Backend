"""Ad-hoc PAIR API precedent search endpoint (US-016)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from src.api.deps import require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.precedent_search import (
    PrecedentSearchMetadata,
    PrecedentSearchRequest,
    PrecedentSearchResponse,
    PrecedentSearchResultItem,
)
from src.models.user import User, UserRole
from src.shared.sanitization import sanitize_user_input
from src.tools.search_precedents import search_precedents_with_meta

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/search",
    response_model=PrecedentSearchResponse,
    status_code=status.HTTP_200_OK,
    operation_id="search_precedents_adhoc",
    summary="Ad-hoc precedent search",
    description="Trigger a live PAIR API search for Singapore case law outside the pipeline. "
    "Falls back to the curated vector store when PAIR is unavailable. Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)
async def search_precedents_adhoc(
    body: PrecedentSearchRequest,
    current_user: User = require_role(UserRole.judge),
) -> PrecedentSearchResponse:
    clean_query = sanitize_user_input(body.query)
    clean_jurisdiction = sanitize_user_input(body.jurisdiction)
    if len(clean_query) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Query is too short after sanitization.",
        )

    try:
        result = await search_precedents_with_meta(
            query=clean_query,
            domain=clean_jurisdiction,
            max_results=body.max_results,
        )
    except Exception as exc:
        logger.exception("Unexpected error during precedent search: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Precedent search service is temporarily unavailable.",
        ) from exc

    items = [
        PrecedentSearchResultItem(
            citation=p.get("citation", ""),
            court=p.get("court"),
            outcome=p.get("outcome"),
            reasoning_summary=p.get("reasoning_summary"),
            similarity_score=p.get("similarity_score"),
            url=p.get("url"),
            source=p.get("source"),
        )
        for p in result.precedents
    ]

    return PrecedentSearchResponse(
        results=items,
        metadata=PrecedentSearchMetadata(
            source_failed=result.metadata.get("source_failed", False),
            fallback_used=result.metadata.get("fallback_used", False),
            pair_status=result.metadata.get("pair_status", "ok"),
        ),
        total=len(items),
    )
