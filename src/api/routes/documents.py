"""Document excerpt endpoint for citation drill-down (US-008)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import DBSession, require_role
from src.models.case import Document
from src.models.user import User, UserRole

router = APIRouter()


@router.get(
    "/{document_id}/excerpt",
    operation_id="get_document_excerpt",
    summary="Get a specific page excerpt from an uploaded document",
)
async def get_document_excerpt(
    document_id: UUID,
    page: int = Query(..., ge=1, description="1-indexed page number"),
    db: DBSession = None,
    current_user: User = require_role(UserRole.judge),
) -> dict:
    result = await db.execute(
        select(Document).where(Document.id == document_id).options(selectinload(Document.case))
    )
    doc = result.scalar_one_or_none()
    if doc is None or doc.case is None or doc.case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    pages: list = doc.pages or []
    if page > len(pages):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Page not found")

    return {
        "document_id": str(document_id),
        "filename": doc.filename,
        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        "page_number": page,
        "total_pages": len(pages),
        "text": pages[page - 1],
    }
