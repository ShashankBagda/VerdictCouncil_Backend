"""Pydantic schemas for judge-facing endpoints (US-009, US-010, US-023)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.case import EvidenceStrength, EvidenceType, FactConfidence, FactStatus

# ---------------------------------------------------------------------------
# US-009: Flag Disputed Facts
# ---------------------------------------------------------------------------


class DisputeFactRequest(BaseModel):
    reason: str = Field(
        ..., description="Reason for disputing the fact", min_length=1, max_length=1000
    )


class DisputeFactResponse(BaseModel):
    fact_id: UUID
    case_id: UUID
    status: FactStatus
    confidence: FactConfidence
    reason: str
    message: str


# ---------------------------------------------------------------------------
# US-010: Evidence Gaps
# ---------------------------------------------------------------------------


class WeakEvidenceItem(BaseModel):
    id: UUID
    evidence_type: EvidenceType
    strength: EvidenceStrength | None = None
    admissibility_flags: dict[str, Any] | None = None
    linked_claims: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class UncorroboratedFact(BaseModel):
    id: UUID
    description: str
    confidence: FactConfidence | None = None
    status: FactStatus | None = None

    model_config = {"from_attributes": True}


class EvidenceGapsResponse(BaseModel):
    case_id: UUID
    weak_evidence: list[WeakEvidenceItem]
    uncorroborated_facts: list[UncorroboratedFact]
    total_evidence_count: int
    total_fact_count: int
    gap_summary: str


# ---------------------------------------------------------------------------
# US-023: Fairness & Bias Audit Display
# ---------------------------------------------------------------------------


class GovernanceFairnessEntry(BaseModel):
    audit_log_id: UUID
    action: str
    fairness_data: dict[str, Any] | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class FairnessAuditCheck(BaseModel):
    label: str = Field(..., description="Human-readable description of the check")
    passed: bool = Field(..., description="Whether this check passed")
    severity: str | None = Field(
        None, description="Severity bucket parsed from the issue prefix (CRITICAL/MAJOR/MINOR)"
    )


class FairnessAuditResponse(BaseModel):
    case_id: UUID
    has_fairness_data: bool = Field(
        ..., description="Whether any fairness data exists for this case"
    )
    # Legacy `hearing-governance` AuditLog projection — kept for back-compat
    # with older cases produced before the LangGraph topology cutover.
    governance_checks: list[GovernanceFairnessEntry] = Field(
        default_factory=list,
        description="Fairness check outputs from legacy hearing-governance audit logs",
    )
    # New-topology fields, sourced from the gate-4 interrupt event payload
    # (`audit_summary.fairness_check` + `audit_summary.recommend_send_back`).
    # The audit phase doesn't write its own AuditLog row (no tool calls), so
    # the persisted SSE interrupt is the durable record we read from.
    checks: list[FairnessAuditCheck] = Field(
        default_factory=list,
        description="One entry per auditor-flagged issue (passed=false) plus a "
        "synthetic 'audit passed' entry when no critical issues were found.",
    )
    verdict: str | None = Field(
        None, description="Auditor's recommendation summary (first recommendation, "
        "or send-back reason when present)."
    )
    overall_score: int | None = Field(
        None, description="100 when audit_passed and no critical issues, otherwise 0."
    )
    fairness_check: dict[str, Any] | None = Field(
        None, description="Raw FairnessCheck payload from the audit phase."
    )
    recommend_send_back: dict[str, Any] | None = Field(
        None, description="Auditor's send-back recommendation (target phase + reason)."
    )
