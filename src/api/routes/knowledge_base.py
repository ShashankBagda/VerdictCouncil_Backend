"""Knowledge base endpoints — vector store health + per-judge CRUD (US-017)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from openai import AsyncOpenAI

from src.api.deps import CurrentUser, DBSession
from src.api.schemas.common import ErrorResponse
from src.api.schemas.knowledge_base import (
    KnowledgeBaseStatusResponse,
    PairApiStatus,
    VectorStoreStatus,
)
from src.services import knowledge_base as kb_service
from src.shared.circuit_breaker import get_pair_search_breaker
from src.shared.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


@router.get(
    "/status",
    response_model=KnowledgeBaseStatusResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_knowledge_base_status",
    summary="Knowledge base and PAIR API health status",
    description="Returns the PAIR API circuit breaker state and vector store health. "
    "Requires authenticated user.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def get_knowledge_base_status(
    current_user: CurrentUser,
) -> KnowledgeBaseStatusResponse:
    # PAIR circuit breaker status
    pair_status_dict = await get_pair_search_breaker().get_status()
    pair_api = PairApiStatus(
        service=pair_status_dict.get("service", "pair_search"),
        state=pair_status_dict.get("state", "unknown"),
        failure_count=pair_status_dict.get("failure_count", -1),
        failure_threshold=pair_status_dict.get("failure_threshold"),
        recovery_timeout_seconds=pair_status_dict.get("recovery_timeout_seconds"),
        opened_at=pair_status_dict.get("opened_at"),
        error=pair_status_dict.get("error"),
    )

    # Vector store health
    store_id = settings.openai_vector_store_id
    if not store_id:
        vector_store = VectorStoreStatus(
            configured=False,
            store_id=None,
            status="not_configured",
        )
    else:
        try:
            await _get_openai_client().beta.vector_stores.retrieve(store_id)
            vector_store = VectorStoreStatus(
                configured=True,
                store_id=store_id,
                status="healthy",
            )
        except Exception as exc:
            logger.warning("Vector store health check failed: %s", exc)
            vector_store = VectorStoreStatus(
                configured=True,
                store_id=store_id,
                status="unavailable",
                error="Vector store health check failed. See server logs for details.",
            )

    return KnowledgeBaseStatusResponse(
        pair_api=pair_api,
        vector_store=vector_store,
        last_checked=datetime.now(UTC),
    )


@router.post(
    "/initialize",
    operation_id="initialize_knowledge_base",
    summary="Create a personal vector store for the current judge",
    status_code=status.HTTP_201_CREATED,
)
async def initialize_kb(
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    if current_user.openai_vector_store_id:
        return {
            "vector_store_id": current_user.openai_vector_store_id,
            "message": "Already initialized",
        }

    store_id = await kb_service.create_judge_vector_store(str(current_user.id))
    current_user.openai_vector_store_id = store_id
    await db.flush()

    return {"vector_store_id": store_id, "message": "Knowledge base created"}


@router.post(
    "/documents",
    operation_id="upload_kb_document",
    summary="Upload a document to the judge's knowledge base",
)
async def upload_kb_document(
    current_user: CurrentUser,
    db: DBSession,
    file: UploadFile = File(...),
) -> dict:
    if not current_user.openai_vector_store_id:
        raise HTTPException(
            status_code=400, detail="Knowledge base not initialized. Call POST /initialize first."
        )

    file_bytes = await file.read()
    result = await kb_service.upload_document_to_kb(
        current_user.openai_vector_store_id,
        file_bytes,
        file.filename or "document",
    )
    return result


@router.get(
    "/documents",
    operation_id="list_kb_documents",
    summary="List documents in the judge's knowledge base",
)
async def list_kb_documents(
    current_user: CurrentUser,
) -> dict:
    if not current_user.openai_vector_store_id:
        return {"documents": [], "initialized": False}

    docs = await kb_service.list_kb_files(current_user.openai_vector_store_id)
    return {"documents": docs, "initialized": True}


@router.delete(
    "/documents/{file_id}",
    operation_id="delete_kb_document",
    summary="Delete a document from the judge's knowledge base",
)
async def delete_kb_document(
    file_id: str,
    current_user: CurrentUser,
) -> dict:
    if not current_user.openai_vector_store_id:
        raise HTTPException(status_code=400, detail="Knowledge base not initialized")

    await kb_service.delete_kb_file(current_user.openai_vector_store_id, file_id)
    return {"message": "Document deleted", "file_id": file_id}


@router.post(
    "/search",
    operation_id="search_kb",
    summary="Search the judge's knowledge base",
)
async def search_kb_endpoint(
    body: dict,
    current_user: CurrentUser,
) -> dict:
    if not current_user.openai_vector_store_id:
        return {"results": [], "message": "Knowledge base not initialized"}

    query = body.get("query", "")
    max_results = body.get("max_results", 5)
    results = await kb_service.search_kb(
        current_user.openai_vector_store_id,
        query,
        max_results=max_results,
    )
    return {"results": results, "query": query}
