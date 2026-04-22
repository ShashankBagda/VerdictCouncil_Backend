import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import selectinload

from src.api.deps import CurrentUser, DBSession, require_role
from src.api.schemas.cases import (
    KNOWN_TRAFFIC_OFFENCE_CODES,
    CaseCreateRequest,
    CaseDetailResponse,
    CaseListResponse,
    CaseResponse,
)
from src.api.schemas.common import ErrorResponse, MessageResponse, ValidationErrorResponse
from src.api.schemas.workflows import RejectionReviewRequest, RejectionReviewResponse
from src.models.audit import AuditLog
from src.models.case import (
    Case,
    CaseComplexity,
    CaseDomain,
    CaseStatus,
    Fact,
    Party,
    Verdict,
)
from src.models.user import User, UserRole
from src.services.case_report_data import build_case_report_data
from src.services.hearing_pack import assemble_pack
from src.services.pdf_export import render_case_report_pdf
from src.services.pipeline_events import subscribe as subscribe_pipeline_events

logger = logging.getLogger(__name__)

router = APIRouter()

STARTABLE_STATUSES = (CaseStatus.pending, CaseStatus.ready_for_review, CaseStatus.failed_retryable)

# SSE stream tuning — exposed at module scope so tests can override.
# HEARTBEAT cadence must be short enough to defeat reverse-proxy idle timers
# (typical: 30-60s). WATCHDOG caps total stream time so a runaway pipeline
# can't hang a client forever; we emit a synthetic terminal event and close.
SSE_HEARTBEAT_SECONDS = 15.0
SSE_WATCHDOG_SECONDS = 600.0

PIPELINE_AGENT_ORDER = [
    "case-processing",
    "complexity-routing",
    "evidence-analysis",
    "fact-reconstruction",
    "witness-analysis",
    "legal-knowledge",
    "argument-construction",
    "deliberation",
    "governance-verdict",
]


def _status_group(status_value: CaseStatus) -> str:
    if status_value in {CaseStatus.pending, CaseStatus.processing, CaseStatus.failed_retryable}:
        return "processing"
    if status_value in {CaseStatus.ready_for_review, CaseStatus.decided}:
        return "completed"
    if status_value == CaseStatus.escalated:
        return "escalated"
    if status_value == CaseStatus.rejected:
        return "rejected"
    if status_value == CaseStatus.closed:
        return "closed"
    if status_value == CaseStatus.failed:
        return "failed"
    return status_value.value


def _map_status_filter(status_filter: str) -> list[CaseStatus]:
    raw = status_filter.strip().lower()
    if raw == "completed":
        return [CaseStatus.ready_for_review, CaseStatus.decided]
    if raw == "processing":
        return [CaseStatus.pending, CaseStatus.processing, CaseStatus.failed_retryable]
    try:
        return [CaseStatus(raw)]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown case status filter: {status_filter}",
        ) from exc


def _serialize_parties(parties: list[Party]) -> list[dict[str, Any]]:
    return [
        {
            "id": party.id,
            "name": party.name,
            "role": party.role,
            "contact_info": party.contact_info,
        }
        for party in parties
    ]


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _display_text(value: Any, fallback: str) -> str:
    return _optional_text(value) or fallback


def _party_role_lookup(parties: list[Party]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for party in parties:
        lookup[party.role.value] = party.name
    return lookup


def _extract_decision_history(audit_logs: list[AuditLog]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for log in sorted(
        audit_logs, key=lambda item: item.created_at.timestamp() if item.created_at else 0.0
    ):
        if log.agent_name != "judge":
            continue
        if not (log.action.startswith("decision_") or log.action == "decision_amendment_apply"):
            continue
        payload = log.input_payload or {}
        decision_type = log.action.removeprefix("decision_")
        if log.action == "decision_amendment_apply":
            decision_type = payload.get("amendment_type") or "amendment"
        history.append(
            {
                "decision_type": decision_type,
                "reason": payload.get("notes"),
                "final_order": payload.get("final_order") or payload.get("proposed_final_order"),
                "recorded_at": log.created_at,
                "recorded_by": payload.get("judge_id") or payload.get("requested_by"),
            }
        )
    return history


def _extract_latest_verdict(case: Case) -> Verdict | None:
    verdicts = list(case.verdicts or [])
    if not verdicts:
        return None
    return verdicts[-1]


def _extract_escalation_reason(case: Case) -> str | None:
    for log in sorted(
        case.audit_logs or [],
        key=lambda item: item.created_at.timestamp() if item.created_at else 0.0,
        reverse=True,
    ):
        if "escalat" not in log.action.lower():
            continue
        for payload in (log.output_payload or {}, log.input_payload or {}):
            for key in ("reason", "escalation_reason", "summary", "detail", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    if case.route and case.route.value == "escalate_human":
        complexity = case.complexity.value if case.complexity else "unspecified"
        return f"Route escalated for human review (complexity: {complexity})."
    return None


def _build_jurisdiction_summary(case: Case) -> dict[str, Any]:
    reasons: list[str] = []
    warning = False
    failure = False
    earliest_fact_date = min(
        [fact.event_date for fact in (case.facts or []) if getattr(fact, "event_date", None)],
        default=None,
    )

    if case.domain == CaseDomain.small_claims and case.claim_amount is not None:
        limit = 30000 if case.consent_to_higher_claim_limit else 20000
        if case.claim_amount > limit:
            failure = True
            reasons.append(
                f"Claim amount ${case.claim_amount:,.0f} exceeds the ${limit:,.0f} SCT limit."
            )
        elif case.claim_amount == limit:
            warning = True
            reasons.append(
                "Claim amount "
                f"${case.claim_amount:,.0f} is exactly at the ${limit:,.0f} "
                "SCT threshold and needs judge review."
            )
        else:
            reasons.append(
                f"Claim amount ${case.claim_amount:,.0f} is within the ${limit:,.0f} SCT limit."
            )
        if earliest_fact_date and case.filed_date:
            limitation_days = (case.filed_date - earliest_fact_date).days
            if limitation_days > 730:
                failure = True
                reasons.append(
                    "Filed "
                    f"{limitation_days} days after the earliest identified "
                    f"cause-of-action date {earliest_fact_date.isoformat()}, "
                    "beyond the 2-year SCT limitation period."
                )
            elif limitation_days == 730:
                warning = True
                reasons.append(
                    "Filed exactly 730 days after the earliest identified "
                    f"cause-of-action date {earliest_fact_date.isoformat()}, "
                    "so the limitation edge needs judge review."
                )
            else:
                reasons.append(
                    "Filed "
                    f"{limitation_days} days after the earliest identified "
                    f"cause-of-action date {earliest_fact_date.isoformat()}, "
                    "within the 2-year SCT limitation period."
                )

    if case.domain == CaseDomain.traffic_violation:
        if case.offence_code:
            if case.offence_code not in KNOWN_TRAFFIC_OFFENCE_CODES:
                failure = True
                reasons.append(f"Offence code {case.offence_code} is not recognised.")
            else:
                reasons.append(f"Offence code {case.offence_code} is recognised.")
        if earliest_fact_date and case.filed_date:
            limitation_days = (case.filed_date - earliest_fact_date).days
            if limitation_days > 365:
                failure = True
                reasons.append(
                    "Offence date "
                    f"{earliest_fact_date.isoformat()} exceeds the 12-month "
                    "limitation period for this offence category."
                )
            elif limitation_days == 365:
                warning = True
                reasons.append(
                    "Offence date "
                    f"{earliest_fact_date.isoformat()} is exactly at the "
                    "12-month limitation boundary and needs judge review."
                )
            else:
                reasons.append(
                    "Filed "
                    f"{limitation_days} days after the earliest offence date "
                    f"{earliest_fact_date.isoformat()}, within the applicable "
                    "limitation period."
                )

    if case.filed_date:
        reasons.append(f"Filed date: {case.filed_date.isoformat()}.")

    if case.jurisdiction_valid is False:
        failure = True

    if failure:
        status_value = "fail"
        valid = False
    elif warning:
        status_value = "warning"
        valid = None
    elif case.jurisdiction_valid is True or reasons:
        status_value = "pass"
        valid = True
    else:
        status_value = "pending"
        valid = case.jurisdiction_valid

    return {"status": status_value, "valid": valid, "reasons": reasons}


def _extract_rejection_reason(case: Case) -> str | None:
    for log in sorted(
        case.audit_logs or [],
        key=lambda item: item.created_at.timestamp() if item.created_at else 0.0,
        reverse=True,
    ):
        for payload in (log.output_payload or {}, log.input_payload or {}):
            issues = payload.get("jurisdiction_issues")
            if isinstance(issues, list) and issues:
                first_issue = issues[0]
                if isinstance(first_issue, str) and first_issue.strip():
                    return first_issue
            for key in ("reason", "detail", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    summary = _build_jurisdiction_summary(case)
    return summary["reasons"][0] if summary["reasons"] else None


def _build_pipeline_progress(case: Case) -> dict[str, Any]:
    completed_agents: set[str] = set()
    running_agent: str | None = None

    grouped_logs: dict[str, list[AuditLog]] = {agent_id: [] for agent_id in PIPELINE_AGENT_ORDER}
    for log in case.audit_logs or []:
        if log.agent_name in grouped_logs:
            grouped_logs[log.agent_name].append(log)

    for agent_id in PIPELINE_AGENT_ORDER:
        logs = grouped_logs[agent_id]
        if not logs:
            continue
        last_action = (logs[-1].action or "").lower()
        if "fail" in last_action or "error" in last_action:
            break
        if any(token in last_action for token in ("complet", "done", "finish", "emit", "persist")):
            completed_agents.add(agent_id)
            continue
        running_agent = agent_id
        break

    if case.status in {CaseStatus.ready_for_review, CaseStatus.decided, CaseStatus.closed}:
        return {"pipeline_progress_percent": 100, "current_agent": None}

    if case.status == CaseStatus.failed:
        progress_percent = round(
            len(completed_agents) / len(PIPELINE_AGENT_ORDER) * 100,
        )
        return {
            "pipeline_progress_percent": progress_percent,
            "current_agent": running_agent,
        }

    if case.status == CaseStatus.processing and running_agent is None:
        remaining = [agent for agent in PIPELINE_AGENT_ORDER if agent not in completed_agents]
        running_agent = remaining[0] if remaining else None

    return {
        "pipeline_progress_percent": round(len(completed_agents) / len(PIPELINE_AGENT_ORDER) * 100),
        "current_agent": running_agent,
    }


def _serialize_case_summary(case: Case) -> dict[str, Any]:
    role_lookup = _party_role_lookup(case.parties or [])
    decision_history = _extract_decision_history(case.audit_logs or [])
    latest_decision = decision_history[-1] if decision_history else None
    latest_verdict = _extract_latest_verdict(case)
    description = case.description or case.title or ""
    summary_snippet = description[:157] + "..." if len(description) > 160 else description

    outcome_summary = None
    if latest_decision and latest_decision.get("final_order"):
        outcome_summary = latest_decision["final_order"]
    elif latest_verdict is not None:
        outcome_summary = latest_verdict.recommended_outcome

    amendment_state = "amended" if any(v.amendment_of for v in case.verdicts or []) else None
    reopen_requests = list(case.reopen_requests or [])
    reopen_state = reopen_requests[-1].status.value if reopen_requests else None

    return {
        "id": case.id,
        "case_id": case.id,
        "title": _display_text(case.title, f"Case {case.id}"),
        "description": case.description,
        "summary_snippet": summary_snippet,
        "domain": case.domain,
        "status": case.status,
        "status_group": _status_group(case.status),
        "jurisdiction": _build_jurisdiction_summary(case),
        "complexity": case.complexity.value if case.complexity else None,
        "route": case.route.value if case.route else None,
        "created_by": case.created_by,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "filed_date": case.filed_date,
        "claim_amount": case.claim_amount,
        "consent_to_higher_claim_limit": case.consent_to_higher_claim_limit,
        "offence_code": case.offence_code,
        "parties": _serialize_parties(case.parties or []),
        "party_names": [party.name for party in case.parties or []],
        "claimant_name": role_lookup.get("claimant"),
        "respondent_name": role_lookup.get("respondent"),
        "prosecution_name": role_lookup.get("prosecution"),
        "accused_name": role_lookup.get("accused"),
        "document_count": len(case.documents or []),
        "pipeline_progress": _build_pipeline_progress(case),
        "outcome_summary": outcome_summary,
        "escalation_reason": _extract_escalation_reason(case),
        "reopen_state": reopen_state,
        "amendment_state": amendment_state,
        "latest_decision": latest_decision,
    }


def _serialize_case_detail(case: Case) -> dict[str, Any]:
    summary = _serialize_case_summary(case)
    summary.update(
        {
            "documents": [
                {
                    "id": document.id,
                    "openai_file_id": _optional_text(getattr(document, "openai_file_id", None)),
                    "filename": document.filename,
                    "file_type": _optional_text(getattr(document, "file_type", None)),
                    "uploaded_at": document.uploaded_at,
                }
                for document in case.documents or []
            ],
            "evidence": list(case.evidence or []),
            "facts": list(case.facts or []),
            "witnesses": list(case.witnesses or []),
            "legal_rules": list(case.legal_rules or []),
            "precedents": list(case.precedents or []),
            "arguments": list(case.arguments or []),
            "deliberations": list(case.deliberations or []),
            "verdicts": list(case.verdicts or []),
            "decision_history": _extract_decision_history(case.audit_logs or []),
            "audit_logs": list(case.audit_logs or []),
        }
    )
    return summary


async def _load_case_for_export(case_id: UUID, db, current_user: User) -> Case:
    """Fetch a case and enforce clerk ownership. Raises 404/403."""
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this case",
        )
    return case


@router.post(
    "/",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_case",
    summary="Create a new case",
    description="Create a new judicial case in the specified domain. Requires clerk or judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
)
async def create_case(
    body: CaseCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.clerk, UserRole.judge, UserRole.senior_judge),
) -> dict[str, Any]:
    case = Case(
        id=uuid4(),
        domain=body.domain,
        title=body.title.strip(),
        description=body.description.strip() if body.description else None,
        filed_date=body.filed_date,
        claim_amount=body.claim_amount,
        consent_to_higher_claim_limit=body.consent_to_higher_claim_limit,
        offence_code=body.offence_code,
        created_by=current_user.id,
    )
    for party in body.parties:
        case.parties.append(
            Party(
                id=uuid4(),
                name=party.name.strip(),
                role=party.role,
                contact_info=party.contact_info,
            )
        )
    db.add(case)
    await db.flush()
    await db.refresh(case)
    return _serialize_case_summary(case)


@router.get(
    "/",
    response_model=CaseListResponse,
    operation_id="list_cases",
    summary="List cases with pagination",
    description="List cases with story-aligned filters and pagination.",
)
async def list_cases(
    db: DBSession,
    current_user: CurrentUser,
    status_filter: str | None = Query(None, alias="status"),
    domain: CaseDomain | None = None,
    search: str | None = Query(None),
    complexity: str | None = Query(None),
    outcome: str | None = Query(None),
    filed_from: date | None = Query(None),
    filed_to: date | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    query = select(Case)

    if current_user.role in {UserRole.clerk, UserRole.judge}:
        query = query.where(Case.created_by == current_user.id)

    if status_filter:
        status_values = _map_status_filter(status_filter)
        query = query.where(Case.status.in_(status_values))
    if domain:
        query = query.where(Case.domain == domain)
    if complexity:
        try:
            query = query.where(Case.complexity == CaseComplexity(complexity))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown complexity filter: {complexity}",
            ) from exc
    if filed_from:
        query = query.where(Case.filed_date >= filed_from)
    if filed_to:
        query = query.where(Case.filed_date <= filed_to)
    if outcome:
        pattern = f"%{outcome.strip()}%"
        query = query.where(Case.verdicts.any(Verdict.recommended_outcome.ilike(pattern)))
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Case.title.ilike(pattern),
                Case.description.ilike(pattern),
                Case.parties.any(Party.name.ilike(pattern)),
                Case.facts.any(Fact.description.ilike(pattern)),
            )
        )

    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = (await db.execute(count_query)).scalar_one()

    sort_column = {
        "created_at": Case.created_at,
        "filed_date": Case.filed_date,
        "status": Case.status,
        "complexity": Case.complexity,
    }.get(sort_by, Case.created_at)
    if sort_direction.lower() == "asc":
        query = query.order_by(sort_column.asc().nullslast())
    else:
        query = query.order_by(sort_column.desc().nullslast())

    query = query.options(
        selectinload(Case.parties),
        selectinload(Case.documents),
        selectinload(Case.facts),
        selectinload(Case.verdicts),
        selectinload(Case.reopen_requests),
        selectinload(Case.audit_logs),
    )
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return {
        "items": [_serialize_case_summary(item) for item in items],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get(
    "/{case_id}",
    response_model=CaseDetailResponse,
    operation_id="get_case",
    summary="Get full case details",
    description="Retrieve a case with all related entities and story-aligned summary fields.",
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_case(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.parties),
            selectinload(Case.documents),
            selectinload(Case.evidence),
            selectinload(Case.facts),
            selectinload(Case.witnesses),
            selectinload(Case.legal_rules),
            selectinload(Case.precedents),
            selectinload(Case.arguments),
            selectinload(Case.deliberations),
            selectinload(Case.verdicts),
            selectinload(Case.reopen_requests),
            selectinload(Case.audit_logs),
        )
    )
    case = result.scalar_one_or_none()

    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this case",
        )

    return _serialize_case_detail(case)


@router.post(
    "/{case_id}/rejection-review",
    response_model=RejectionReviewResponse,
    operation_id="review_rejected_case",
    summary="Override or close a rejected case",
    responses={
        400: {"model": ErrorResponse, "description": "Case is not rejected"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def review_rejected_case(
    case_id: UUID,
    body: RejectionReviewRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge, UserRole.senior_judge),
) -> RejectionReviewResponse:
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(selectinload(Case.audit_logs))
        .with_for_update()
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.status != CaseStatus.rejected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only rejected cases can be reviewed through this endpoint",
        )

    rejection_reason = _extract_rejection_reason(case)
    if body.action.value == "override":
        case.status = CaseStatus.processing
    else:
        case.status = CaseStatus.closed

    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action=f"rejection_{body.action.value}",
            input_payload={
                "justification": body.justification,
                "judge_id": str(current_user.id),
                "rejection_reason": rejection_reason,
            },
            output_payload={"new_status": case.status.value},
        )
    )

    if body.action.value == "override":
        from src.models.pipeline_job import PipelineJobType
        from src.workers.outbox import enqueue_outbox_job

        await enqueue_outbox_job(
            db,
            case_id=case_id,
            job_type=PipelineJobType.case_pipeline,
            payload={"resume_from_stage": "case-processing", "resume_reason": "rejection_override"},
        )

    await db.commit()

    return RejectionReviewResponse(
        case_id=case_id,
        action=body.action,
        status=case.status,
        rejection_reason=rejection_reason,
        resumed_from_stage="case-processing" if body.action.value == "override" else None,
        message="Rejected case returned to processing."
        if body.action.value == "override"
        else "Rejected case closed and archived.",
    )


@router.get(
    "/{case_id}/report.pdf",
    operation_id="export_case_report_pdf",
    summary="Export the case as a PDF report",
    description=(
        "Render a case summary PDF covering parties, evidence, facts, "
        "arguments, verdict, and fairness report."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def export_case_report_pdf(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Response:
    await _load_case_for_export(case_id, db, current_user)
    data = await build_case_report_data(db, case_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    pdf_bytes = render_case_report_pdf(data)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="case-{case_id}-report.pdf"',
        },
    )


@router.get(
    "/{case_id}/hearing-pack",
    operation_id="export_hearing_pack",
    summary="Export the hearing pack zip for a case",
    description=(
        "Assemble a zip archive of manifest, case summary, evidence, "
        "facts, arguments, and verdict for in-court review."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def export_hearing_pack(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> Response:
    await _load_case_for_export(case_id, db, current_user)
    data = await build_case_report_data(db, case_id)
    if data is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    zip_bytes = assemble_pack(data)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="case-{case_id}-hearing-pack.zip"',
        },
    )


@router.get(
    "/{case_id}/status/stream",
    operation_id="stream_pipeline_status",
    summary="Stream pipeline progress events via SSE",
    description="Server-Sent Events stream backed by the Redis progress pub/sub.",
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def stream_pipeline_status(
    case_id: UUID,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    await _load_case_for_export(case_id, db, current_user)

    async def event_generator():
        # Producer-consumer pattern: a background task owns the subscribe()
        # generator for its full lifetime and pushes payloads onto a queue.
        # The consumer wakes up every SSE_HEARTBEAT_SECONDS to emit keepalives
        # and check for client disconnect / watchdog expiry. This avoids the
        # wait_for-on-__anext__ pitfall where a heartbeat-driven cancellation
        # would tear down the underlying pubsub mid-iteration.
        queue: asyncio.Queue[str] = asyncio.Queue()
        producer_done = asyncio.Event()

        async def _producer():
            try:
                async for payload in subscribe_pipeline_events(case_id):
                    await queue.put(payload)
            finally:
                producer_done.set()

        producer_task = asyncio.create_task(_producer())
        stream_start = time.monotonic()

        try:
            while True:
                if await request.is_disconnected():
                    return
                remaining = SSE_WATCHDOG_SECONDS - (time.monotonic() - stream_start)
                if remaining <= 0:
                    timeout_event = {
                        "case_id": str(case_id),
                        "agent": "pipeline",
                        "phase": "terminal",
                        "ts": datetime.now(UTC).isoformat(),
                        "detail": {
                            "reason": "watchdog_timeout",
                            "stopped_at": "sse-stream",
                        },
                    }
                    yield f"data: {json.dumps(timeout_event)}\n\n"
                    return
                try:
                    payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(SSE_HEARTBEAT_SECONDS, remaining),
                    )
                except TimeoutError:
                    if producer_done.is_set() and queue.empty():
                        return
                    # SSE comment lines keep idle connections warm without
                    # polluting the event log on the subscriber side.
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {payload}\n\n"
                if producer_done.is_set() and queue.empty():
                    return
        finally:
            producer_task.cancel()
            # Cancellation and cleanup errors must not escape — the client
            # already disconnected or the watchdog fired.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer_task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _run_case_pipeline(case_id: UUID) -> None:
    """Background task: run the 9-agent pipeline for a case and persist results."""

    from src.db.persist_case_results import persist_case_results
    from src.models.case import CaseStatus as CaseStatusModel
    from src.services.database import async_session
    from src.shared.case_state import CaseState
    from src.shared.config import settings

    async with async_session() as db:
        case_result = await db.execute(
            select(Case)
            .where(Case.id == case_id)
            .options(selectinload(Case.documents), selectinload(Case.parties))
        )
        case = case_result.scalar_one_or_none()
        if not case:
            logger.warning("run_case_pipeline: case %s not found", case_id)
            return

        raw_documents = [
            {
                "document_id": str(document.id),
                "filename": document.filename,
                "file_type": _optional_text(getattr(document, "file_type", None)),
                "openai_file_id": _optional_text(getattr(document, "openai_file_id", None)),
            }
            for document in case.documents
        ]
        initial_state = CaseState(
            case_id=str(case.id),
            domain=case.domain.value,
            status="processing",
            parties=[
                {
                    "name": party.name,
                    "role": party.role.value,
                    "contact_info": party.contact_info,
                }
                for party in case.parties
            ],
            case_metadata={
                "title": case.title,
                "description": case.description,
                "filed_date": case.filed_date.isoformat() if case.filed_date else None,
                "claim_amount": case.claim_amount,
                "consent_to_higher_claim_limit": case.consent_to_higher_claim_limit,
                "offence_code": case.offence_code,
            },
            raw_documents=raw_documents,
        )

    try:
        if settings.use_mesh_runner:
            from src.pipeline.mesh_runner_factory import get_mesh_runner

            runner = await get_mesh_runner()
            final_state = await runner.run(initial_state, run_id=initial_state.run_id)
        else:
            from src.pipeline.runner import PipelineRunner

            final_state = await PipelineRunner().run(initial_state)
    except Exception:
        logger.exception("Pipeline run failed for case_id=%s", case_id)
        async with async_session() as db:
            db_case = (
                await db.execute(select(Case).where(Case.id == case_id))
            ).scalar_one_or_none()
            if db_case:
                db_case.status = CaseStatusModel.failed
                await db.commit()
        return

    async with async_session() as db:
        await persist_case_results(db, case_id, final_state)


@router.post(
    "/{case_id}/process",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="process_case",
    summary="Start the 9-agent pipeline for a case",
    description="Kicks off the multi-agent pipeline asynchronously.",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Case has no documents or is in a terminal state",
        },
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def process_case(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> MessageResponse:
    result = await db.execute(
        select(Case).where(Case.id == case_id).options(selectinload(Case.documents))
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if current_user.role == UserRole.clerk and case.created_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to run this case",
        )

    if not case.documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case has no uploaded documents",
        )

    flip = await db.execute(
        update(Case)
        .where(Case.id == case_id, Case.status.in_(STARTABLE_STATUSES))
        .values(status=CaseStatus.processing)
        .returning(Case.id)
    )
    if flip.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Case is not in a startable state",
        )

    # Outbox INSERT shares the status-flip transaction so a post-commit
    # crash cannot leave a case `processing` without a pending job.
    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    await enqueue_outbox_job(db, case_id=case_id, job_type=PipelineJobType.case_pipeline)
    await db.commit()

    return MessageResponse(message="Pipeline started")
