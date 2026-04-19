"""Knowledge base status endpoint — vector store and PAIR API health (US-017)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from openai import AsyncOpenAI

from src.api.deps import require_role
from src.api.schemas.common import ErrorResponse, MessageResponse
from src.api.schemas.knowledge_base import (
    KnowledgeBaseDocument,
    KnowledgeBaseDocumentListResponse,
    KnowledgeBaseSearchRequest,
    KnowledgeBaseSearchResponse,
    KnowledgeBaseStatusResponse,
    PairApiStatus,
    VectorStoreStatus,
)
from src.models.user import User, UserRole
from src.services.knowledge_base_store import (
    delete_document,
    initialize_store,
    list_documents,
    save_document,
    search_documents,
)
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
    "Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def get_knowledge_base_status(
    current_user: User = require_role(UserRole.judge),
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
    response_model=MessageResponse,
    operation_id="initialize_knowledge_base",
    summary="Initialize knowledge base storage",
    description="Prepare local knowledge base storage and metadata index.",
    responses={403: {"model": ErrorResponse, "description": "Insufficient permissions"}},
)
async def initialize_knowledge_base(
    current_user: User = require_role(UserRole.judge),
) -> dict:
    initialize_store()
    return {"message": "Knowledge base initialized"}


@router.get(
    "/documents",
    response_model=KnowledgeBaseDocumentListResponse,
    operation_id="list_knowledge_base_documents",
    summary="List knowledge base documents",
)
async def list_knowledge_base_documents(
    current_user: User = require_role(UserRole.judge),
) -> dict:
    data = list_documents()
    return data


@router.post(
    "/documents",
    response_model=KnowledgeBaseDocument,
    operation_id="upload_knowledge_base_document",
    summary="Upload a document to the knowledge base",
)
async def upload_knowledge_base_document(
    file: UploadFile = File(...),
    current_user: User = require_role(UserRole.judge),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required")

    return await save_document(file)


@router.delete(
    "/documents/{file_id}",
    response_model=MessageResponse,
    operation_id="delete_knowledge_base_document",
    summary="Delete a knowledge base document",
)
async def delete_knowledge_base_document(
    file_id: str,
    current_user: User = require_role(UserRole.judge),
) -> dict:
    deleted = delete_document(file_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return {"message": "Document deleted"}


@router.post(
    "/search",
    response_model=KnowledgeBaseSearchResponse,
    operation_id="search_knowledge_base",
    summary="Search the knowledge base",
)
async def search_knowledge_base(
    body: KnowledgeBaseSearchRequest,
    current_user: User = require_role(UserRole.judge),
) -> dict:
    return search_documents(body.query, limit=body.limit)
