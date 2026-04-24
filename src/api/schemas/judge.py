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
    governance_checks: list[GovernanceFairnessEntry] = Field(
        default_factory=list,
        description="Fairness check outputs from governance agent audit logs",
    )
    has_fairness_data: bool = Field(
        ..., description="Whether any fairness data exists for this case"
    )
