"""Hearing notes CRUD endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.hearing_notes import (
    HearingNoteCreateRequest,
    HearingNoteListResponse,
    HearingNoteResponse,
    HearingNoteUpdateRequest,
)
from src.models.audit import AuditLog
from src.models.case import Case, HearingNote
from src.models.user import User, UserRole

router = APIRouter()


@router.post(
    "/{case_id}/hearing-notes",
    response_model=HearingNoteResponse,
    operation_id="create_hearing_note",
    summary="Create hearing note",
)
async def create_hearing_note(
    case_id: UUID,
    body: HearingNoteCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> HearingNote:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    note = HearingNote(
        case_id=case_id,
        judge_id=current_user.id,
        content=body.content,
        section_reference=body.section_reference,
        note_type=body.note_type,
    )
    db.add(note)
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="hearing_note_create",
            input_payload={"section_reference": body.section_reference, "note_type": body.note_type},
            output_payload={"note_id": str(note.id)},
        )
    )
    await db.flush()
    await db.refresh(note)
    return note


@router.get(
    "/{case_id}/hearing-notes",
    response_model=HearingNoteListResponse,
    operation_id="list_hearing_notes",
    summary="List hearing notes",
)
async def list_hearing_notes(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> HearingNoteListResponse:
    result = await db.execute(select(HearingNote).where(HearingNote.case_id == case_id))
    items = list(result.scalars().all())
    return HearingNoteListResponse(items=items, total=len(items))


@router.patch(
    "/{case_id}/hearing-notes/{note_id}",
    response_model=HearingNoteResponse,
    operation_id="update_hearing_note",
    summary="Update hearing note",
    responses={
        403: {"model": ErrorResponse, "description": "Cannot edit locked note"},
        404: {"model": ErrorResponse, "description": "Note not found"},
    },
)
async def update_hearing_note(
    case_id: UUID,
    note_id: UUID,
    body: HearingNoteUpdateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> HearingNote:
    result = await db.execute(
        select(HearingNote).where(HearingNote.id == note_id, HearingNote.case_id == case_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if note.is_locked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Note is locked")
    if note.judge_id != current_user.id and current_user.role != UserRole.senior_judge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot edit another judge's note",
        )

    if body.content is not None:
        note.content = body.content
    if body.section_reference is not None:
        note.section_reference = body.section_reference
    if body.note_type is not None:
        note.note_type = body.note_type

    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="hearing_note_update",
            input_payload={"note_id": str(note_id)},
        )
    )
    await db.flush()
    await db.refresh(note)
    return note


@router.post(
    "/{case_id}/hearing-notes/{note_id}/lock",
    response_model=HearingNoteResponse,
    operation_id="lock_hearing_note",
    summary="Lock hearing note",
)
async def lock_hearing_note(
    case_id: UUID,
    note_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> HearingNote:
    result = await db.execute(
        select(HearingNote).where(HearingNote.id == note_id, HearingNote.case_id == case_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")

    note.is_locked = True
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="hearing_note_lock",
            input_payload={"note_id": str(note_id)},
        )
    )
    await db.flush()
    await db.refresh(note)
    return note


@router.delete(
    "/{case_id}/hearing-notes/{note_id}",
    operation_id="delete_hearing_note",
    summary="Delete hearing note",
)
async def delete_hearing_note(
    case_id: UUID,
    note_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> dict:
    result = await db.execute(
        select(HearingNote).where(HearingNote.id == note_id, HearingNote.case_id == case_id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if note.is_locked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Note is locked")
    if note.judge_id != current_user.id and current_user.role != UserRole.senior_judge:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete another judge's note",
        )

    await db.delete(note)
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="hearing_note_delete",
            input_payload={"note_id": str(note_id)},
        )
    )
    await db.flush()
    return {"message": "hearing note deleted"}
