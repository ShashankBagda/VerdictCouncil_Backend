"""Knowledge base endpoints: global health (US-017) + per-judge vector store CRUD."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from openai import AsyncOpenAI

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.knowledge_base import (
    KnowledgeBaseDeleteResponse,
    KnowledgeBaseDocument,
    KnowledgeBaseInitializeResponse,
    KnowledgeBaseListResponse,
    KnowledgeBaseSearchRequest,
    KnowledgeBaseSearchResponse,
    KnowledgeBaseStatusResponse,
    KnowledgeBaseUploadResponse,
    PairApiStatus,
    VectorStoreStatus,
)
from src.models.user import User, UserRole
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


def _kb_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Vector store unavailable. See server logs.",
    )


@router.get(
    "/status",
    response_model=KnowledgeBaseStatusResponse,
    status_code=status.HTTP_200_OK,
    operation_id="get_knowledge_base_status",
    summary="Knowledge base and PAIR API health status",
    description="Returns PAIR circuit breaker state, global vector store health, "
    "and per-judge store metadata (if initialized). Requires judge role.",
    responses={403: {"model": ErrorResponse, "description": "Insufficient permissions"}},
)
async def get_knowledge_base_status(
    current_user: User = require_role(UserRole.judge),
) -> KnowledgeBaseStatusResponse:
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

    global_store_id = settings.openai_vector_store_id
    if not global_store_id:
        vector_store = VectorStoreStatus(configured=False, store_id=None, status="not_configured")
    else:
        try:
            await _get_openai_client().vector_stores.retrieve(global_store_id)
            vector_store = VectorStoreStatus(
                configured=True, store_id=global_store_id, status="healthy"
            )
        except Exception as exc:
            logger.warning("Vector store health check failed: %s", exc)
            vector_store = VectorStoreStatus(
                configured=True,
                store_id=global_store_id,
                status="unavailable",
                error="Vector store health check failed. See server logs for details.",
            )

    # Per-judge store: single retrieve pulls file_counts.total + last_active_at.
    initialized = False
    documents_count: int | None = None
    last_updated_at: datetime | None = None
    judge_store_id = current_user.knowledge_base_vector_store_id
    if judge_store_id:
        initialized = True
        try:
            judge_store = await _get_openai_client().vector_stores.retrieve(judge_store_id)
            file_counts = getattr(judge_store, "file_counts", None)
            if file_counts is not None:
                documents_count = getattr(file_counts, "total", None)
            last_active = getattr(judge_store, "last_active_at", None)
            if last_active:
                last_updated_at = datetime.fromtimestamp(last_active, tz=UTC)
        except Exception as exc:
            logger.warning("Per-judge vector store retrieve failed: %s", exc)

    return KnowledgeBaseStatusResponse(
        pair_api=pair_api,
        vector_store=vector_store,
        last_checked=datetime.now(UTC),
        initialized=initialized,
        documents_count=documents_count,
        chunks_count=None,
        last_updated_at=last_updated_at,
    )


@router.post(
    "/initialize",
    response_model=KnowledgeBaseInitializeResponse,
    status_code=status.HTTP_200_OK,
    operation_id="initialize_knowledge_base",
    summary="Provision a per-judge vector store (idempotent)",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        503: {"model": ErrorResponse, "description": "Vector store unavailable"},
    },
)
async def initialize_knowledge_base(
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> KnowledgeBaseInitializeResponse:
    try:
        store_id, created = await kb_service.ensure_judge_vector_store(db, current_user)
    except Exception as exc:
        logger.exception("Failed to initialize judge knowledge base: %s", exc)
        raise _kb_unavailable() from exc
    return KnowledgeBaseInitializeResponse(vector_store_id=store_id, created=created)


@router.post(
    "/documents",
    response_model=KnowledgeBaseUploadResponse,
    status_code=status.HTTP_200_OK,
    operation_id="upload_knowledge_base_document",
    summary="Upload a document to the judge's knowledge base",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        413: {"model": ErrorResponse, "description": "File too large"},
        503: {"model": ErrorResponse, "description": "Vector store unavailable"},
    },
)
async def upload_knowledge_base_document(
    db: DBSession,
    file: UploadFile = File(...),
    filename_override: str | None = Form(default=None),
    current_user: User = require_role(UserRole.judge),
) -> KnowledgeBaseUploadResponse:
    contents = await file.read()
    if len(contents) > settings.kb_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(f"File exceeds max upload size of {settings.kb_max_upload_bytes} bytes."),
        )

    try:
        store_id, _created = await kb_service.ensure_judge_vector_store(db, current_user)
        resolved_name = filename_override or file.filename or "document"
        result = await kb_service.upload_document_to_kb(
            vector_store_id=store_id,
            file_bytes=contents,
            filename=resolved_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to upload to judge knowledge base: %s", exc)
        raise _kb_unavailable() from exc

    return KnowledgeBaseUploadResponse(
        id=result["file_id"],
        filename=result["filename"],
        status=result.get("status") or "indexed",
    )


@router.get(
    "/documents",
    response_model=KnowledgeBaseListResponse,
    status_code=status.HTTP_200_OK,
    operation_id="list_knowledge_base_documents",
    summary="List documents in the judge's knowledge base",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        503: {"model": ErrorResponse, "description": "Vector store unavailable"},
    },
)
async def list_knowledge_base_documents(
    current_user: User = require_role(UserRole.judge),
) -> KnowledgeBaseListResponse:
    store_id = current_user.knowledge_base_vector_store_id
    if not store_id:
        return KnowledgeBaseListResponse(items=[], total=0)
    try:
        rows = await kb_service.list_kb_files(store_id)
    except Exception as exc:
        logger.exception("Failed to list judge knowledge base: %s", exc)
        raise _kb_unavailable() from exc

    items = [
        KnowledgeBaseDocument(
            id=row["file_id"],
            filename=row.get("filename") or "unknown",
            status=row.get("status") or "unknown",
            bytes=row.get("bytes"),
            created_at=row.get("created_at"),
        )
        for row in rows
    ]
    return KnowledgeBaseListResponse(items=items, total=len(items))


@router.delete(
    "/documents/{file_id}",
    response_model=KnowledgeBaseDeleteResponse,
    status_code=status.HTTP_200_OK,
    operation_id="delete_knowledge_base_document",
    summary="Delete a document from the judge's knowledge base",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Knowledge base not initialized"},
        503: {"model": ErrorResponse, "description": "Vector store unavailable"},
    },
)
async def delete_knowledge_base_document(
    file_id: str,
    current_user: User = require_role(UserRole.judge),
) -> KnowledgeBaseDeleteResponse:
    store_id = current_user.knowledge_base_vector_store_id
    if not store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not initialized",
        )
    try:
        await kb_service.delete_kb_file(store_id, file_id)
    except Exception as exc:
        logger.exception("Failed to delete judge knowledge base file: %s", exc)
        raise _kb_unavailable() from exc
    return KnowledgeBaseDeleteResponse(id=file_id, deleted=True)


@router.post(
    "/search",
    response_model=KnowledgeBaseSearchResponse,
    status_code=status.HTTP_200_OK,
    operation_id="search_knowledge_base",
    summary="Search the judge's knowledge base",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Knowledge base not initialized"},
        503: {"model": ErrorResponse, "description": "Vector store unavailable"},
    },
)
async def search_knowledge_base(
    body: KnowledgeBaseSearchRequest,
    current_user: User = require_role(UserRole.judge),
) -> KnowledgeBaseSearchResponse:
    store_id = current_user.knowledge_base_vector_store_id
    if not store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not initialized",
        )
    try:
        hits = await kb_service.search_kb(
            vector_store_id=store_id,
            query=body.q,
            max_results=body.limit or 5,
        )
    except Exception as exc:
        logger.exception("Failed to search judge knowledge base: %s", exc)
        raise _kb_unavailable() from exc
    return KnowledgeBaseSearchResponse(
        items=[
            {
                "file_id": h["file_id"],
                "filename": h.get("filename"),
                "content": h.get("content", ""),
                "score": h["score"],
            }
            for h in hits
        ]
    )
