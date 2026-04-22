from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaseStatusEnum(str, Enum):
    pending = "pending"
    processing = "processing"
    ready_for_review = "ready_for_review"
    decided = "decided"
    rejected = "rejected"
    escalated = "escalated"
    closed = "closed"
    failed = "failed"


class CaseDomainEnum(str, Enum):
    small_claims = "small_claims"
    traffic_violation = "traffic_violation"


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


class Deliberation(BaseModel):
    model_config = ConfigDict(extra="allow")

    preliminary_conclusion: str | None = None
    confidence_score: int | None = None
    reasoning_chain: list[dict[str, Any]] = Field(default_factory=list)
    uncertainty_flags: list[dict[str, Any]] = Field(default_factory=list)


class Witnesses(BaseModel):
    model_config = ConfigDict(extra="allow")

    # Pipeline agent uses "witnesses" key; SAM layer2 uses "statements"
    witnesses: list[Any] = Field(default_factory=list)
    statements: list[Any] = Field(default_factory=list)
    credibility: dict[str, Any] = Field(default_factory=dict)


class AuditEntry(BaseModel):
    agent: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    action: str
    input_payload: dict[str, Any] | None = None
    output_payload: dict[str, Any] | None = None
    system_prompt: str | None = None
    llm_response: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    model: str | None = None
    token_usage: dict[str, Any] | None = None
    solace_message_id: str | None = None


class CaseState(BaseModel):
    # Schema version — incremented when the CaseState shape changes in a way
    # that breaks round-trip with older checkpoints. The reader in
    # `src/db/pipeline_state.py` compares this against CURRENT_SCHEMA_VERSION
    # and fails loud on mismatch rather than silently defaulting.
    schema_version: int = 1

    # Identity & Status
    case_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_run_id: str | None = None
    domain: CaseDomainEnum | None = None
    status: CaseStatusEnum = CaseStatusEnum.pending
    parties: list[dict[str, Any]] = Field(default_factory=list)
    case_metadata: dict[str, Any] = Field(default_factory=dict)

    # Documents (written by Case Processing)
    raw_documents: list[dict[str, Any]] = Field(default_factory=list)

    # Evidence (written by Evidence Analysis)
    evidence_analysis: dict[str, Any] | None = None

    # Facts (written by Fact Reconstruction)
    extracted_facts: dict[str, Any] | None = None

    # Witnesses (written by Witness Analysis)
    witnesses: Witnesses | None = None

    # Law (written by Legal Knowledge)
    legal_rules: list[dict[str, Any]] = Field(default_factory=list)
    precedents: list[dict[str, Any]] = Field(default_factory=list)
    precedent_source_metadata: dict[str, Any] | None = None

    # Arguments (written by Argument Construction)
    arguments: dict[str, Any] | None = None

    # Deliberation (written by Deliberation)
    deliberation: Deliberation | None = None

    # Governance (written by Governance & Verdict)
    fairness_check: FairnessCheck | None = None
    verdict_recommendation: VerdictRecommendation | None = None

    # Judge decision (written externally)
    judge_decision: dict[str, Any] | None = None

    # Audit (append-only)
    audit_log: list[AuditEntry] = Field(default_factory=list)

    model_config = {"populate_by_name": True, "validate_assignment": True}
