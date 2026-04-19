"""Case data endpoints: document upload, pipeline status, analysis sub-resources, SSE stream.

These endpoints serve the frontend's per-case data needs that were previously
returning 404. They query the same DB models that GET /cases/{case_id} returns
in aggregate, but expose them individually so the frontend can fetch them in
parallel via Promise.allSettled.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.api.deps import CurrentUser, DBSession
from src.api.schemas.cases import (
    ArgumentResponse,
    DeliberationResponse,
    DocumentResponse,
    EvidenceResponse,
    FactResponse,
    LegalRuleResponse,
    PrecedentResponse,
    VerdictResponse,
    WitnessResponse,
)
from src.api.schemas.common import ErrorResponse, MessageResponse
from src.models.audit import AuditLog
from src.models.case import (
    Argument,
    Case,
    CaseStatus,
    Deliberation,
    Document,
    Evidence,
    Fact,
    LegalRule,
    Precedent,
    Verdict,
    Witness,
)
from src.models.user import UserRole

logger = logging.getLogger(__name__)

router = APIRouter()

# 9-agent pipeline topology used for status derivation
PIPELINE_AGENTS = [
    "case-processing",
    "fact-reconstruction",
    "evidence-analysis",
    "witness-analysis",
    "legal-knowledge",
    "argument-construction",
    "complexity-routing",
    "deliberation",
    "governance-verdict",
]

AGENT_LABELS = {
    "case-processing": "Case Processing",
    "fact-reconstruction": "Fact Reconstruction",
    "evidence-analysis": "Evidence Analysis",
    "witness-analysis": "Witness Analysis",
    "legal-knowledge": "Legal Knowledge",
    "argument-construction": "Argument Construction",
    "complexity-routing": "Complexity Routing",
    "deliberation": "Deliberation",
    "governance-verdict": "Governance & Verdict",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_case_or_404(case_id: UUID, db, current_user) -> Case:
    """Load a case with access check. Raises 404/403."""
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return case


def _derive_agent_status(case: Case, audit_logs: list[AuditLog]) -> list[dict[str, Any]]:
    """Derive per-agent status from the case status and audit log entries."""
    agent_actions: dict[str, list[AuditLog]] = {a: [] for a in PIPELINE_AGENTS}
    for log in audit_logs:
        name = log.agent_name
        if name in agent_actions:
            agent_actions[name].append(log)

    agents = []
    for agent_id in PIPELINE_AGENTS:
        logs = agent_actions[agent_id]
        if not logs:
            agent_status = "pending"
            start_time = None
            end_time = None
        else:
            last = logs[-1]
            action_lower = (last.action or "").lower()
            if "fail" in action_lower or "error" in action_lower:
                agent_status = "completed" if "recover" in action_lower else "failed"
            elif "complet" in action_lower or "done" in action_lower or "finish" in action_lower:
                agent_status = "completed"
            elif "start" in action_lower or "run" in action_lower or "process" in action_lower:
                agent_status = "running"
            else:
                agent_status = "completed"
            start_time = logs[0].created_at.isoformat() if logs[0].created_at else None
            end_time = last.created_at.isoformat() if last.created_at else None

        agents.append({
            "agent_id": agent_id,
            "name": AGENT_LABELS.get(agent_id, agent_id),
            "status": agent_status,
            "start_time": start_time,
            "end_time": end_time,
            "elapsed_seconds": None,
            "error_message": None,
            "output_summary": None,
        })

    # If case is in a terminal state, mark all pending agents accordingly
    if case.status in (CaseStatus.ready_for_review, CaseStatus.decided, CaseStatus.closed):
        for a in agents:
            if a["status"] == "pending":
                a["status"] = "completed"
    elif case.status == CaseStatus.failed:
        for a in agents:
            if a["status"] in ("pending", "running"):
                a["status"] = "failed"
    elif case.status == CaseStatus.processing:
        # Mark the first pending agent as running
        for a in agents:
            if a["status"] == "pending":
                a["status"] = "running"
                break

    return agents


def _compute_progress(agents: list[dict]) -> int:
    if not agents:
        return 0
    done = sum(1 for a in agents if a["status"] in ("completed", "failed"))
    return round(done / len(agents) * 100)


def _derive_overall_status(case: Case, agents: list[dict]) -> str:
    if case.status == CaseStatus.failed:
        return "failed"
    if case.status in (CaseStatus.ready_for_review, CaseStatus.decided, CaseStatus.closed):
        return "completed"
    if any(a["status"] == "failed" for a in agents):
        return "failed"
    if all(a["status"] == "completed" for a in agents):
        return "completed"
    if any(a["status"] == "running" for a in agents):
        return "processing"
    return "pending"


# ---------------------------------------------------------------------------
# Document upload
# ---------------------------------------------------------------------------


@router.post(
    "/{case_id}/documents",
    response_model=list[DocumentResponse],
    status_code=status.HTTP_201_CREATED,
    operation_id="upload_documents",
    summary="Upload documents to a case",
    description="Upload one or more files to a case. Files are stored as metadata "
    "records in the database. Requires authenticated user.",
    responses={
        404: {"model": ErrorResponse, "description": "Case not found"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
    },
)
async def upload_documents(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    files: list[UploadFile] = File(..., description="Files to upload"),
) -> list[Document]:
    case = await _get_case_or_404(case_id, db, current_user)

    created = []
    for upload in files:
        doc = Document(
            case_id=case.id,
            filename=upload.filename or "unnamed",
            file_type=upload.content_type,
            uploaded_by=current_user.id,
        )
        db.add(doc)
        created.append(doc)

    # Log the upload
    audit = AuditLog(
        case_id=case.id,
        agent_name="system",
        action="document_upload",
        input_payload={"filenames": [f.filename for f in files]},
    )
    db.add(audit)
    await db.flush()
    for doc in created:
        await db.refresh(doc)

    return created


# ---------------------------------------------------------------------------
# Pipeline status
# ---------------------------------------------------------------------------


@router.get(
    "/{case_id}/status",
    operation_id="get_pipeline_status",
    summary="Get pipeline processing status",
    description="Returns the 9-agent pipeline status derived from the case state "
    "and audit log. Used by the frontend polling hook.",
    responses={
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_pipeline_status(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> dict:
    case = await _get_case_or_404(case_id, db, current_user)

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.case_id == case_id)
        .order_by(AuditLog.created_at.asc())
    )
    logs = list(result.scalars().all())

    agents = _derive_agent_status(case, logs)
    progress = _compute_progress(agents)
    overall = _derive_overall_status(case, agents)

    return {
        "agents": agents,
        "overall_progress_percent": progress,
        "overall_status": overall,
    }


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@router.get(
    "/{case_id}/status/stream",
    operation_id="stream_pipeline_status",
    summary="Stream pipeline status via SSE",
    description="Server-Sent Events stream that pushes pipeline status updates "
    "every 3 seconds. The stream closes when the pipeline reaches a terminal state.",
    responses={
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def stream_pipeline_status(
    case_id: UUID,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    # Validate case exists and user has access
    await _get_case_or_404(case_id, db, current_user)

    async def event_generator():
        from src.services.database import async_session as session_factory

        while True:
            if await request.is_disconnected():
                break

            async with session_factory() as fresh_db:
                case_result = await fresh_db.execute(
                    select(Case).where(Case.id == case_id)
                )
                case = case_result.scalar_one_or_none()
                if not case:
                    break

                log_result = await fresh_db.execute(
                    select(AuditLog)
                    .where(AuditLog.case_id == case_id)
                    .order_by(AuditLog.created_at.asc())
                )
                logs = list(log_result.scalars().all())

            agents = _derive_agent_status(case, logs)
            progress = _compute_progress(agents)
            overall = _derive_overall_status(case, agents)

            payload = json.dumps({
                "agents": agents,
                "overall_progress_percent": progress,
                "overall_status": overall,
            })
            yield f"data: {payload}\n\n"

            # Stop streaming if terminal
            if overall in ("completed", "failed"):
                break

            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Individual analysis sub-resources
# ---------------------------------------------------------------------------


@router.get(
    "/{case_id}/evidence",
    response_model=list[EvidenceResponse],
    operation_id="get_case_evidence",
    summary="Get evidence items for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_evidence(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[Evidence]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(Evidence).where(Evidence.case_id == case_id))
    return list(result.scalars().all())


@router.get(
    "/{case_id}/timeline",
    response_model=list[FactResponse],
    operation_id="get_case_timeline",
    summary="Get reconstructed facts / timeline for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_timeline(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[Fact]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(
        select(Fact).where(Fact.case_id == case_id).order_by(Fact.event_date.asc().nullslast())
    )
    return list(result.scalars().all())


@router.get(
    "/{case_id}/witnesses",
    response_model=list[WitnessResponse],
    operation_id="get_case_witnesses",
    summary="Get witnesses for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_witnesses(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[Witness]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(Witness).where(Witness.case_id == case_id))
    return list(result.scalars().all())


@router.get(
    "/{case_id}/statutes",
    response_model=list[LegalRuleResponse],
    operation_id="get_case_statutes",
    summary="Get applicable statutes / legal rules for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_statutes(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[LegalRule]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(LegalRule).where(LegalRule.case_id == case_id))
    return list(result.scalars().all())


@router.get(
    "/{case_id}/precedents",
    response_model=list[PrecedentResponse],
    operation_id="get_case_precedents",
    summary="Get matched precedents for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_precedents(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[Precedent]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(Precedent).where(Precedent.case_id == case_id))
    return list(result.scalars().all())


@router.get(
    "/{case_id}/arguments",
    response_model=list[ArgumentResponse],
    operation_id="get_case_arguments",
    summary="Get constructed arguments for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_arguments(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[Argument]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(Argument).where(Argument.case_id == case_id))
    return list(result.scalars().all())


@router.get(
    "/{case_id}/deliberation",
    response_model=list[DeliberationResponse],
    operation_id="get_case_deliberation",
    summary="Get AI deliberation for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_deliberation(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[Deliberation]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(Deliberation).where(Deliberation.case_id == case_id))
    return list(result.scalars().all())


@router.get(
    "/{case_id}/verdict",
    response_model=list[VerdictResponse],
    operation_id="get_case_verdict",
    summary="Get generated verdicts for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_verdict(
    case_id: UUID, db: DBSession, current_user: CurrentUser,
) -> list[Verdict]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(Verdict).where(Verdict.case_id == case_id))
    return list(result.scalars().all())
