"""Phase output Pydantic schemas for the 6-phase topology.

Lifted verbatim from `tasks/schema-target-2026-04-25.md` §2 (the Sprint 0
canonical target). The legacy 9-agent schemas + `FIELD_OWNERSHIP`
allowlist were deleted in 1.A1.SEC3; these schemas are now the single
source of truth for what fields each phase owns.

Every model uses `extra="forbid"` per Sprint 0.5 §5 D-4. AuditOutput
additionally sets `strict=True` (the only phase using OpenAI strict JSON
schema; other phases use ToolStrategy(Schema) per source-driven audit
F-8).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, conlist

from src.shared.confidence import ConfidenceLevel

# ---------------------------------------------------------------------------
# Enums used across multiple phase schemas
# ---------------------------------------------------------------------------

CaseDomain = Literal["small_claims", "traffic_violation"]
ComplexityLevel = Literal["simple", "moderate", "complex"]
RouteDecision = Literal["gate2", "escalate", "halt"]
FactStatus = Literal["agreed", "disputed", "contradicted"]
EvidenceStrength = Literal["weak", "moderate", "strong"]
EvidenceType = Literal["document", "testimony", "physical", "digital", "other"]
SuppressionReason = Literal[
    "no_source_match", "low_score", "expired_statute", "out_of_jurisdiction"
]
RerunTargetPhase = Literal["intake", "research", "synthesis"]
CaseStatus = Literal[
    "draft",
    "extracting",
    "awaiting_intake_confirmation",
    "pending",
    "processing",
    "ready_for_review",
    "escalated",
    "closed",
    "failed",
    "failed_retryable",
    "awaiting_review_gate1",
    "awaiting_review_gate2",
    "awaiting_review_gate3",
    "awaiting_review_gate4",
]


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class CredibilityScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(min_length=1)
    span: list[int] | None = Field(default=None, min_length=2, max_length=2)
    exhibit_id: str | None = None


class Party(BaseModel):
    model_config = ConfigDict(extra="forbid")
    party_id: str
    role: Literal["claimant", "respondent", "witness", "counsel", "other"]
    name: str
    contact: str | None = None


class RawDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str
    doc_type: Literal["complaint", "evidence", "pleading", "correspondence", "other"]
    filename: str
    content_hash: str
    ingested_at: datetime


class CaseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jurisdiction: str
    claim_amount: float | None = Field(default=None, ge=0.0)
    filed_at: date | None = None
    offence_code: str | None = None


class RoutingFactor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor: str
    # Weighted contribution to the routing decision. The intake prompt's
    # Phase 4b applies dimension multipliers up to ×3, so a normalised
    # 0..1 cap was wrong — relax to a non-negative float and let the
    # rationale string carry the qualitative reading.
    weight: float = Field(ge=0.0)
    rationale: str


VulnerabilityType = Literal[
    "SELF_REPRESENTED",
    "ELDERLY",
    "LANGUAGE_BARRIER",
    "COGNITIVE_CONCERN",
    "FINANCIAL_VULNERABILITY",
    "POWER_IMBALANCE",
]


class VulnerabilityAssessment(BaseModel):
    """Per-party vulnerability assessment.

    The intake prompt's Phase 4c requires one entry per named party so
    downstream nodes (gate-1 review, hearing-governance safeguards) can
    key per-party safeguards (TRIGGER_9 asymmetric representation, plain-
    language summaries for SELF_REPRESENTED, procedural accommodations
    for ELDERLY/COGNITIVE). Empty `vulnerability_types` means the party
    was assessed and found not vulnerable.
    """

    model_config = ConfigDict(extra="forbid")
    party_name: str
    vulnerability_types: list[VulnerabilityType] = Field(default_factory=list)
    safeguards_recommended: list[str] = Field(default_factory=list)
    notes: str | None = None


class RoutingDecision(BaseModel):
    """Replaces the `_COMPLEXITY_ROUTING_METADATA_FIELDS` workaround
    (validation.py:5-15; Sprint 0.2 §1.2 F-5).
    """

    model_config = ConfigDict(extra="forbid")
    complexity: ComplexityLevel
    complexity_score: int = Field(ge=0, le=100)
    route: RouteDecision
    routing_factors: conlist(RoutingFactor, min_length=0)
    vulnerability_assessment: conlist(VulnerabilityAssessment, min_length=0)
    escalation_reason: str | None = None
    pipeline_halt: bool = False


# ---------------------------------------------------------------------------
# Intake phase
# ---------------------------------------------------------------------------


class IntakeOutput(BaseModel):
    """Single lightweight-model phase — replaces case-processing +
    complexity-routing (Sprint 0.4 §2.1).
    """

    model_config = ConfigDict(extra="forbid")

    domain: CaseDomain
    parties: conlist(Party, min_length=0)
    case_metadata: CaseMetadata
    raw_documents: conlist(RawDocument, min_length=0)
    routing_decision: RoutingDecision


# ---------------------------------------------------------------------------
# Research phase — four typed sub-outputs
# ---------------------------------------------------------------------------


class EvidenceItem(BaseModel):
    """Item-level evidence record (Sprint 0.2 F-2)."""

    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    evidence_type: EvidenceType
    strength: EvidenceStrength
    description: str
    source_ref: SourceRef | None = None
    admissibility_flags: dict[str, bool] = Field(default_factory=dict)
    linked_claims: list[str] = Field(default_factory=list)


class EvidenceResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_items: conlist(EvidenceItem, min_length=0)
    credibility_scores: dict[str, CredibilityScore] = Field(default_factory=dict)


class ExtractedFactItem(BaseModel):
    """Item-level fact record (Sprint 0.2 F-2). Sprint 0.5 §5 D-3:
    `confidence` is the `ConfidenceLevel` enum.

    Field renamed from `date` (per schema-target spec) to `event_date`
    so it doesn't shadow the imported `date` type during Pydantic's
    annotation resolution under `from __future__ import annotations`.
    """

    model_config = ConfigDict(extra="forbid")
    fact_id: str
    description: str
    event_date: date | None = None
    confidence: ConfidenceLevel
    status: FactStatus
    source_refs: list[SourceRef] = Field(default_factory=list)
    corroboration: dict[str, str] = Field(default_factory=dict)


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_date: date
    description: str
    source_refs: list[SourceRef] = Field(default_factory=list)


class FactsResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facts: conlist(ExtractedFactItem, min_length=0)
    timeline: conlist(TimelineEvent, min_length=0)


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement_id: str
    text: str
    made_at: datetime | None = None
    source_ref: SourceRef | None = None


class Witness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    witness_id: str
    name: str
    role: Literal["eyewitness", "expert", "character", "other"]
    statements: conlist(Statement, min_length=0)
    credibility: CredibilityScore


class WitnessesResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    witnesses: conlist(Witness, min_length=0)
    credibility: dict[str, CredibilityScore] = Field(default_factory=dict)


class LegalRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    jurisdiction: str
    citation: str
    text: str
    applicability: str
    # Sprint 3 3.B.4 — citation provenance: source_ids backing this rule.
    # Populated by 3.B.5 from the run's tool-artifact chain. Empty default
    # keeps legacy outputs parseable during the rollout.
    supporting_sources: list[str] = Field(default_factory=list)


class Precedent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_name: str
    citation: str
    jurisdiction: str
    holding: str
    relevance_rationale: str
    supporting_sources: list[str] = Field(default_factory=list)


class PrecedentProvenance(BaseModel):
    """Folds the `common.py:355-357` side-channel into the schema
    (Sprint 0.2 F-4 / F-8)."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["pair", "vector_store", "degraded"]
    query: str
    retrieved_at: datetime
    degraded_reason: str | None = None


class LegalElement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    element: str
    satisfied: bool
    rationale: str


class SuppressedCitation(BaseModel):
    """Mirrors the `suppressed_citation` table DDL (migration 0025)."""

    model_config = ConfigDict(extra="forbid")
    citation_text: str
    reason: SuppressionReason


class LawResearch(BaseModel):
    """Closes the 3-field schema gap (Sprint 0.2 §1.6 — ownership lists 5,
    legacy schema declared 2)."""

    model_config = ConfigDict(extra="forbid")
    legal_rules: conlist(LegalRule, min_length=0)
    precedents: conlist(Precedent, min_length=0)
    precedent_source_metadata: PrecedentProvenance
    legal_elements_checklist: conlist(LegalElement, min_length=0)
    suppressed_citations: conlist(SuppressedCitation, min_length=0)


# ---------------------------------------------------------------------------
# ResearchPart wrapper + ResearchOutput join (used by 1.A1.5)
# ---------------------------------------------------------------------------


class ResearchPart(BaseModel):
    """Wrapper emitted by each research subagent.

    Written into `research_parts: Annotated[dict[str, ResearchPart], merge_dict]`
    keyed by scope (Sprint 0.5 §5 D-2; SA F-2 dict-keyed accumulator).
    """

    model_config = ConfigDict(extra="forbid")
    scope: Literal["evidence", "facts", "witnesses", "law"]
    evidence: EvidenceResearch | None = None
    facts: FactsResearch | None = None
    witnesses: WitnessesResearch | None = None
    law: LawResearch | None = None


class ResearchOutput(BaseModel):
    """Produced by `research_join_node` (Sprint 0.4 §4.3).

    `partial=True` is set by `from_parts` when any expected scope is missing.
    The gate2 UI surfaces this to the judge.
    """

    model_config = ConfigDict(extra="forbid")
    evidence: EvidenceResearch | None = None
    facts: FactsResearch | None = None
    witnesses: WitnessesResearch | None = None
    law: LawResearch | None = None
    partial: bool = False

    @classmethod
    def from_parts(cls, parts: dict[str, ResearchPart]) -> ResearchOutput:
        expected = {"evidence", "facts", "witnesses", "law"}
        present = set(parts.keys())
        return cls(
            evidence=parts["evidence"].evidence if "evidence" in parts else None,
            facts=parts["facts"].facts if "facts" in parts else None,
            witnesses=parts["witnesses"].witnesses if "witnesses" in parts else None,
            law=parts["law"].law if "law" in parts else None,
            partial=bool(expected - present),
        )


# ---------------------------------------------------------------------------
# Synthesis phase
# ---------------------------------------------------------------------------


QuestionType = Literal[
    "factual_clarification",
    "evidence_gap",
    "credibility_probe",
    "legal_interpretation",
]


class SuggestedQuestion(BaseModel):
    """One judicial question targeted at a specific argument weakness.

    Emitted by the synthesis agent via the `generate_questions` tool and
    persisted on the parent `Argument` row's JSONB `suggested_questions`
    column so the dossier "Suggested Questions" tab can render them.
    """

    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    rationale: str | None = None
    question_type: QuestionType
    targets_weakness: str | None = None


class Argument(BaseModel):
    """One IRAC argument for a single side, with its weaknesses and probes.

    Replaces the legacy single-`ArgumentPosition` shape so each side can
    carry multiple issues / charges. The persistence layer writes one
    `argument` table row per item in this list.
    """

    model_config = ConfigDict(extra="forbid")
    party: Literal["claimant", "respondent"]
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    legal_basis: str = Field(min_length=1)
    supporting_refs: list[SourceRef] = Field(default_factory=list)
    weaknesses: conlist(str, min_length=1)
    strength_score: int | None = Field(default=None, ge=0, le=100)
    suggested_questions: list[SuggestedQuestion] = Field(default_factory=list)


class ContestedPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    claimant_view: str
    respondent_view: str


class ArgumentSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claimant_arguments: conlist(Argument, min_length=1)
    respondent_arguments: conlist(Argument, min_length=1)
    contested_points: list[ContestedPoint] = Field(default_factory=list)
    counter_arguments: list[str] = Field(default_factory=list)


class ReasoningStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_no: int = Field(ge=1)
    description: str
    supports: list[str] = Field(default_factory=list)


class UncertaintyFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    rationale: str
    severity: ConfidenceLevel


class SynthesisOutput(BaseModel):
    """Replaces argument-construction + hearing-analysis (Sprint 0.4 §2.6).

    `confidence_calc` is NOT a tool in the new topology (Sprint 0.5 §5 D-7) —
    the synthesis node may call `src/utils/confidence_calc.py` post-LLM and
    overwrite `confidence` if the operator opts in. For now the LLM emits the
    enum directly.
    """

    model_config = ConfigDict(extra="forbid")

    arguments: ArgumentSet
    # `preliminary_conclusion` is intentionally nullable: the synthesis
    # prompt forbids the agent from emitting a verdict (Judge's role,
    # auditor enforces). When the agent obeys it sets this to None and
    # the dossier "Hearing Analysis" panel renders the reasoning_chain
    # without a verdict line.
    preliminary_conclusion: str | None = None
    confidence: ConfidenceLevel | None = None
    reasoning_chain: conlist(ReasoningStep, min_length=1)
    uncertainty_flags: list[UncertaintyFlag] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Audit phase (strict mode — only phase using OpenAI strict JSON schema)
# ---------------------------------------------------------------------------


class FairnessCheck(BaseModel):
    """Kept verbatim from `src/shared/case_state.py:30-36`."""

    model_config = ConfigDict(extra="forbid")
    critical_issues_found: bool
    audit_passed: bool
    issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class SendBackRecommendation(BaseModel):
    """Sprint 4 4.A3.14 — auditor's send-back-to-phase recommendation.

    Surfaced in the gate4 review panel as a "Send back to ▼ <phase>"
    dropdown. The judge can act on it via the unified `/respond`
    endpoint with `action="send_back"`, which rewinds the LangGraph
    thread to a checkpoint before the target phase ran.

    `audit` is intentionally excluded from `to_phase` — sending back to
    audit is a rerun-audit, not a rewind. Use `should_rerun=True` with
    `target_phase="audit"` for that.
    """

    model_config = ConfigDict(extra="forbid")

    to_phase: RerunTargetPhase
    reason: str


class AuditOutput(BaseModel):
    """Sprint 0.5 §5 D-9 post-hoc rerun mechanic.

    The auditor sets `should_rerun` + `target_phase` + `reason` and the
    worker reads these fields to call the same rerun endpoint judges use
    (`/cases/{id}/rerun?phase=...`). The resulting `judge_corrections` row
    carries `correction_source='auditor'` (migration 0025 DDL tweak).

    Sprint 4 4.A3.14 adds `recommend_send_back` — an optional structured
    recommendation the gate4 review panel surfaces to the judge. Distinct
    from `should_rerun`: send-back rewinds the thread to a past checkpoint
    (later state stays accessible via `get_state_history` for audit),
    while rerun re-executes the target phase from the current head.

    Strict mode only — Sprint 0.5 §5 D-4: this is the one phase using
    OpenAI strict JSON schema. Other phases use `ToolStrategy(Schema)`
    with `extra="forbid"` (SA F-8).
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    fairness_check: FairnessCheck
    status: CaseStatus
    should_rerun: bool = False
    target_phase: RerunTargetPhase | None = None
    reason: str | None = None
    recommend_send_back: SendBackRecommendation | None = None
