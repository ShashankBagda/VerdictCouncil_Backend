"""Domain management routes.

Admin routes: /api/v1/admin/domains (CRUD, document management)
Public route: /api/v1/domains (active domains for intake dropdown)

Every admin route declares response_model= explicitly (enforced by test).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, status
from sqlalchemy import select

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
) -> list[PublicDomainResponse]:
    result = await db.execute(
        select(Domain).where(Domain.is_active.is_(True)).order_by(Domain.name)
    )
    return [
        PublicDomainResponse(
            id=d.id,
            code=d.code,
            name=d.name,
            description=d.description,
            has_vector_store=bool(d.vector_store_id),
        )
        for d in result.scalars().all()
    ]


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

        live_count_result = await db.execute(select(Case).where(Case.domain_id == domain_id))
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


async def _ingest_domain_document(
    doc_id: UUID,
    domain_id: UUID,
    vector_store_id: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    actor_id: UUID,
) -> None:
    """Background pipeline: upload → parse → sanitize → index."""
    from openai import AsyncOpenAI
    from openai import NotFoundError as OpenAINotFoundError

    from src.services.database import async_session
    from src.tools.parse_document import parse_document

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    async with async_session() as db:
        try:
            doc = await db.get(DomainDocument, doc_id)
            if doc is None:
                logger.error("Background ingest: doc %s not found", doc_id)
                return

            try:
                oa_file = await client.files.create(
                    file=(filename, file_bytes, content_type),
                    purpose="assistants",
                )
                doc.openai_file_id = oa_file.id
                doc.status = DomainDocumentStatus.uploading
                await db.commit()
            except Exception as exc:
                doc.status = DomainDocumentStatus.failed
                doc.error_reason = f"OpenAI upload failed: {exc}"
                await db.commit()
                logger.error("Ingest upload failed for doc %s: %s", doc_id, exc)
                return

            try:
                parse_result = await parse_document(
                    file_id=doc.openai_file_id,
                    run_classifier=settings.classifier_sanitizer_enabled,
                )
                doc.status = DomainDocumentStatus.parsed
                await db.commit()
            except Exception as exc:
                doc.status = DomainDocumentStatus.failed
                doc.error_reason = f"Parse failed: {exc}"
                await db.commit()
                logger.error("Ingest parse failed for doc %s: %s", doc_id, exc)
                return

            pages = parse_result.get("pages", [])
            if not pages:
                doc.status = DomainDocumentStatus.failed
                doc.error_reason = "Parse returned no pages"
                await db.commit()
                return

            sanitized_text = "\n".join(
                f"--- Page {i + 1} ---\n{p.get('text', '')}" for i, p in enumerate(pages)
            )
            sanitized_filename = f"{filename.rsplit('.', 1)[0]}.sanitized.txt"

            try:
                san_file = await client.files.create(
                    file=(sanitized_filename, sanitized_text.encode(), "text/plain"),
                    purpose="assistants",
                )
                doc.sanitized_file_id = san_file.id
                doc.status = DomainDocumentStatus.indexing
                doc.sanitized = True
                await db.commit()
            except Exception as exc:
                doc.status = DomainDocumentStatus.failed
                doc.error_reason = f"Sanitized file upload failed: {exc}"
                await db.commit()
                logger.error("Ingest sanitized upload failed for doc %s: %s", doc_id, exc)
                return

            current_vs_id = vector_store_id
            try:
                vs_file = await client.vector_stores.files.create_and_poll(
                    vector_store_id=current_vs_id,
                    file_id=san_file.id,
                )
            except OpenAINotFoundError:
                # Vector store was deleted or belongs to another environment — re-provision.
                logger.warning(
                    "Vector store %s not found; re-provisioning for domain %s",
                    current_vs_id,
                    domain_id,
                )
                try:
                    from src.services.knowledge_base import ensure_domain_vector_store

                    current_vs_id, _ = await ensure_domain_vector_store(
                        db, str(domain_id), force_recreate=True
                    )
                    vs_file = await client.vector_stores.files.create_and_poll(
                        vector_store_id=current_vs_id,
                        file_id=san_file.id,
                    )
                except Exception as exc:
                    doc.status = DomainDocumentStatus.failed
                    doc.error_reason = f"Indexing failed after vector store re-provisioning: {exc}"
                    await db.commit()
                    logger.error("Ingest re-provision failed for doc %s: %s", doc_id, exc)
                    return
            except Exception as exc:
                doc.status = DomainDocumentStatus.failed
                doc.error_reason = f"Indexing failed: {exc}"
                await db.commit()
                logger.error("Ingest index failed for doc %s: %s", doc_id, exc)
                return

            if vs_file.status != "completed":
                doc.status = DomainDocumentStatus.failed
                doc.error_reason = f"Vector store file status: {vs_file.status}"
                await db.commit()
                return

            doc.status = DomainDocumentStatus.indexed
            san = parse_result.get("sanitization")
            db.add(
                AdminEvent(
                    actor_id=actor_id,
                    action="domain_document_uploaded",
                    payload={
                        "domain_id": str(domain_id),
                        "doc_id": str(doc_id),
                        "filename": filename,
                        "regex_hits": san.regex_hits if san else 0,
                        "classifier_hits": san.classifier_hits if san else 0,
                        "chunks_scanned": san.chunks_scanned if san else 0,
                    },
                )
            )
            await db.commit()
            logger.info("Ingest complete for doc %s (%s)", doc_id, filename)

        except Exception as exc:
            logger.error("Unexpected ingest error for doc %s: %s", doc_id, exc)
            await db.rollback()


@router.post(
    "/admin/{domain_id}/documents",
    response_model=DomainDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="upload_domain_document",
    summary="Upload a document to a domain KB",
    description="Sanitize-at-ingest pipeline. Feature-flagged behind domain_uploads_enabled.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Domain not found"},
        413: {"model": ErrorResponse, "description": "File too large"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        503: {
            "model": ErrorResponse,
            "description": "Uploads temporarily disabled or vector store unavailable",
        },  # noqa: E501
    },
)
async def upload_domain_document(
    background_tasks: BackgroundTasks,
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
        from src.services.knowledge_base import ensure_domain_vector_store

        try:
            await ensure_domain_vector_store(db, str(domain_id))
            await db.refresh(domain)
        except Exception as exc:
            logger.error("Failed to provision vector store for domain %s: %s", domain.code, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Domain vector store could not be provisioned",
            ) from exc

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

    filename = file.filename or "document"
    doc = DomainDocument(
        id=uuid4(),
        domain_id=domain_id,
        filename=filename,
        mime_type=content_type,
        size_bytes=len(content),
        status=DomainDocumentStatus.pending,
        idempotency_key=uuid4(),
        uploaded_by=current_user.id,
        sanitized=False,
        uploaded_at=datetime.now(UTC),
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(
        _ingest_domain_document,
        doc_id=doc.id,
        domain_id=domain_id,
        vector_store_id=domain.vector_store_id,
        file_bytes=content,
        filename=filename,
        content_type=content_type,
        actor_id=current_user.id,
    )

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
