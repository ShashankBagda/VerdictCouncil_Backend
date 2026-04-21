"""Judge-facing endpoints: disputed facts, evidence gaps, fairness audit.

US-009, US-010, US-023.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB

from src.api.deps import DBSession, require_role
from src.api.schemas.common import ErrorResponse
from src.api.schemas.judge import (
    DisputeFactRequest,
    DisputeFactResponse,
    EvidenceGapsResponse,
    FairnessAuditResponse,
    GovernanceFairnessEntry,
    UncorroboratedFact,
    WeakEvidenceItem,
)
from src.models.audit import AuditLog
from src.models.case import (
    Case,
    Evidence,
    EvidenceStrength,
    Fact,
    FactConfidence,
    FactStatus,
    Verdict,
)
from src.models.user import User, UserRole

router = APIRouter()

_AGENT_NAME = "judge"


# --------------------------------------------------------------------------- #
# US-009: Flag Disputed Facts
# --------------------------------------------------------------------------- #


@router.patch(
    "/{case_id}/facts/{fact_id}/dispute",
    response_model=DisputeFactResponse,
    operation_id="dispute_fact",
    summary="Flag a fact as disputed",
    description="Mark a specific fact as disputed. Sets fact status and confidence to 'disputed' "
    "and records the judge's reason. Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Fact not found"},
        409: {"model": ErrorResponse, "description": "Fact is already disputed"},
    },
)
async def dispute_fact(
    case_id: UUID,
    fact_id: UUID,
    body: DisputeFactRequest,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> DisputeFactResponse:
    result = await db.execute(
        select(Fact).where(Fact.id == fact_id, Fact.case_id == case_id).with_for_update()
    )
    fact = result.scalar_one_or_none()
    if not fact:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fact not found")

    if fact.status == FactStatus.disputed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Fact is already disputed.",
        )

    fact.status = FactStatus.disputed
    fact.confidence = FactConfidence.disputed

    existing = dict(fact.corroboration) if fact.corroboration else {}
    existing["dispute_reason"] = body.reason
    existing["disputed_by"] = str(current_user.id)
    fact.corroboration = existing

    audit = AuditLog(
        case_id=case_id,
        agent_name=_AGENT_NAME,
        action="dispute_fact",
        input_payload={"fact_id": str(fact_id), "reason": body.reason},
        output_payload={"status": "disputed", "confidence": "disputed"},
    )
    db.add(audit)
    await db.flush()

    return DisputeFactResponse(
        fact_id=fact.id,
        case_id=fact.case_id,
        status=fact.status,
        confidence=fact.confidence,
        reason=body.reason,
        message="Fact has been marked as disputed.",
    )


# --------------------------------------------------------------------------- #
# US-010: Evidence Gaps
# --------------------------------------------------------------------------- #


@router.get(
    "/{case_id}/evidence-gaps",
    response_model=EvidenceGapsResponse,
    operation_id="get_evidence_gaps",
    summary="Surface evidence gaps for a case",
    description="Returns weak evidence items and facts lacking corroboration. Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_evidence_gaps(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> EvidenceGapsResponse:
    case_result = await db.execute(select(Case).where(Case.id == case_id))
    if case_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    # Weak evidence
    weak_result = await db.execute(
        select(Evidence).where(
            Evidence.case_id == case_id,
            Evidence.strength == EvidenceStrength.weak,
        )
    )
    weak_evidence = list(weak_result.scalars().all())

    # Uncorroborated facts: non-disputed, corroboration IS NULL or empty JSONB object
    uncorroborated_result = await db.execute(
        select(Fact).where(
            Fact.case_id == case_id,
            Fact.status != FactStatus.disputed,
            (Fact.corroboration.is_(None)) | (cast(Fact.corroboration, JSONB) == cast("{}", JSONB)),
        )
    )
    uncorroborated_facts = list(uncorroborated_result.scalars().all())

    # Totals
    total_evidence = (
        await db.execute(
            select(func.count()).select_from(
                select(Evidence).where(Evidence.case_id == case_id).subquery()
            )
        )
    ).scalar_one()

    total_facts = (
        await db.execute(
            select(func.count()).select_from(select(Fact).where(Fact.case_id == case_id).subquery())
        )
    ).scalar_one()

    gap_summary = (
        f"{len(weak_evidence)} of {total_evidence} evidence item(s) are weak; "
        f"{len(uncorroborated_facts)} of {total_facts} fact(s) lack corroboration."
    )

    return EvidenceGapsResponse(
        case_id=case_id,
        weak_evidence=[WeakEvidenceItem.model_validate(e) for e in weak_evidence],
        uncorroborated_facts=[UncorroboratedFact.model_validate(f) for f in uncorroborated_facts],
        total_evidence_count=total_evidence,
        total_fact_count=total_facts,
        gap_summary=gap_summary,
    )


# --------------------------------------------------------------------------- #
# US-023: Fairness & Bias Audit Display
# --------------------------------------------------------------------------- #


@router.get(
    "/{case_id}/fairness-audit",
    response_model=FairnessAuditResponse,
    operation_id="get_fairness_audit",
    summary="Get fairness & bias audit results for a case",
    description="Surfaces the governance agent's fairness check output and the verdict-level "
    "fairness report. Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_fairness_audit(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> FairnessAuditResponse:
    case_result = await db.execute(select(Case).where(Case.id == case_id))
    if case_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    # Most recent verdict fairness_report
    verdict_result = await db.execute(
        select(Verdict).where(Verdict.case_id == case_id).order_by(Verdict.id.desc()).limit(1)
    )
    verdict = verdict_result.scalar_one_or_none()
    verdict_fairness_report = verdict.fairness_report if verdict else None

    # Governance audit log entries
    audit_result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.case_id == case_id,
            AuditLog.agent_name == "governance_verdict",
        )
        .limit(50)
    )
    audit_entries = list(audit_result.scalars().all())

    governance_checks = [
        GovernanceFairnessEntry(
            audit_log_id=entry.id,
            action=entry.action,
            fairness_data=entry.output_payload,
            created_at=entry.created_at,
        )
        for entry in audit_entries
    ]

    has_fairness_data = verdict_fairness_report is not None or len(governance_checks) > 0

    return FairnessAuditResponse(
        case_id=case_id,
        verdict_fairness_report=verdict_fairness_report,
        governance_checks=governance_checks,
        has_fairness_data=has_fairness_data,
    )


# --------------------------------------------------------------------------- #
# US-006: Evidence Dashboard
# --------------------------------------------------------------------------- #


@router.get(
    "/{case_id}/evidence-dashboard",
    operation_id="get_evidence_dashboard",
    summary="Aggregate evidence and contradictions for a case",
    description="Summarise evidence strength, admissibility-flag truthiness, and any "
    "contradictions surfaced via disputed facts or corroboration. Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_evidence_dashboard(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> dict:
    case_result = await db.execute(select(Case).where(Case.id == case_id))
    if case_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    evidence_result = await db.execute(select(Evidence).where(Evidence.case_id == case_id))
    evidence_rows = list(evidence_result.scalars().all())

    fact_result = await db.execute(select(Fact).where(Fact.case_id == case_id))
    fact_rows = list(fact_result.scalars().all())

    strength_counts = {"strong": 0, "medium": 0, "weak": 0, "unrated": 0}
    flag_counts: dict[str, dict[str, int]] = {}
    for ev in evidence_rows:
        key = ev.strength.value if ev.strength else "unrated"
        strength_counts[key] = strength_counts.get(key, 0) + 1
        if ev.admissibility_flags:
            for flag_name, flag_value in ev.admissibility_flags.items():
                bucket = flag_counts.setdefault(flag_name, {"truthy_count": 0, "falsy_count": 0})
                if flag_value:
                    bucket["truthy_count"] += 1
                else:
                    bucket["falsy_count"] += 1

    strength_summary = {**strength_counts, "total": len(evidence_rows)}
    admissibility_flags_summary = [
        {"flag": name, "truthy_count": c["truthy_count"], "falsy_count": c["falsy_count"]}
        for name, c in sorted(flag_counts.items())
    ]

    contradictions = []
    for fact in fact_rows:
        is_disputed = fact.status == FactStatus.disputed
        corroboration = fact.corroboration if isinstance(fact.corroboration, dict) else {}
        has_contradicts = "contradicts" in corroboration
        if is_disputed or has_contradicts:
            contradictions.append(
                {
                    "fact_id": str(fact.id),
                    "description": fact.description,
                    "status": fact.status.value if fact.status else None,
                    "dispute_reason": corroboration.get("dispute_reason"),
                    "contradicts": corroboration.get("contradicts"),
                }
            )

    return {
        "case_id": str(case_id),
        "strength_summary": strength_summary,
        "admissibility_flags_summary": admissibility_flags_summary,
        "contradictions": contradictions,
        "total_evidence_count": len(evidence_rows),
        "total_fact_count": len(fact_rows),
        "has_evidence_data": bool(evidence_rows or fact_rows),
    }


# --------------------------------------------------------------------------- #
# US-003: Jurisdiction Validation
# --------------------------------------------------------------------------- #


def _extract_jurisdiction_issues(payload: dict | None) -> list[str]:
    """Pull the issues list out of a case_processing audit payload."""
    if not payload:
        return []
    if isinstance(payload.get("jurisdiction_issues"), list):
        return list(payload["jurisdiction_issues"])
    metadata = payload.get("case_metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("jurisdiction_issues"), list):
        return list(metadata["jurisdiction_issues"])
    return []


@router.get(
    "/{case_id}/jurisdiction",
    operation_id="get_jurisdiction_validation",
    summary="Show jurisdiction validation for a case",
    description="Combine the case-level jurisdiction_valid flag with the most recent "
    "case_processing audit payload so the judge can see both the verdict and the reasoning. "
    "Requires judge role.",
    responses={
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Case not found"},
    },
)
async def get_jurisdiction_validation(
    case_id: UUID,
    db: DBSession,
    current_user: User = require_role(UserRole.judge),
) -> dict:
    case_result = await db.execute(select(Case).where(Case.id == case_id))
    case = case_result.scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    audit_result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.case_id == case_id,
            AuditLog.agent_name == "case_processing",
            AuditLog.action == "agent_response",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    )
    audit = audit_result.scalar_one_or_none()
    audit_payload = audit.output_payload if audit else None
    audit_log_id = str(audit.id) if audit else None

    return {
        "case_id": str(case_id),
        "jurisdiction_valid": case.jurisdiction_valid,
        "jurisdiction_issues": _extract_jurisdiction_issues(audit_payload),
        "audit_payload": audit_payload,
        "audit_log_id": audit_log_id,
        "has_validation_data": case.jurisdiction_valid is not None or audit is not None,
    }
