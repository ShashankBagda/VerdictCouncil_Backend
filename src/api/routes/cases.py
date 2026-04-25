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
    CaseConfirmRequest,
    CaseCreateRequest,
    CaseDetailResponse,
    CaseDraftCreateRequest,
    CaseIntakeMessageRequest,
    CaseListResponse,
    CaseResponse,
    GateAdvanceRequest,
    GateRerunRequest,
    JudicialDecisionCreate,
    SuggestedQuestionsUpdate,
)
from src.api.schemas.common import ErrorResponse, MessageResponse, ValidationErrorResponse
from src.models.audit import AuditLog
from src.models.case import (
    Case,
    CaseComplexity,
    CaseDomain,
    CaseStatus,
    Fact,
    Party,
)
from src.models.pipeline_event import PipelineEvent
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
    "hearing-analysis",
    "hearing-governance",
]


_GATE_PAUSE_STATUSES = {
    CaseStatus.awaiting_review_gate1,
    CaseStatus.awaiting_review_gate2,
    CaseStatus.awaiting_review_gate3,
    CaseStatus.awaiting_review_gate4,
}


def _status_group(status_value: CaseStatus) -> str:
    if status_value in {CaseStatus.pending, CaseStatus.processing, CaseStatus.failed_retryable}:
        return "processing"
    if status_value in _GATE_PAUSE_STATUSES:
        return "awaiting_review"
    if status_value == CaseStatus.ready_for_review:
        return "completed"
    if status_value == CaseStatus.escalated:
        return "escalated"
    if status_value == CaseStatus.closed:
        return "closed"
    if status_value == CaseStatus.failed:
        return "failed"
    return status_value.value


def _map_status_filter(status_filter: str) -> list[CaseStatus]:
    raw = status_filter.strip().lower()
    if raw == "completed":
        return [CaseStatus.ready_for_review]
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
            # Offence codes are not a closed set — the RTA lists many sections
            # and sitting judges are the authority on which code applies. We
            # record the code on the jurisdiction summary for audit, but never
            # fail the case on an allow-list mismatch.
            reasons.append(f"Offence code recorded: {case.offence_code}.")
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

    if case.status in {CaseStatus.ready_for_review, CaseStatus.closed}:
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
    description = case.description or case.title or ""
    summary_snippet = description[:157] + "..." if len(description) > 160 else description

    reopen_requests = list(case.reopen_requests or [])
    reopen_state = reopen_requests[-1].status.value if reopen_requests else None

    return {
        "id": case.id,
        "case_id": case.id,
        "title": _display_text(case.title, f"Case {case.id}"),
        "description": case.description,
        "summary_snippet": summary_snippet,
        "domain": case.domain,
        "domain_id": case.domain_id,
        "domain_detail": (
            {"id": case.domain_ref.id, "code": case.domain_ref.code, "name": case.domain_ref.name}
            if case.domain_ref
            else None
        ),
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
        "escalation_reason": _extract_escalation_reason(case),
        "reopen_state": reopen_state,
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
            "hearing_analyses": list(case.hearing_analyses or []),
            "audit_logs": list(case.audit_logs or []),
            "domain_has_vector_store": bool(
                case.domain_ref and case.domain_ref.is_active and case.domain_ref.vector_store_id
            ),
        }
    )
    return summary


async def _load_case_for_export(case_id: UUID, db, current_user: User) -> Case:
    """Fetch a case, raising 404 if not found."""
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case


@router.post(
    "/",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_case",
    summary="Create a new case",
    description="Create a new judicial case in the specified domain. Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        422: {"model": ValidationErrorResponse, "description": "Validation error"},
    },
)
async def create_case(
    body: CaseCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> dict[str, Any]:
    from src.models.domain import Domain as DomainModel

    # Resolve domain row — must be active
    domain_row: DomainModel | None = None
    if body.domain_id is not None:
        domain_row = await db.get(DomainModel, body.domain_id)
        if domain_row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Domain {body.domain_id} not found",
            )
        # Check for disagreement between domain_id and legacy domain enum
        if body.domain is not None and body.domain.value != domain_row.code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="domain_id and domain disagree — send only one or ensure they match",
            )
    elif body.domain is not None:
        result = await db.execute(
            select(DomainModel)
            .where(DomainModel.code == body.domain.value)
            .with_for_update(read=True)
        )
        domain_row = result.scalar_one_or_none()
        if domain_row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Domain seed row missing — contact platform team",
            )

    if domain_row is not None and not domain_row.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Domain '{domain_row.code}' is not active",
        )

    # Resolve legacy domain enum from domain_row if only domain_id was sent
    legacy_domain = body.domain
    if legacy_domain is None and domain_row is not None:
        from src.models.case import CaseDomain

        try:
            legacy_domain = CaseDomain(domain_row.code)
        except ValueError:
            legacy_domain = None  # New domain without a legacy enum value — OK

    case = Case(
        id=uuid4(),
        domain=legacy_domain,
        domain_id=domain_row.id if domain_row else None,
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
    if case.domain_id:
        await db.refresh(case, ["domain_ref"])
    return _serialize_case_summary(case)


# ---------------------------------------------------------------------------
# Chat-first intake — draft / confirm / extract / stream / message
# ---------------------------------------------------------------------------
#
# The legacy POST /cases/ above takes a fully-typed payload from the judge.
# The new intake flow inverts this: the judge picks a domain and uploads
# typed documents; the extractor proposes fields; the judge confirms via
# the chat surface; only then does the case reach `pending` and become
# eligible for the 9-agent pipeline.


@router.post(
    "/draft",
    response_model=CaseResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_case_draft",
    summary="Create a draft case for docs-as-source-of-truth intake",
)
async def create_case_draft(
    body: CaseDraftCreateRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> dict[str, Any]:
    from src.models.domain import Domain as DomainModel

    domain_row: DomainModel | None = None
    if body.domain_id is not None:
        domain_row = await db.get(DomainModel, body.domain_id)
        if domain_row is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Domain {body.domain_id} not found",
            )
    elif body.domain is not None:
        result = await db.execute(select(DomainModel).where(DomainModel.code == body.domain.value))
        domain_row = result.scalar_one_or_none()
        if domain_row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Domain seed row missing — contact platform team",
            )

    if domain_row is not None and not domain_row.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Domain '{domain_row.code}' is not active",
        )

    legacy_domain = body.domain
    if legacy_domain is None and domain_row is not None:
        try:
            legacy_domain = CaseDomain(domain_row.code)
        except ValueError:
            legacy_domain = None

    case = Case(
        id=uuid4(),
        domain=legacy_domain,
        domain_id=domain_row.id if domain_row else None,
        filed_date=body.filed_date,
        status=CaseStatus.draft,
        created_by=current_user.id,
    )
    db.add(case)
    await db.flush()
    # _serialize_case_summary + _build_jurisdiction_summary +
    # _extract_escalation_reason + _build_pipeline_progress walk six lazy
    # relationships. On the legacy create path ≥2 parties are always
    # populated in session memory, so `case.parties` never lazy-loads —
    # but `case.facts`, `case.audit_logs`, etc. still would. The old path
    # gets away with it because those collections are only touched if
    # facts/audit rows exist, which for a freshly-created case is always
    # empty and returns fast from the ORM's null-marker path (not a real
    # query). For a draft created with NO parties, the very first
    # relationship access trips the greenlet wall before any of that
    # happy-path short-circuiting kicks in. Refresh them all so the
    # collections materialise as real (empty) lists.
    await db.refresh(
        case,
        ["parties", "documents", "reopen_requests", "facts", "audit_logs"],
    )
    if case.domain_id:
        await db.refresh(case, ["domain_ref"])
    return _serialize_case_summary(case)


@router.post(
    "/{case_id}/confirm",
    response_model=CaseResponse,
    status_code=status.HTTP_200_OK,
    operation_id="confirm_case_intake",
    summary="Judge confirms extracted fields — transitions draft/awaiting_intake_confirmation → pending",  # noqa: E501
)
async def confirm_case_intake(
    case_id: UUID,
    body: CaseConfirmRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> dict[str, Any]:
    # Eager-load every collection we'll touch before returning: parties
    # (mutated below), plus the relationships the summary serializer walks
    # (documents, facts, reopen_requests, audit_logs, domain_ref). Without
    # selectinload, `case.parties` would lazy-load, which triggers an
    # autoflush — and with dirty scalar attributes already pending on the
    # session that flush hits asyncpg outside an active greenlet and 500s.
    result = await db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.parties),
            selectinload(Case.documents),
            selectinload(Case.facts),
            selectinload(Case.reopen_requests),
            selectinload(Case.audit_logs),
            selectinload(Case.domain_ref),
        )
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if case.status not in (
        CaseStatus.draft,
        CaseStatus.extracting,
        CaseStatus.awaiting_intake_confirmation,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Case cannot be confirmed from status {case.status.value}",
        )

    # Domain-specific validation was loosened on CaseConfirmRequest (no
    # closed offence-code whitelist), but we still enforce the SCT
    # jurisdictional caps that the Act fixes in statute.
    if case.domain == CaseDomain.small_claims:
        if body.claim_amount is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Small-claims matters require claim_amount at confirm",
            )
        if body.claim_amount > 30000:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="SCT claim_amount exceeds the $30,000 jurisdiction limit.",
            )
        if body.claim_amount > 20000 and not body.consent_to_higher_claim_limit:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="SCT claim_amount above $20,000 requires consent_to_higher_claim_limit.",
            )
    if case.domain == CaseDomain.traffic_violation and not body.offence_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Traffic matters require offence_code at confirm",
        )

    case.title = body.title.strip()
    case.description = body.description.strip() if body.description else None
    case.filed_date = body.filed_date
    case.claim_amount = body.claim_amount
    case.consent_to_higher_claim_limit = body.consent_to_higher_claim_limit
    case.offence_code = body.offence_code
    case.status = CaseStatus.pending

    # Replace parties wholesale — confirm is authoritative. The Party rows
    # have ON DELETE CASCADE so orphans are cleaned up automatically.
    for existing in list(case.parties):
        await db.delete(existing)
    for party in body.parties:
        case.parties.append(
            Party(
                id=uuid4(),
                name=party.name.strip(),
                role=party.role,
                contact_info=party.contact_info,
            )
        )

    await db.commit()
    # commit() expires all ORM attributes. Refresh scalars first, then the
    # lazy collections the serializer walks, to avoid MissingGreenlet.
    await db.refresh(case)
    await db.refresh(
        case,
        ["parties", "documents", "reopen_requests", "facts", "audit_logs"],
    )
    if case.domain_id:
        await db.refresh(case, ["domain_ref"])

    # Close the intake stream for any subscribers still listening.
    from src.services.intake_events import publish_intake_event

    await publish_intake_event(
        case_id,
        {"type": "confirmed", "ts": datetime.now(UTC).isoformat()},
    )

    return _serialize_case_summary(case)


@router.post(
    "/{case_id}/intake/extract",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="trigger_intake_extraction",
    summary="(Re-)enqueue intake extraction for a draft case",
)
async def trigger_intake_extraction(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> MessageResponse:
    case = await db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    if case.status not in (
        CaseStatus.draft,
        CaseStatus.extracting,
        CaseStatus.awaiting_intake_confirmation,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Intake extraction not valid from status {case.status.value}",
        )

    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    case.status = CaseStatus.extracting
    await enqueue_outbox_job(db, case_id=case_id, job_type=PipelineJobType.intake_extraction)
    await db.commit()
    return MessageResponse(message="Intake extraction enqueued")


@router.post(
    "/{case_id}/intake/message",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="send_intake_message",
    summary="Judge correction on the intake chat — re-runs extraction",
)
async def send_intake_message(
    case_id: UUID,
    body: CaseIntakeMessageRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> MessageResponse:
    case = await db.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    if case.status not in (
        CaseStatus.draft,
        CaseStatus.extracting,
        CaseStatus.awaiting_intake_confirmation,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot accept intake message from status {case.status.value}",
        )

    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    case.status = CaseStatus.extracting
    await enqueue_outbox_job(
        db,
        case_id=case_id,
        job_type=PipelineJobType.intake_extraction,
        payload={"correction": body.content},
    )
    await db.commit()

    # Echo the judge's message on the stream so the frontend chat renders
    # it immediately without waiting for the worker to pick the job up.
    from src.services.intake_events import publish_intake_event

    await publish_intake_event(
        case_id,
        {
            "type": "user_message",
            "content": body.content,
            "ts": datetime.now(UTC).isoformat(),
        },
    )
    return MessageResponse(message="Correction received — re-extracting")


@router.get(
    "/{case_id}/intake/stream",
    operation_id="stream_intake_events",
    summary="SSE stream of intake extraction events (ai-sdk UIMessage format)",
    description=(
        "Server-Sent Events stream backed by the intake Redis pub/sub channel. "
        "Closes on `done`, `error`, or `confirmed` events."
    ),
)
async def stream_intake_events(
    case_id: UUID,
    request: Request,
    db: DBSession,
    current_user: CurrentUser,
) -> StreamingResponse:
    case = await _load_case_for_export(case_id, db, current_user)

    from src.services.intake_events import subscribe_intake_events

    async def event_generator():
        # Snapshot-on-connect so a client that reconnects after the worker
        # already finished still sees the latest extraction without polling.
        if case.intake_extraction is not None:
            snap = {
                "type": "done"
                if case.status == CaseStatus.awaiting_intake_confirmation
                else "status",
                "phase": "reconnect_snapshot",
                "extraction": case.intake_extraction,
                "ts": datetime.now(UTC).isoformat(),
            }
            yield f"data: {json.dumps(snap)}\n\n"

        queue: asyncio.Queue[str] = asyncio.Queue()
        producer_done = asyncio.Event()

        async def _producer():
            try:
                async for payload in subscribe_intake_events(case_id):
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
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "error",
                                "message": "watchdog_timeout",
                                "ts": datetime.now(UTC).isoformat(),
                            }
                        )
                        + "\n\n"
                    )
                    return
                try:
                    payload = await asyncio.wait_for(
                        queue.get(), timeout=min(SSE_HEARTBEAT_SECONDS, remaining)
                    )
                except TimeoutError:
                    if producer_done.is_set() and queue.empty():
                        return
                    heartbeat = {
                        "kind": "heartbeat",
                        "schema_version": 1,
                        "ts": datetime.now(UTC).isoformat(),
                    }
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"
                    continue
                yield f"data: {payload}\n\n"
                if producer_done.is_set() and queue.empty():
                    return
        finally:
            producer_task.cancel()
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
    filed_from: date | None = Query(None),
    filed_to: date | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    query = select(Case)

    if current_user.role == UserRole.judge:
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
        selectinload(Case.reopen_requests),
        selectinload(Case.audit_logs),
        selectinload(Case.domain_ref),
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
            selectinload(Case.hearing_analyses),
            selectinload(Case.reopen_requests),
            selectinload(Case.audit_logs),
            selectinload(Case.domain_ref),
        )
    )
    case = result.scalar_one_or_none()

    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    return _serialize_case_detail(case)


@router.get(
    "/{case_id}/report.pdf",
    operation_id="export_case_report_pdf",
    summary="Export the case as a PDF report",
    description=(
        "Render a case summary PDF covering parties, evidence, facts, arguments, verdict, and fairness report."  # noqa: E501
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
        "Assemble a zip archive of manifest, case summary, evidence, facts, arguments, and verdict for in-court review."  # noqa: E501
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
    "/{case_id}/events",
    operation_id="list_pipeline_events",
    summary="List recorded pipeline events for a case",
    description=(
        "Returns a paginated log of every SSE event written to the replay table. "
        "Useful for debugging and replay without a live SSE stream."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized to view this case"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def list_pipeline_events(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    case_row = await db.get(Case, case_id)
    if case_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if current_user.role not in (UserRole.admin, UserRole.judge, UserRole.senior_judge):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    rows = await db.execute(
        select(PipelineEvent)
        .where(PipelineEvent.case_id == case_id)
        .order_by(PipelineEvent.ts.asc())
        .limit(limit)
        .offset(offset)
    )
    events = rows.scalars().all()
    return {
        "case_id": str(case_id),
        "total": len(events),
        "limit": limit,
        "offset": offset,
        "events": [
            {
                "id": str(e.id),
                "kind": e.kind,
                "schema_version": e.schema_version,
                "agent": e.agent,
                "ts": e.ts.isoformat(),
                "payload": e.payload,
            }
            for e in events
        ],
    }


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
    import jwt as _jwt

    # Read vc_token from the cookie header directly so we can surface
    # impending expiry without keeping the DB session open for the
    # lifetime of the SSE stream.
    vc_token = request.cookies.get("vc_token")

    token_expires_at: datetime | None = None
    if vc_token:
        try:
            payload = _jwt.decode(
                vc_token,
                options={"verify_signature": False, "verify_exp": False},
                algorithms=["HS256"],
            )
            exp = payload.get("exp")
            if exp:
                token_expires_at = datetime.fromtimestamp(exp, UTC)
        except Exception:
            pass  # auth already validated by CurrentUser; expiry warning is best-effort

    await _load_case_for_export(case_id, db, current_user)

    async def event_generator():
        # Snapshot-on-connect: emit current case status so a client that
        # re-subscribes after a gate advance sees the current state immediately,
        # without waiting for the next Redis event. Uses the already-open
        # dependency-injected session so tests can mock it cleanly.
        _snap_case = await db.get(Case, case_id)
        if _snap_case is not None:
            snap_event = {
                "kind": "progress",
                "schema_version": 1,
                "case_id": str(case_id),
                "agent": "pipeline",
                "phase": "case.status",
                "ts": datetime.now(UTC).isoformat(),
                "detail": {
                    "status": _snap_case.status.value if _snap_case.status else None,
                    "gate_state": _snap_case.gate_state,
                },
            }
            yield f"event: progress\ndata: {json.dumps(snap_event)}\n\n"

        # Producer-consumer pattern: a background task owns the subscribe()
        # generator for its full lifetime and pushes payloads onto a queue.
        # The consumer wakes up every SSE_HEARTBEAT_SECONDS to emit keepalives
        # and check for client disconnect / watchdog expiry.
        queue: asyncio.Queue[str] = asyncio.Queue()
        producer_done = asyncio.Event()

        async def _redis_producer():
            try:
                async for payload in subscribe_pipeline_events(case_id):
                    await queue.put(payload)
            finally:
                producer_done.set()

        producer_task = asyncio.create_task(_redis_producer())
        stream_start = time.monotonic()

        try:
            while True:
                if await request.is_disconnected():
                    return
                remaining = SSE_WATCHDOG_SECONDS - (time.monotonic() - stream_start)
                if remaining <= 0:
                    timeout_event = {
                        "kind": "progress",
                        "schema_version": 1,
                        "case_id": str(case_id),
                        "agent": "pipeline",
                        "phase": "terminal",
                        "ts": datetime.now(UTC).isoformat(),
                        "detail": {
                            "reason": "watchdog_timeout",
                            "stopped_at": "sse-stream",
                        },
                    }
                    yield f"event: progress\ndata: {json.dumps(timeout_event)}\n\n"
                    return
                try:
                    payload = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(SSE_HEARTBEAT_SECONDS, remaining),
                    )
                except TimeoutError:
                    if producer_done.is_set() and queue.empty():
                        return
                    now = datetime.now(UTC)
                    heartbeat = {
                        "kind": "heartbeat",
                        "schema_version": 1,
                        "ts": now.isoformat(),
                    }
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat)}\n\n"
                    # Emit auth_expiring when the session cookie will expire within
                    # 2 × heartbeat interval (≥ 60 s warning window at default 15 s
                    # heartbeat). Best-effort: skip if token expiry is unavailable.
                    if token_expires_at is not None:
                        secs_left = (token_expires_at - now).total_seconds()
                        if 0 < secs_left <= 2 * SSE_HEARTBEAT_SECONDS + 60:
                            auth_expiring = {
                                "kind": "auth_expiring",
                                "schema_version": 1,
                                "expires_at": token_expires_at.isoformat(),
                            }
                            yield (f"event: auth_expiring\ndata: {json.dumps(auth_expiring)}\n\n")
                    continue
                # Extract the `kind` field to emit the named SSE event type so
                # EventSource.addEventListener('progress'/'agent', ...) fires natively.
                try:
                    kind = json.loads(payload).get("kind", "progress")
                except (json.JSONDecodeError, AttributeError):
                    kind = "progress"
                yield f"event: {kind}\ndata: {payload}\n\n"
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


async def _run_case_pipeline(case_id: UUID, *, trace_id: str | None = None) -> None:
    """Background task: run the 9-agent pipeline for a case and persist results.

    `trace_id` (Sprint 2 2.C1.4) is the W3C hex trace id resurrected by the
    worker from `pipeline_jobs.traceparent`. It is stamped onto LangSmith
    metadata so the agent run can be cross-referenced with its OTEL trace.
    """

    from src.api.schemas.pipeline_events import PipelineProgressEvent
    from src.db.persist_case_results import persist_case_results
    from src.models.case import CaseStatus as CaseStatusModel
    from src.services.database import async_session
    from src.services.pipeline_events import (
        check_cancel_flag,
        clear_cancel_flag,
        publish_progress,
    )
    from src.shared.case_state import CaseState, CaseStatusEnum

    # Clear any stale cancel flag left over from a previous run so a
    # freshly-started pipeline does not immediately self-cancel.
    await clear_cancel_flag(case_id)

    async with async_session() as db:
        from sqlalchemy.orm import joinedload as _joinedload

        case_result = await db.execute(
            select(Case)
            .where(Case.id == case_id)
            .options(
                selectinload(Case.documents),
                selectinload(Case.parties),
                _joinedload(Case.domain_ref),
            )
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
                "pages": getattr(document, "pages", None),
            }
            for document in case.documents
        ]

        domain_vector_store_id = (
            case.domain_ref.vector_store_id
            if case.domain_ref and case.domain_ref.is_active
            else None
        )

        initial_state = CaseState(
            case_id=str(case.id),
            domain=case.domain.value if case.domain else None,
            domain_vector_store_id=domain_vector_store_id,
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
        from src.pipeline.graph.runner import GraphPipelineRunner

        final_state = await GraphPipelineRunner().run(initial_state, trace_id=trace_id)
    except Exception as exc:
        logger.exception("Pipeline run failed for case_id=%s", case_id)
        # Sprint 1 1.A1.6: legacy AgentOutputParseError is gone — `create_agent`
        # with `ToolStrategy(handle_errors=True)` retries on validation errors
        # internally, so a leaked exception here is uniformly orchestrator-level.
        reason = "orchestrator_exception"
        mlflow_run_id: str | None = None
        try:
            import mlflow as _mlflow

            active = _mlflow.active_run()
            if active:
                mlflow_run_id = active.info.run_id
        except Exception:
            pass
        await publish_progress(
            PipelineProgressEvent(
                case_id=case_id,
                agent="pipeline",
                phase="failed",
                step=None,
                ts=datetime.now(UTC),
                error=str(exc)[:500],
                detail={
                    "reason": reason,
                    **({"mlflow_run_id": mlflow_run_id} if mlflow_run_id else {}),
                },
            )
        )
        async with async_session() as db:
            db_case = (
                await db.execute(select(Case).where(Case.id == case_id))
            ).scalar_one_or_none()
            if db_case:
                db_case.status = CaseStatusModel.failed
                await db.commit()
        return

    # If the run was cancelled mid-flight the cancel flag is still set and
    # final_state.status is still "processing" (cancelled runs don't advance
    # to a gate-pause or completion status). Gate on both to avoid treating
    # a flag set in the narrow window after a successful run as a cancellation.
    if await check_cancel_flag(case_id) and final_state.status == CaseStatusEnum.processing:
        await clear_cancel_flag(case_id)
        async with async_session() as db:
            db_case = (
                await db.execute(select(Case).where(Case.id == case_id))
            ).scalar_one_or_none()
            if db_case:
                db_case.status = CaseStatusModel.failed
                await db.commit()
        return

    gate_state_payload: dict | None = None
    gate_run_id: str | None = None
    status_val = final_state.status.value if final_state.status else ""
    if status_val.startswith("awaiting_review_gate"):
        gate_num = int(status_val[-1])
        gate_state_payload = {
            "current_gate": gate_num,
            "awaiting_review": True,
            "rerun_agent": None,
        }
        gate_run_id = f"{case_id}-gate{gate_num}"

    async with async_session() as db:
        await persist_case_results(db, case_id, final_state, gate_state_payload=gate_state_payload)

    if gate_run_id and final_state.run_id:
        from src.db.pipeline_state import persist_case_state

        async with async_session() as db:
            await persist_case_state(
                db,
                case_id=case_id,
                run_id=gate_run_id,
                agent_name="gate_complete",
                state=final_state,
            )

    # Close the SSE stream from the backend side: the frontend treats
    # `agent=pipeline` + `phase=awaiting_review|terminal` as the
    # authoritative shutdown signal, but the sequential runner never
    # emits it on its own. Without this, the browser holds an open
    # EventSource well past the gate pause and never shows the gate
    # review UI until the next poll tick catches up.
    if status_val.startswith("awaiting_review_gate"):
        close_event = PipelineProgressEvent(
            case_id=case_id,
            agent="pipeline",
            phase="awaiting_review",
            step=None,
            total=9,
            ts=datetime.now(UTC),
            detail={"reason": "gate_pause", "stopped_at": status_val},
        )
        await publish_progress(close_event)


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


@router.post(
    "/{case_id}/cancel",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="cancel_case_pipeline",
    summary="Request cancellation of a running pipeline",
    description=(
        "Signals the pipeline to stop at the next inter-turn window. "
        "SSE disconnect does NOT cancel the run — use this endpoint instead."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Case not found"},
        409: {"model": ErrorResponse, "description": "Case is not currently processing"},
    },
)
async def cancel_case_pipeline(
    case_id: UUID,
    db: DBSession,
    current_user: CurrentUser,
) -> MessageResponse:
    result = await db.execute(select(Case).where(Case.id == case_id))
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if case.status != CaseStatus.processing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Case is not currently processing",
        )

    from src.services.pipeline_events import set_cancel_flag

    await set_cancel_flag(case_id)
    return MessageResponse(message="Cancellation requested")


# Statuses from which the pipeline can be restarted by a judge.
_RESTARTABLE_STATUSES = (
    CaseStatus.failed,
    CaseStatus.failed_retryable,
    CaseStatus.escalated,
)


@router.post(
    "/{case_id}/restart",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="restart_case_pipeline",
    summary="Restart a failed or escalated case pipeline",
    description=(
        "Reset a case in 'failed', 'failed_retryable', or 'escalated' status back to "
        "'pending' and re-enqueue the full 9-agent pipeline. All prior analysis results "
        "are retained in the database for audit purposes."
    ),
    responses={
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Case not found"},
        409: {"model": ErrorResponse, "description": "Case is not in a restartable state"},
    },
)
async def restart_case_pipeline(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> MessageResponse:
    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    result = await db.execute(
        select(Case).where(Case.id == case_id).options(selectinload(Case.documents))
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if not case.documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Case has no uploaded documents — cannot restart pipeline",
        )

    flip = await db.execute(
        update(Case)
        .where(Case.id == case_id, Case.status.in_(_RESTARTABLE_STATUSES))
        .values(status=CaseStatus.processing)
        .returning(Case.id)
    )
    if flip.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Case cannot be restarted from its current state. "
                "Only 'failed', 'failed_retryable', or 'escalated' cases can be restarted."
            ),
        )

    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="pipeline_restarted",
            input_payload={
                "restarted_by": str(current_user.id),
                "previous_status": case.status.value,
            },
        )
    )
    await enqueue_outbox_job(db, case_id=case_id, job_type=PipelineJobType.case_pipeline)
    await db.commit()

    return MessageResponse(message="Pipeline restarted")


_VALID_GATE_NAMES = {"gate1", "gate2", "gate3", "gate4"}

_NEXT_GATE: dict[str, str | None] = {
    "gate1": "gate2",
    "gate2": "gate3",
    "gate3": "gate4",
    "gate4": None,
}


@router.post(
    "/{case_id}/gates/{gate_name}/advance",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="advance_gate",
    summary="Advance to the next pipeline gate",
)
async def advance_gate(
    case_id: UUID,
    gate_name: str,
    body: GateAdvanceRequest,  # noqa: ARG001
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> MessageResponse:
    from src.models.pipeline_job import PipelineJobType
    from src.workers.outbox import enqueue_outbox_job

    if gate_name not in _VALID_GATE_NAMES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid gate")

    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    expected_pause = CaseStatus(f"awaiting_review_{gate_name}")
    if case.status != expected_pause:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Case is not paused at {gate_name}",
        )

    next_gate = _NEXT_GATE[gate_name]
    if next_gate is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gate 4 is the final gate; record a decision instead",
        )

    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="gate_advanced",
            input_payload={"gate_name": gate_name, "next_gate": next_gate},
        )
    )
    case.status = CaseStatus.processing
    await enqueue_outbox_job(
        db,
        case_id=case_id,
        job_type=PipelineJobType.gate_run,
        payload={"gate_name": next_gate},
    )
    await db.commit()

    return MessageResponse(message=f"Advancing to {next_gate}")


@router.post(
    "/{case_id}/gates/{gate_name}/rerun",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="rerun_gate",
    summary="Re-run agents in the current gate from a specific agent",
)
async def rerun_gate(
    case_id: UUID,
    gate_name: str,
    body: GateRerunRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> MessageResponse:
    from src.models.pipeline_job import PipelineJobType
    from src.pipeline.graph.prompts import GATE_AGENTS
    from src.workers.outbox import enqueue_outbox_job

    if gate_name not in _VALID_GATE_NAMES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid gate")

    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    expected_pause = CaseStatus(f"awaiting_review_{gate_name}")
    if case.status != expected_pause:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Case is not paused at {gate_name}",
        )

    if body.agent_name and body.agent_name not in GATE_AGENTS[gate_name]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Agent {body.agent_name!r} is not in {gate_name}",
        )

    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="gate_rerun_requested",
            input_payload={
                "gate_name": gate_name,
                "start_agent": body.agent_name,
                "has_instructions": bool(body.instructions),
            },
        )
    )
    case.status = CaseStatus.processing
    await enqueue_outbox_job(
        db,
        case_id=case_id,
        job_type=PipelineJobType.gate_run,
        payload={
            "gate_name": gate_name,
            "start_agent": body.agent_name,
            "instructions": body.instructions,
        },
    )
    await db.commit()

    return MessageResponse(message=f"Re-running {gate_name}")


@router.post(
    "/{case_id}/decision",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="record_decision",
    summary="Record the judge's decision with AI engagement responses",
)
async def record_decision(
    case_id: UUID,
    body: JudicialDecisionCreate,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> MessageResponse:
    from datetime import UTC, datetime

    case = (
        await db.execute(
            select(Case)
            .where(Case.id == case_id)
            .options(selectinload(Case.facts), selectinload(Case.hearing_analyses))
        )
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    case.judicial_decision = {
        "verdict_text": body.verdict_text,
        "ai_engagements": [e.model_dump() for e in body.ai_engagements],
        "recorded_at": datetime.now(UTC).isoformat(),
        "judge_id": str(current_user.id),
    }
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="judicial_decision_recorded",
            input_payload={
                "verdict_text": body.verdict_text[:200],
                "engagements_count": len(body.ai_engagements),
            },
        )
    )
    await db.commit()

    return MessageResponse(message="Decision recorded")


@router.patch(
    "/{case_id}/suggested-questions",
    response_model=MessageResponse,
    operation_id="update_suggested_questions",
    summary="Update suggested questions for a case argument",
)
async def update_suggested_questions(
    case_id: UUID,
    body: SuggestedQuestionsUpdate,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> MessageResponse:
    from src.models.case import Argument, ArgumentSide

    case = (await db.execute(select(Case).where(Case.id == case_id))).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    if case.created_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    try:
        side = ArgumentSide(body.side)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid side: {body.side}",
        ) from None

    result = await db.execute(
        select(Argument).where(Argument.case_id == case_id, Argument.side == side)
    )
    argument = result.scalar_one_or_none()
    if not argument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No argument found for side {body.side}",
        )

    argument.suggested_questions = body.questions
    db.add(
        AuditLog(
            case_id=case_id,
            agent_name="judge",
            action="suggested_questions_edit",
            input_payload={"side": body.side, "questions_count": len(body.questions)},
        )
    )
    await db.commit()

    return MessageResponse(message="Suggested questions updated")
