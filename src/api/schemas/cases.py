"""Case request/response schemas including CaseDetailResponse with nested models."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from src.models.case import (
    ArgumentSide,
    CaseDomain,
    CaseStatus,
    EvidenceStrength,
    EvidenceType,
    FactConfidence,
    FactStatus,
    PartyRole,
    RecommendationType,
)

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CaseCreateRequest(BaseModel):
    """Create a new case for processing."""

    domain: CaseDomain = Field(
        ..., description="Legal domain of the case", examples=["small_claims"]
    )


# ---------------------------------------------------------------------------
# List/summary response schemas
# ---------------------------------------------------------------------------


class CaseResponse(BaseModel):
    """Summary response for a case."""

    id: UUID = Field(..., description="Case ID")
    domain: CaseDomain = Field(..., description="Legal domain")
    status: CaseStatus = Field(..., description="Current case status")
    jurisdiction_valid: bool | None = Field(None, description="Whether jurisdiction is valid")
    complexity: str | None = Field(None, description="Case complexity level")
    route: str | None = Field(None, description="Processing route")
    created_by: UUID = Field(..., description="ID of the user who created the case")

    model_config = {"from_attributes": True}


class CaseListResponse(BaseModel):
    """Paginated list of cases."""

    items: list[CaseResponse] = Field(..., description="List of cases")
    total: int = Field(..., description="Total number of matching cases", examples=[42])
    page: int = Field(..., description="Current page number", examples=[1])
    per_page: int = Field(..., description="Items per page", examples=[20])


# ---------------------------------------------------------------------------
# Nested detail models (for GET /cases/{case_id})
# Fields match the current cherry-picked projection in the handler.
# ---------------------------------------------------------------------------


class PartyResponse(BaseModel):
    id: UUID
    name: str = Field(..., description="Party name")
    role: PartyRole = Field(..., description="Role in the case")
    contact_info: dict[str, Any] | None = Field(None, description="Contact details")

    model_config = {"from_attributes": True}


class DocumentResponse(BaseModel):
    id: UUID
    filename: str = Field(..., description="Original filename")
    file_type: str | None = Field(None, description="MIME type or extension")
    uploaded_at: datetime | None = Field(None, description="Upload timestamp")

    model_config = {"from_attributes": True}


class EvidenceResponse(BaseModel):
    id: UUID
    evidence_type: EvidenceType = Field(..., description="Type of evidence")
    strength: EvidenceStrength | None = Field(None, description="Assessed strength")
    admissibility_flags: dict[str, Any] | None = Field(None, description="Admissibility flags")

    model_config = {"from_attributes": True}


class FactResponse(BaseModel):
    id: UUID
    description: str = Field(..., description="Fact description")
    event_date: date | None = Field(None, description="Date of the event")
    confidence: FactConfidence | None = Field(None, description="Confidence level")
    status: FactStatus | None = Field(None, description="Agreed or disputed")

    model_config = {"from_attributes": True}


class WitnessResponse(BaseModel):
    id: UUID
    name: str = Field(..., description="Witness name")
    role: str | None = Field(None, description="Witness role")
    credibility_score: int | None = Field(None, description="Credibility score (0-100)")

    model_config = {"from_attributes": True}


class LegalRuleResponse(BaseModel):
    id: UUID
    statute_name: str = Field(..., description="Name of the statute")
    section: str | None = Field(None, description="Section reference")
    relevance_score: float | None = Field(None, description="Relevance score (0-1)")

    model_config = {"from_attributes": True}


class PrecedentResponse(BaseModel):
    id: UUID
    citation: str = Field(..., description="Case citation")
    court: str | None = Field(None, description="Court name")
    outcome: str | None = Field(None, description="Case outcome")
    similarity_score: float | None = Field(None, description="Similarity score (0-1)")

    model_config = {"from_attributes": True}


class ArgumentResponse(BaseModel):
    id: UUID
    side: ArgumentSide = Field(..., description="Which side the argument supports")
    legal_basis: str = Field(..., description="Legal basis for the argument")
    weaknesses: str | None = Field(None, description="Identified weaknesses")

    model_config = {"from_attributes": True}


class DeliberationResponse(BaseModel):
    id: UUID
    preliminary_conclusion: str | None = Field(None, description="Preliminary conclusion")
    confidence_score: int | None = Field(None, description="Confidence score (0-100)")

    model_config = {"from_attributes": True}


class VerdictResponse(BaseModel):
    id: UUID
    recommendation_type: RecommendationType = Field(..., description="Type of recommendation")
    recommended_outcome: str = Field(..., description="Recommended outcome text")
    confidence_score: int | None = Field(None, description="Confidence score (0-100)")

    model_config = {"from_attributes": True}


class AuditLogSummary(BaseModel):
    """Lightweight audit log entry for case detail view."""

    id: UUID
    agent_name: str = Field(..., description="Name of the agent that performed the action")
    action: str = Field(..., description="Action performed")
    created_at: datetime | None = Field(None, description="Timestamp")

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Full case detail response
# ---------------------------------------------------------------------------


class CaseDetailResponse(BaseModel):
    """Full case with all related entities."""

    id: UUID = Field(..., description="Case ID")
    domain: CaseDomain = Field(..., description="Legal domain")
    status: CaseStatus = Field(..., description="Current case status")
    jurisdiction_valid: bool | None = Field(None, description="Whether jurisdiction is valid")
    complexity: str | None = Field(None, description="Case complexity level")
    route: str | None = Field(None, description="Processing route")
    created_by: UUID = Field(..., description="Creator user ID")
    parties: list[PartyResponse] = Field(default_factory=list, description="Case parties")
    documents: list[DocumentResponse] = Field(
        default_factory=list, description="Uploaded documents"
    )
    evidence: list[EvidenceResponse] = Field(default_factory=list, description="Evidence items")
    facts: list[FactResponse] = Field(default_factory=list, description="Reconstructed facts")
    witnesses: list[WitnessResponse] = Field(default_factory=list, description="Witnesses")
    legal_rules: list[LegalRuleResponse] = Field(
        default_factory=list, description="Applicable legal rules"
    )
    precedents: list[PrecedentResponse] = Field(
        default_factory=list, description="Relevant precedents"
    )
    arguments: list[ArgumentResponse] = Field(
        default_factory=list, description="Constructed arguments"
    )
    deliberations: list[DeliberationResponse] = Field(
        default_factory=list, description="AI deliberations"
    )
    verdicts: list[VerdictResponse] = Field(default_factory=list, description="Generated verdicts")
    audit_logs: list[AuditLogSummary] = Field(
        default_factory=list, description="Audit trail entries"
    )

    model_config = {"from_attributes": True}
