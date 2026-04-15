"""Structured output schemas for each pipeline agent.

Each agent has a Pydantic model defining the fields it writes to CaseState.
Governance-verdict uses OpenAI's strict JSON schema mode (fully specified).
Other agents use json_object mode with post-parse Pydantic validation because
their outputs contain variable-structure dicts that can't be fully specified
for strict mode (which requires additionalProperties: false everywhere).
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent 9: governance-verdict (strict mode — fully specified)
# ---------------------------------------------------------------------------

class AlternativeOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    reasoning: str


class FairnessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    critical_issues_found: bool
    audit_passed: bool
    issues: list[str]
    recommendations: list[str]


class VerdictRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_type: str
    recommended_outcome: str
    confidence_score: int
    reasoning: str
    alternative_outcomes: list[AlternativeOutcome]


class GovernanceVerdictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fairness_check: FairnessCheck
    verdict_recommendation: VerdictRecommendation
    status: str


# ---------------------------------------------------------------------------
# Validation-only models (used after parsing, NOT for strict mode)
# These validate agent outputs but allow extra/variable fields via dict[str, Any].
# ---------------------------------------------------------------------------

class CaseProcessingOutput(BaseModel):
    case_id: str
    run_id: str
    domain: str | None = None
    status: str = "pending"
    parties: list[dict[str, Any]] = Field(default_factory=list)
    case_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_documents: list[dict[str, Any]] = Field(default_factory=list)


class ComplexityRoutingOutput(BaseModel):
    status: str = "processing"
    case_metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    evidence_type: str
    strength: str
    description: str
    source_ref: str | None = None
    admissibility_flags: dict[str, Any] | None = None
    linked_claims: list[str] = Field(default_factory=list)


class EvidenceAnalysisOutput(BaseModel):
    evidence_analysis: dict[str, Any]


class ExtractedFactItem(BaseModel):
    description: str
    date: str | None = None
    confidence: str = "medium"
    status: str = "agreed"
    source_refs: list[str] = Field(default_factory=list)
    corroboration: dict[str, Any] | None = None


class FactReconstructionOutput(BaseModel):
    extracted_facts: dict[str, Any]


class WitnessAnalysisOutput(BaseModel):
    witnesses: dict[str, Any]


class LegalKnowledgeOutput(BaseModel):
    legal_rules: list[dict[str, Any]] = Field(default_factory=list)
    precedents: list[dict[str, Any]] = Field(default_factory=list)


class ArgumentConstructionOutput(BaseModel):
    arguments: dict[str, Any]


class DeliberationOutput(BaseModel):
    deliberation: dict[str, Any]


# ---------------------------------------------------------------------------
# Agent → Schema mappings
# ---------------------------------------------------------------------------

# Strict mode: only governance-verdict (fully specified, no dict[str, Any])
_STRICT_MODE_SCHEMAS: dict[str, type[BaseModel]] = {
    "governance-verdict": GovernanceVerdictOutput,
}

# Post-parse validation: all agents (allows dict[str, Any] fields)
AGENT_VALIDATION_SCHEMAS: dict[str, type[BaseModel]] = {
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

    Only returns a schema for agents with fully specified models (no
    dict[str, Any] fields). Returns None for others, so the caller
    falls back to json_object mode.
    """
    schema_cls = _STRICT_MODE_SCHEMAS.get(agent_name)
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


def validate_agent_output(agent_name: str, output: dict[str, Any]) -> bool:
    """Validate parsed agent output against its Pydantic model.

    Returns True if valid. Logs warnings on validation failure but does
    not raise — the pipeline continues with the raw parsed output.
    """
    schema_cls = AGENT_VALIDATION_SCHEMAS.get(agent_name)
    if schema_cls is None:
        return True

    try:
        schema_cls.model_validate(output)
        return True
    except Exception as exc:
        logger.warning("Agent '%s' output failed validation: %s", agent_name, exc)
        return False
