"""Structured output schemas for each pipeline agent.

Each agent has a Pydantic model defining the fields it writes to CaseState.
These are converted to OpenAI's strict JSON schema format for
response_format={"type": "json_schema", ...}.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Agent 1: case-processing
# ---------------------------------------------------------------------------

class CaseProcessingOutput(BaseModel):
    case_id: str
    run_id: str
    domain: str | None = None
    status: str = "pending"
    parties: list[dict[str, Any]] = Field(default_factory=list)
    case_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_documents: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent 2: complexity-routing
# ---------------------------------------------------------------------------

class ComplexityRoutingOutput(BaseModel):
    status: str = "processing"
    case_metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent 3: evidence-analysis
# ---------------------------------------------------------------------------

class EvidenceItem(BaseModel):
    evidence_type: str
    strength: str
    description: str
    source_ref: str | None = None
    admissibility_flags: dict[str, Any] | None = None
    linked_claims: list[str] = Field(default_factory=list)


class EvidenceAnalysisOutput(BaseModel):
    evidence_analysis: dict[str, Any]


# ---------------------------------------------------------------------------
# Agent 4: fact-reconstruction
# ---------------------------------------------------------------------------

class ExtractedFactItem(BaseModel):
    description: str
    date: str | None = None
    confidence: str = "medium"
    status: str = "agreed"
    source_refs: list[str] = Field(default_factory=list)
    corroboration: dict[str, Any] | None = None


class FactReconstructionOutput(BaseModel):
    extracted_facts: dict[str, Any]


# ---------------------------------------------------------------------------
# Agent 5: witness-analysis
# ---------------------------------------------------------------------------

class WitnessAnalysisOutput(BaseModel):
    witnesses: dict[str, Any]


# ---------------------------------------------------------------------------
# Agent 6: legal-knowledge
# ---------------------------------------------------------------------------

class LegalKnowledgeOutput(BaseModel):
    legal_rules: list[dict[str, Any]] = Field(default_factory=list)
    precedents: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent 7: argument-construction
# ---------------------------------------------------------------------------

class ArgumentConstructionOutput(BaseModel):
    arguments: dict[str, Any]


# ---------------------------------------------------------------------------
# Agent 8: deliberation
# ---------------------------------------------------------------------------

class DeliberationOutput(BaseModel):
    deliberation: dict[str, Any]


# ---------------------------------------------------------------------------
# Agent 9: governance-verdict
# ---------------------------------------------------------------------------

class FairnessCheck(BaseModel):
    critical_issues_found: bool = False
    audit_passed: bool = True
    issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class VerdictRecommendation(BaseModel):
    recommendation_type: str
    recommended_outcome: str
    confidence_score: int
    reasoning: str
    alternative_outcomes: list[dict[str, Any]] = Field(default_factory=list)


class GovernanceVerdictOutput(BaseModel):
    fairness_check: dict[str, Any]
    verdict_recommendation: dict[str, Any]
    status: str = "ready_for_review"


# ---------------------------------------------------------------------------
# Agent → Schema mapping
# ---------------------------------------------------------------------------

AGENT_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "case-processing": CaseProcessingOutput,
    "complexity-routing": ComplexityRoutingOutput,
    "evidence-analysis": EvidenceAnalysisOutput,
    "fact-reconstruction": FactReconstructionOutput,
    "witness-analysis": WitnessAnalysisOutput,
    "legal-knowledge": LegalKnowledgeOutput,
    "argument-construction": ArgumentConstructionOutput,
    "deliberation": DeliberationOutput,
    "governance-verdict": GovernanceVerdictOutput,
}


def get_strict_json_schema(agent_name: str) -> dict[str, Any] | None:
    """Return OpenAI strict JSON schema response_format for an agent.

    Returns None if no schema is defined (caller should fall back to
    json_object mode).
    """
    schema_cls = AGENT_OUTPUT_SCHEMAS.get(agent_name)
    if schema_cls is None:
        return None

    json_schema = schema_cls.model_json_schema()

    return {
        "type": "json_schema",
        "json_schema": {
            "name": f"{agent_name.replace('-', '_')}_output",
            "strict": True,
            "schema": json_schema,
        },
    }
