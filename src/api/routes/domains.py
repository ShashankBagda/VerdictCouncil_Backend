"""Domain management routes.

Admin routes: /api/v1/admin/domains (CRUD, document management)
Public route: /api/v1/domains (active domains for intake dropdown)

Every admin route declares response_model= explicitly (enforced by test).
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import CurrentUser, DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.domains import (
    AdminDomainResponse,
    DomainCapabilitiesResponse,
    DomainCreateRequest,
    DomainDocumentResponse,
    DomainUpdateRequest,
    PublicDomainResponse,
)
from src.models.admin_event import AdminEvent
from src.models.domain import Domain, DomainDocument, DomainDocumentStatus
from src.models.user import User, UserRole
from src.shared.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


# ---------------------------------------------------------------------------
# Public endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=list[PublicDomainResponse],
    operation_id="list_active_domains",
    summary="List active domains for intake",
    description="Returns active domains only. vector_store_id and is_active are omitted.",
)
async def list_active_domains(
    db: DBSession,
    current_user: CurrentUser,
) -> list[Domain]:
    result = await db.execute(select(Domain).where(Domain.is_active.is_(True)).order_by(Domain.name))
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/capabilities",
    response_model=DomainCapabilitiesResponse,
    operation_id="get_domain_capabilities",
    summary="Get domain feature capabilities",
    description="Returns flags for features that may be disabled pending follow-up work.",
)
async def get_domain_capabilities(
    current_user: CurrentUser,
) -> dict:
    return {"uploads_enabled": settings.domain_uploads_enabled}


@router.get(
    "/admin",
    response_model=list[AdminDomainResponse],
    operation_id="list_domains_admin",
    summary="List all domains (admin)",
    responses={403: {"model": ErrorResponse, "description": "Insufficient permissions"}},
)
async def list_domains_admin(
    db: DBSession,
    current_user: User = require_role(UserRole.admin),
) -> list[Domain]:
    result = await db.execute(select(Domain).order_by(Domain.name))
    return list(result.scalars().all())


@router.post(
    "/admin",
    response_model=AdminDomainResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_domain",
    summary="Create a new domain",
    responses={
        400: {"model": ErrorResponse, "description": "Domain code already exists"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def create_domain(
    body: DomainCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.admin),
) -> Domain:
    existing = await db.execute(select(Domain).where(Domain.code == body.code))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Domain code '{body.code}' already exists",
        )

    from src.services.knowledge_base import ensure_domain_vector_store

    domain = Domain(
        id=uuid4(),
        code=body.code,
        name=body.name,
        description=body.description,
        created_by=current_user.id,
    )
    db.add(domain)
    await db.flush()

    # Provision the vector store inline (sets is_active=True on success)
    try:
        await ensure_domain_vector_store(db, str(domain.id))
    except Exception as exc:
        logger.error("Failed to provision vector store for domain %s: %s", body.code, exc)
        # Domain created but inactive — admin can retry via PATCH

    db.add(
        AdminEvent(
            actor_id=current_user.id,
            action="domain_created",
            payload={"domain_id": str(domain.id), "code": domain.code},
        )
    )
    await db.flush()
    await db.refresh(domain)
    return domain


@router.get(
    "/admin/{domain_id}",
    response_model=AdminDomainResponse,
    operation_id="get_domain_admin",
    summary="Get domain by ID (admin)",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
    },
)
async def get_domain_admin(
    domain_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.admin),
) -> Domain:
    domain = await db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    return domain


@router.patch(
    "/admin/{domain_id}",
    response_model=AdminDomainResponse,
    operation_id="update_domain",
    summary="Update domain metadata",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
    },
)
async def update_domain(
    domain_id: UUID,
    body: DomainUpdateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.admin),
) -> Domain:
    domain = await db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")

    if body.name is not None:
        domain.name = body.name
    if body.description is not None:
        domain.description = body.description
    if body.is_active is not None:
        domain.is_active = body.is_active

    db.add(
        AdminEvent(
            actor_id=current_user.id,
            action="domain_updated",
            payload={"domain_id": str(domain_id), "changes": body.model_dump(exclude_none=True)},
        )
    )
    await db.flush()
    await db.refresh(domain)
    return domain


@router.delete(
    "/admin/{domain_id}",
    response_model=AdminDomainResponse,
    operation_id="retire_domain",
    summary="Soft-delete (retire) a domain",
    description=(
        "Sets is_active=False and blocks new case intake. "
        "Pass ?hard=true to permanently delete (requires zero live cases)."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Live cases prevent hard delete"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
    },
)
async def retire_domain(
    domain_id: UUID,
    db: DBSession,
    hard: bool = False,
    current_user: User = require_role(UserRole.admin),
) -> Domain:
    domain = await db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")

    if hard:
        from src.models.case import Case

        live_count_result = await db.execute(
            select(Case).where(Case.domain_id == domain_id)
        )
        if live_count_result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot hard-delete domain with live cases",
            )
        # Delete the vector store if provisioned
        if domain.vector_store_id:
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=settings.openai_api_key)
                await client.vector_stores.delete(domain.vector_store_id)
            except Exception as exc:
                logger.warning("Failed to delete OpenAI store %s: %s", domain.vector_store_id, exc)

        db.add(
            AdminEvent(
                actor_id=current_user.id,
                action="domain_deleted",
                payload={"domain_id": str(domain_id), "code": domain.code},
            )
        )
        await db.delete(domain)
        await db.flush()
        # Return a detached snapshot so FastAPI can serialize it
        return domain

    domain.is_active = False
    db.add(
        AdminEvent(
            actor_id=current_user.id,
            action="domain_retired",
            payload={"domain_id": str(domain_id), "code": domain.code},
        )
    )
    await db.flush()
    await db.refresh(domain)
    return domain


# ---------------------------------------------------------------------------
# Document management
# ---------------------------------------------------------------------------


@router.get(
    "/admin/{domain_id}/documents",
    response_model=list[DomainDocumentResponse],
    operation_id="list_domain_documents",
    summary="List documents in a domain KB",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
    },
)
async def list_domain_documents(
    domain_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.admin),
) -> list[DomainDocument]:
    domain = await db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    result = await db.execute(
        select(DomainDocument)
        .where(DomainDocument.domain_id == domain_id)
        .order_by(DomainDocument.uploaded_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/admin/{domain_id}/documents",
    response_model=DomainDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="upload_domain_document",
    summary="Upload a document to a domain KB",
    description="Sanitize-at-ingest pipeline. Upload route is feature-flagged behind domain_uploads_enabled.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": ErrorResponse, "description": "Document could not be parsed/sanitized"},
        503: {"model": ErrorResponse, "description": "Uploads temporarily disabled"},
    },
)
async def upload_domain_document(
    domain_id: UUID,
    db: DBSession,
    file: UploadFile,
    current_user: User = require_role(UserRole.admin),
) -> DomainDocument:
    if not settings.domain_uploads_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Domain document uploads are disabled pending sanitizer hardening",
        )

    domain = await db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
    if not domain.vector_store_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Domain vector store not provisioned",
        )

    content_type = file.content_type or ""
    if content_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {content_type}",
        )

    content = await file.read()
    if len(content) > settings.domain_kb_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File exceeds maximum size of "
                f"{settings.domain_kb_max_upload_bytes // 1024 // 1024} MiB"
            ),
        )

    from openai import AsyncOpenAI

    from src.tools.parse_document import parse_document

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    idempotency_key = uuid4()

    # Step 1: DB-first insert with status='pending' before any OpenAI calls
    doc = DomainDocument(
        id=uuid4(),
        domain_id=domain_id,
        filename=file.filename or "document",
        mime_type=content_type,
        size_bytes=len(content),
        status=DomainDocumentStatus.pending,
        idempotency_key=idempotency_key,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    await db.flush()

    # Step 2: Upload original to OpenAI Files
    try:
        oa_file = await client.files.create(
            file=(file.filename or "document", content, content_type),
            purpose="assistants",
        )
        doc.openai_file_id = oa_file.id
        doc.status = DomainDocumentStatus.uploading
        await db.flush()
    except Exception as exc:
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = f"OpenAI upload failed: {exc}"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to upload to OpenAI: {exc}",
        ) from exc

    # Step 3: Parse + sanitize
    try:
        parse_result = await parse_document(file_id=doc.openai_file_id)
        doc.status = DomainDocumentStatus.parsed
        await db.flush()
    except Exception as exc:
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = f"Parse failed: {exc}"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Document could not be parsed: {exc}",
        ) from exc

    # Build sanitized text artifact with page markers
    pages = parse_result.get("pages", [])
    if not pages:
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = "Parse returned no pages"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Document yielded no extractable text",
        )

    sanitized_text = "\n".join(
        f"--- Page {i + 1} ---\n{p.get('text', '')}" for i, p in enumerate(pages)
    )
    sanitized_filename = f"{(file.filename or 'document').rsplit('.', 1)[0]}.sanitized.txt"

    # Step 4: Upload sanitized artifact
    try:
        san_file = await client.files.create(
            file=(sanitized_filename, sanitized_text.encode(), "text/plain"),
            purpose="assistants",
        )
        doc.sanitized_file_id = san_file.id
        doc.status = DomainDocumentStatus.indexing
        doc.sanitized = True
        await db.flush()

        # Add ONLY the sanitized file to the domain vector store
        vs_file = await client.vector_stores.files.create_and_poll(
            vector_store_id=domain.vector_store_id,
            file_id=san_file.id,
        )
    except Exception as exc:
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = f"Indexing failed: {exc}"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to index document: {exc}",
        ) from exc

    if vs_file.status != "completed":
        doc.status = DomainDocumentStatus.failed
        doc.error_reason = f"Vector store file status: {vs_file.status}"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Document indexing did not complete",
        )

    doc.status = DomainDocumentStatus.indexed
    db.add(
        AdminEvent(
            actor_id=current_user.id,
            action="domain_document_uploaded",
            payload={"domain_id": str(domain_id), "doc_id": str(doc.id), "filename": doc.filename},
        )
    )
    await db.flush()
    await db.refresh(doc)
    return doc


@router.delete(
    "/admin/{domain_id}/documents/{doc_id}",
    response_model=DomainDocumentResponse,
    operation_id="delete_domain_document",
    summary="Delete a document from domain KB",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Document not found"},
    },
)
async def delete_domain_document(
    domain_id: UUID,
    doc_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.admin),
) -> DomainDocument:
    result = await db.execute(
        select(DomainDocument).where(
            DomainDocument.id == doc_id, DomainDocument.domain_id == domain_id
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    domain = await db.get(Domain, domain_id)

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    # Best-effort removal from OpenAI — always remove the DB row
    if doc.sanitized_file_id and domain and domain.vector_store_id:
        try:
            await client.vector_stores.files.delete(
                vector_store_id=domain.vector_store_id, file_id=doc.sanitized_file_id
            )
        except Exception as exc:
            logger.warning("Could not remove vector store file %s: %s", doc.sanitized_file_id, exc)
        try:
            await client.files.delete(doc.sanitized_file_id)
        except Exception as exc:
            logger.warning("Could not delete sanitized file %s: %s", doc.sanitized_file_id, exc)

    if doc.openai_file_id:
        try:
            await client.files.delete(doc.openai_file_id)
        except Exception as exc:
            logger.warning("Could not delete original file %s: %s", doc.openai_file_id, exc)

    db.add(
        AdminEvent(
            actor_id=current_user.id,
            action="domain_document_deleted",
            payload={"domain_id": str(domain_id), "doc_id": str(doc_id)},
        )
    )
    await db.delete(doc)
    await db.flush()
    return doc
