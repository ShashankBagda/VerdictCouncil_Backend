"""Case request/response schemas aligned to the user-story contract."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.models.case import (
    ArgumentSide,
    CaseDomain,
    CaseStatus,
    EvidenceStrength,
    EvidenceType,
    FactConfidence,
    FactStatus,
    PartyRole,
    PrecedentSource,
    RecommendationType,
)

KNOWN_TRAFFIC_OFFENCE_CODES = {
    "RTA-S64",
    "RTA-S65",
    "RTA-S67",
    "RTA-S69",
}


class CasePartyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    role: PartyRole = Field(..., description="Role in the matter")
    contact_info: dict[str, Any] | None = Field(
        default=None, description="Optional contact information"
    )


class CaseCreateRequest(BaseModel):
    """Create a new case for processing."""

    domain: CaseDomain = Field(
        ..., description="Legal domain of the case", examples=["small_claims"]
    )
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    filed_date: date | None = Field(default=None, description="Filed date in the tribunal")
    parties: list[CasePartyCreateRequest] = Field(default_factory=list)
    claim_amount: float | None = Field(default=None, ge=0)
    consent_to_higher_claim_limit: bool = Field(default=False)
    offence_code: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_domain_requirements(self) -> CaseCreateRequest:
        if len(self.parties) < 2:
            raise ValueError("At least two parties are required for case intake.")

        if self.domain == CaseDomain.small_claims:
            if self.claim_amount is None:
                raise ValueError("SCT cases require claim_amount.")
            if self.claim_amount > 30000:
                raise ValueError("SCT claim_amount exceeds the $30,000 jurisdiction limit.")
            if self.claim_amount > 20000 and not self.consent_to_higher_claim_limit:
                raise ValueError(
                    "SCT claim_amount above $20,000 requires consent_to_higher_claim_limit."
                )

        if self.domain == CaseDomain.traffic_violation:
            if not self.offence_code:
                raise ValueError("Traffic cases require offence_code.")
            if self.offence_code not in KNOWN_TRAFFIC_OFFENCE_CODES:
                raise ValueError(f"Unknown traffic offence code: {self.offence_code}")

        return self


class CaseJurisdictionResponse(BaseModel):
    status: str = Field(..., description="pass, fail, warning, or pending")
    valid: bool | None = Field(None, description="Whether the case passed jurisdiction checks")
    reasons: list[str] = Field(default_factory=list)


class CaseProgressResponse(BaseModel):
    pipeline_progress_percent: int = Field(..., ge=0, le=100)
    current_agent: str | None = Field(None)


class CaseDecisionRecordResponse(BaseModel):
    decision_type: str = Field(..., description="accept, modify, reject, or amendment type")
    reason: str | None = Field(None)
    final_order: str | None = Field(None)
    recorded_at: datetime | None = Field(None)
    recorded_by: str | None = Field(None)


class PartyResponse(BaseModel):
    id: UUID
    name: str = Field(..., description="Party name")
    role: PartyRole = Field(..., description="Role in the case")
    contact_info: dict[str, Any] | None = Field(None, description="Contact details")

    model_config = {"from_attributes": True}


class DocumentResponse(BaseModel):
    id: UUID
    openai_file_id: str | None = None
    filename: str = Field(..., description="Original filename")
    file_type: str | None = Field(None, description="MIME type or extension")
    uploaded_at: datetime | None = Field(None, description="Upload timestamp")

    model_config = {"from_attributes": True}


class EvidenceResponse(BaseModel):
    id: UUID
    evidence_type: EvidenceType = Field(..., description="Type of evidence")
    strength: EvidenceStrength | None = Field(None, description="Assessed strength")
    admissibility_flags: dict[str, Any] | None = Field(None, description="Admissibility flags")
    linked_claims: dict[str, Any] | None = Field(
        None, description="Linked claims, contradictions, or corroboration metadata"
    )

    model_config = {"from_attributes": True}


class FactResponse(BaseModel):
    id: UUID
    description: str = Field(..., description="Fact description")
    event_date: date | None = Field(None, description="Date of the event")
    event_time: time | None = Field(None, description="Time of the event")
    confidence: FactConfidence | None = Field(None, description="Confidence level")
    status: FactStatus | None = Field(None, description="Agreed or disputed")
    corroboration: dict[str, Any] | None = Field(
        None, description="Corroboration or dispute metadata"
    )
    source_document_id: UUID | None = Field(None, description="Originating document id")

    model_config = {"from_attributes": True}


class WitnessResponse(BaseModel):
    id: UUID
    name: str = Field(..., description="Witness name")
    role: str | None = Field(None, description="Witness role")
    credibility_score: int | None = Field(None, description="Credibility score (0-100)")
    bias_indicators: dict[str, Any] | None = Field(
        None, description="Bias indicators and credibility factors"
    )
    simulated_testimony: str | None = Field(
        None, description="Traffic-only simulated testimony summary"
    )

    model_config = {"from_attributes": True}


class LegalRuleResponse(BaseModel):
    id: UUID
    statute_name: str = Field(..., description="Name of the statute")
    section: str | None = Field(None, description="Section reference")
    verbatim_text: str | None = Field(None, description="Verbatim statutory text")
    relevance_score: float | None = Field(None, description="Relevance score (0-1)")
    application: str | None = Field(None, description="Narrative application to the case facts")

    model_config = {"from_attributes": True}


class PrecedentResponse(BaseModel):
    id: UUID
    citation: str = Field(..., description="Case citation")
    court: str | None = Field(None, description="Court name")
    outcome: str | None = Field(None, description="Case outcome")
    reasoning_summary: str | None = Field(None, description="Key reasoning summary")
    similarity_score: float | None = Field(None, description="Similarity score (0-1)")
    distinguishing_factors: str | None = Field(
        None, description="How the precedent differs from the current case"
    )
    source: PrecedentSource | None = Field(None, description="curated or live_search")
    url: str | None = Field(None, description="Source URL when available")

    model_config = {"from_attributes": True}


class ArgumentResponse(BaseModel):
    id: UUID
    side: ArgumentSide = Field(..., description="Which side the argument supports")
    legal_basis: str = Field(..., description="Legal basis for the argument")
    supporting_evidence: dict[str, Any] | None = Field(
        None, description="Supporting evidence chain"
    )
    weaknesses: str | None = Field(None, description="Identified weaknesses")
    suggested_questions: dict[str, Any] | None = Field(
        None, description="Suggested judicial questions"
    )

    model_config = {"from_attributes": True}


class DeliberationResponse(BaseModel):
    id: UUID
    reasoning_chain: dict[str, Any] | None = Field(None, description="Structured reasoning chain")
    preliminary_conclusion: str | None = Field(None, description="Preliminary conclusion")
    uncertainty_flags: dict[str, Any] | None = Field(
        None, description="Uncertainty flags and pivot factors"
    )
    confidence_score: int | None = Field(None, description="Confidence score (0-100)")

    model_config = {"from_attributes": True}


class VerdictResponse(BaseModel):
    id: UUID
    recommendation_type: RecommendationType = Field(..., description="Type of recommendation")
    recommended_outcome: str = Field(..., description="Recommended outcome text")
    sentence: dict[str, Any] | None = Field(None, description="Traffic sentence details")
    confidence_score: int | None = Field(None, description="Confidence score (0-100)")
    alternative_outcomes: dict[str, Any] | None = Field(
        None, description="Alternative outcomes and pivot factors"
    )
    fairness_report: dict[str, Any] | None = Field(
        None, description="Structured fairness and bias audit"
    )
    amendment_of: UUID | None = Field(None, description="Previous verdict in the amendment chain")
    amendment_reason: str | None = Field(None, description="Reason for the amendment")
    amended_by: UUID | None = Field(None, description="Judge who amended the verdict")

    model_config = {"from_attributes": True}


class AuditLogSummary(BaseModel):
    """Lightweight audit log entry for case detail view."""

    id: UUID
    agent_name: str = Field(..., description="Name of the agent that performed the action")
    action: str = Field(..., description="Action performed")
    created_at: datetime | None = Field(None, description="Timestamp")
    solace_message_id: str | None = Field(None, description="Broker message id")

    model_config = {"from_attributes": True}


class CaseResponse(BaseModel):
    """Summary response for a case."""

    id: UUID = Field(..., description="Case ID")
    case_id: UUID = Field(..., description="Case ID duplicate for client compatibility")
    title: str | None = Field(None, description="Case title")
    description: str | None = Field(None, description="Case summary or description")
    summary_snippet: str | None = Field(None, description="Short search-result summary")
    domain: CaseDomain = Field(..., description="Legal domain")
    status: CaseStatus = Field(..., description="Current case status")
    status_group: str = Field(..., description="High-level status bucket for filtering")
    jurisdiction: CaseJurisdictionResponse = Field(..., description="Jurisdiction summary")
    complexity: str | None = Field(None, description="Case complexity level")
    route: str | None = Field(None, description="Processing route")
    created_by: UUID = Field(..., description="ID of the user who created the case")
    created_at: datetime | None = Field(None, description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")
    filed_date: date | None = Field(None, description="Filed date")
    claim_amount: float | None = Field(None, description="SCT claim amount")
    consent_to_higher_claim_limit: bool = Field(False)
    offence_code: str | None = Field(None, description="Traffic offence code")
    parties: list[PartyResponse] = Field(default_factory=list, description="Case parties")
    party_names: list[str] = Field(default_factory=list, description="Flattened party names")
    claimant_name: str | None = Field(None)
    respondent_name: str | None = Field(None)
    prosecution_name: str | None = Field(None)
    accused_name: str | None = Field(None)
    document_count: int = Field(0, description="Number of documents attached")
    pipeline_progress: CaseProgressResponse = Field(..., description="Pipeline progress summary")
    outcome_summary: str | None = Field(None, description="Latest recommendation or decision")
    escalation_reason: str | None = Field(None, description="Why the case was escalated")
    reopen_state: str | None = Field(None, description="Pending or latest reopen-request state")
    amendment_state: str | None = Field(None, description="Current amendment status")
    latest_decision: CaseDecisionRecordResponse | None = Field(
        None, description="Most recent judge decision record"
    )


class CaseListResponse(BaseModel):
    """Paginated list of cases."""

    items: list[CaseResponse] = Field(..., description="List of cases")
    total: int = Field(..., description="Total number of matching cases", examples=[42])
    page: int = Field(..., description="Current page number", examples=[1])
    per_page: int = Field(..., description="Items per page", examples=[20])


class CaseDetailResponse(CaseResponse):
    """Full case with all related entities."""

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
    decision_history: list[CaseDecisionRecordResponse] = Field(
        default_factory=list, description="Recorded judge decisions and amendments"
    )
    audit_logs: list[AuditLogSummary] = Field(
        default_factory=list, description="Audit trail entries"
    )
