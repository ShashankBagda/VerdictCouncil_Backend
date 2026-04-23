"""Case data endpoints: document upload, pipeline status, analysis sub-resources, SSE stream.

These endpoints serve the frontend's per-case data needs that were previously
returning 404. They query the same DB models that GET /cases/{case_id} returns
in aggregate, but expose them individually so the frontend can fetch them in
parallel via Promise.allSettled.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select

from src.api.deps import CurrentUser, DBSession
from src.api.schemas.cases import (
    ArgumentResponse,
    DocumentResponse,
    EvidenceResponse,
    FactResponse,
    HearingAnalysisResponse,
    LegalRuleResponse,
    PrecedentResponse,
    WitnessResponse,
)
from src.api.schemas.common import ErrorResponse
from src.api.schemas.workflows import SupplementaryUploadResponse
from src.models.audit import AuditLog
from src.models.case import (
    Argument,
    Case,
    CaseStatus,
    Document,
    Evidence,
    Fact,
    HearingAnalysis,
    LegalRule,
    Precedent,
    Witness,
)
logger = logging.getLogger(__name__)

router = APIRouter()

# 9-agent pipeline topology used for status derivation
PIPELINE_AGENTS = [
    "case-processing",
    "complexity-routing",
    "evidence-analysis",
    "fact-reconstruction",
    "witness-analysis",
    "legal-knowledge",
    "argument-construction",
    "hearing-analysis",
    "hearing-governance",
]

AGENT_LABELS = {
    "case-processing": "Case Processing",
    "complexity-routing": "Complexity Routing",
    "evidence-analysis": "Evidence Analysis",
    "fact-reconstruction": "Fact Reconstruction",
    "witness-analysis": "Witness Analysis",
    "legal-knowledge": "Legal Knowledge",
    "argument-construction": "Argument Construction",
    "hearing-analysis": "Hearing Analysis",
    "hearing-governance": "Hearing Governance",
}

SUPPLEMENTARY_RETRIGGERED_STAGES = PIPELINE_AGENTS[2:]
SUPPLEMENTARY_PRESERVED_STAGES = PIPELINE_AGENTS[:2]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_case_or_404(case_id: UUID, db, current_user) -> Case:
    """Load a case, raising 404 if not found."""
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
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
            elapsed_seconds = None
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
            if logs[0].created_at and last.created_at:
                elapsed_seconds = max(
                    int((last.created_at - logs[0].created_at).total_seconds()),
                    0,
                )
            else:
                elapsed_seconds = None

        agents.append(
            {
                "agent_id": agent_id,
                "name": AGENT_LABELS.get(agent_id, agent_id),
                "status": agent_status,
                "start_time": start_time,
                "end_time": end_time,
                "elapsed_seconds": elapsed_seconds,
                "error_message": None,
                "output_summary": None,
            }
        )

    # If case is in a terminal state, mark all pending agents accordingly
    if case.status in (CaseStatus.ready_for_review, CaseStatus.closed):
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
    if case.status in (CaseStatus.ready_for_review, CaseStatus.closed):
        return "completed"
    if any(a["status"] == "failed" for a in agents):
        return "failed"
    if all(a["status"] == "completed" for a in agents):
        return "completed"
    if any(a["status"] == "running" for a in agents):
        return "processing"
    return "pending"


def _derive_current_agent(agents: list[dict[str, Any]]) -> str | None:
    for agent in agents:
        if agent["status"] == "running":
            return agent["agent_id"]
    return None


def _derive_overall_elapsed_seconds(agents: list[dict[str, Any]]) -> int | None:
    completed = [
        agent["elapsed_seconds"] for agent in agents if agent.get("elapsed_seconds") is not None
    ]
    if not completed:
        return None
    return int(sum(completed))


def _latest_reprocessing_summary(audit_logs: list[AuditLog]) -> dict[str, Any] | None:
    for log in reversed(audit_logs):
        if log.action != "supplementary_document_upload":
            continue
        payload = log.output_payload or {}
        return {
            "retriggered_stages": payload.get("retriggered_stages") or [],
            "preserved_stages": payload.get("preserved_stages") or [],
            "reason": (log.input_payload or {}).get("reason"),
            "requested_at": log.created_at.isoformat() if log.created_at else None,
        }
    return None


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

    import openai

    from src.shared.config import settings

    oa_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    created = []
    for upload in files:
        content = await upload.read()
        openai_file_id: str | None = None
        try:
            oa_file = await oa_client.files.create(
                file=(
                    upload.filename or "document.bin",
                    content,
                    upload.content_type or "application/octet-stream",
                ),
                purpose="assistants",
            )
            openai_file_id = oa_file.id
        except Exception:
            logger.warning("OpenAI Files API upload failed for %s", upload.filename)

        doc = Document(
            case_id=case.id,
            filename=upload.filename or "unnamed",
            file_type=upload.content_type,
            uploaded_by=current_user.id,
            openai_file_id=openai_file_id,
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


@router.post(
    "/{case_id}/supplementary-documents",
    response_model=SupplementaryUploadResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="upload_supplementary_documents",
    summary="Upload supplementary documents and trigger selective re-processing",
    responses={
        400: {"model": ErrorResponse, "description": "Closed cases cannot accept documents"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def upload_supplementary_documents(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    files: list[UploadFile] = File(..., description="Supplementary files to upload"),
    reason: str | None = Form(default=None),
) -> SupplementaryUploadResponse:
    case = await _get_case_or_404(case_id, db, current_user)
    if case.status == CaseStatus.closed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Closed cases cannot accept supplementary documents",
        )

    import openai

    from src.shared.config import settings

    oa_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    created: list[Document] = []
    for upload in files:
        content = await upload.read()
        openai_file_id: str | None = None
        try:
            oa_file = await oa_client.files.create(
                file=(
                    upload.filename or "document.bin",
                    content,
                    upload.content_type or "application/octet-stream",
                ),
                purpose="assistants",
            )
            openai_file_id = oa_file.id
        except Exception:
            logger.warning("OpenAI Files API upload failed for %s", upload.filename)

        doc = Document(
            case_id=case.id,
            filename=upload.filename or "unnamed",
            file_type=upload.content_type,
            uploaded_by=current_user.id,
            openai_file_id=openai_file_id,
        )
        db.add(doc)
        created.append(doc)

    case.status = CaseStatus.processing

    db.add(
        AuditLog(
            case_id=case.id,
            agent_name="system",
            action="supplementary_document_upload",
            input_payload={
                "filenames": [f.filename for f in files],
                "reason": reason,
                "uploaded_by": str(current_user.id),
            },
            output_payload={
                "retriggered_stages": SUPPLEMENTARY_RETRIGGERED_STAGES,
                "preserved_stages": SUPPLEMENTARY_PRESERVED_STAGES,
            },
        )
    )

    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    # Supplementary upload resets to gate 1 — judge reviews before re-analysis continues.
    case.gate_state = {"current_gate": 1, "awaiting_review": False, "rerun_agent": None}
    await enqueue_outbox_job(
        db,
        case_id=case_id,
        job_type=PipelineJobType.gate_run,
        payload={"gate_name": "gate1"},
    )

    await db.flush()
    for doc in created:
        await db.refresh(doc)
    await db.commit()

    return SupplementaryUploadResponse(
        case_id=case.id,
        documents=created,
        retriggered_stages=SUPPLEMENTARY_RETRIGGERED_STAGES,
        preserved_stages=SUPPLEMENTARY_PRESERVED_STAGES,
        status=case.status,
        message="Supplementary documents stored and selective re-processing requested.",
    )


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
        select(AuditLog).where(AuditLog.case_id == case_id).order_by(AuditLog.created_at.asc())
    )
    logs = list(result.scalars().all())

    agents = _derive_agent_status(case, logs)
    progress = _compute_progress(agents)
    overall = _derive_overall_status(case, agents)

    return {
        "agents": agents,
        "overall_progress_percent": progress,
        "overall_status": overall,
        "current_agent": _derive_current_agent(agents),
        "overall_elapsed_seconds": _derive_overall_elapsed_seconds(agents),
        "reprocessing_summary": _latest_reprocessing_summary(logs),
    }


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
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
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
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
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
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
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
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
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
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
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
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> list[Argument]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(Argument).where(Argument.case_id == case_id))
    return list(result.scalars().all())


@router.get(
    "/{case_id}/hearing-analysis",
    response_model=list[HearingAnalysisResponse],
    operation_id="get_case_hearing_analysis",
    summary="Get AI hearing analysis for a case",
    responses={404: {"model": ErrorResponse}},
)
async def get_case_hearing_analysis(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> list[HearingAnalysis]:
    await _get_case_or_404(case_id, db, current_user)
    result = await db.execute(select(HearingAnalysis).where(HearingAnalysis.case_id == case_id))
    return list(result.scalars().all())
