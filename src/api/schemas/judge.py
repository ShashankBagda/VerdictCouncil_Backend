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


class FairnessAuditResponse(BaseModel):
    case_id: UUID
    verdict_fairness_report: dict[str, Any] | None = Field(
        None, description="Fairness report stored in the verdict record"
    )
    governance_checks: list[GovernanceFairnessEntry] = Field(
        default_factory=list,
        description="Fairness check outputs from governance agent audit logs",
    )
    has_fairness_data: bool = Field(
        ..., description="Whether any fairness data exists for this case"
    )


# ---------------------------------------------------------------------------
# US-003: Jurisdiction Validation Result
# ---------------------------------------------------------------------------


class JurisdictionValidationResponse(BaseModel):
    case_id: UUID
    jurisdiction_valid: bool | None = Field(
        None,
        description="Top-level jurisdiction status from the Case record (set by Agent 1).",
    )
    jurisdiction_issues: list[str] = Field(
        default_factory=list,
        description="Jurisdiction issues extracted from the most recent case-processing "
        "audit log, if any.",
    )
    audit_payload: dict[str, Any] | None = Field(
        None,
        description="Raw output_payload from the most recent case-processing audit log "
        "for this case, when available.",
    )
    audit_log_id: UUID | None = Field(
        None, description="Identifier of the audit log row sourcing this response."
    )
    created_at: datetime | None = Field(None, description="Timestamp of the source audit log row.")
    has_validation_data: bool = Field(
        ...,
        description="True when either jurisdiction_valid is set on the Case or a "
        "case-processing audit log row exists.",
    )


# ---------------------------------------------------------------------------
# US-006: Evidence Analysis Dashboard
# ---------------------------------------------------------------------------


class EvidenceStrengthBreakdown(BaseModel):
    strong: int = 0
    moderate: int = 0
    weak: int = 0
    unrated: int = Field(0, description="Evidence rows with no strength rating set")
    total: int = 0


class AdmissibilityFlagSummary(BaseModel):
    flag: str = Field(..., description="Admissibility flag key (e.g. 'authenticated', 'hearsay')")
    truthy_count: int = Field(
        0, description="Evidence rows where this flag is set to a truthy value"
    )
    falsy_count: int = Field(
        0, description="Evidence rows where this flag is present but set to a falsy value"
    )


class ContradictionItem(BaseModel):
    fact_id: UUID
    description: str
    status: FactStatus | None = None
    confidence: FactConfidence | None = None
    contradiction_notes: dict[str, Any] | None = Field(
        None,
        description="Subset of the fact's corroboration JSONB containing contradiction "
        "signals (e.g. dispute_reason, contradicts).",
    )

    model_config = {"from_attributes": True}


class EvidenceDashboardResponse(BaseModel):
    case_id: UUID
    strength_summary: EvidenceStrengthBreakdown
    admissibility_flags_summary: list[AdmissibilityFlagSummary] = Field(
        default_factory=list,
        description="Per-flag tally across all evidence rows for this case.",
    )
    contradictions: list[ContradictionItem] = Field(
        default_factory=list,
        description="Facts with status=disputed or corroboration entries that flag a "
        "contradiction.",
    )
    total_evidence_count: int = 0
    total_fact_count: int = 0
    has_evidence_data: bool = Field(
        ...,
        description="True when at least one evidence or fact row exists for the case.",
    )
