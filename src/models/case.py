from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.models.domain import Domain

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CaseDomain(str, enum.Enum):
    small_claims = "small_claims"
    traffic_violation = "traffic_violation"


class CaseStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready_for_review = "ready_for_review"
    escalated = "escalated"
    closed = "closed"
    failed = "failed"
    # Set by stuck-case watchdog when a `processing` case has not advanced
    # past its threshold — typically because the broker dropped its in-flight
    # message. The frontend surfaces this as "Pipeline interrupted, retry?"
    failed_retryable = "failed_retryable"
    # Gate pause statuses — set after each gate group completes; judge must
    # approve or re-run before the pipeline advances to the next gate.
    awaiting_review_gate1 = "awaiting_review_gate1"
    awaiting_review_gate2 = "awaiting_review_gate2"
    awaiting_review_gate3 = "awaiting_review_gate3"
    awaiting_review_gate4 = "awaiting_review_gate4"


class CaseComplexity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class CaseRoute(str, enum.Enum):
    proceed_automated = "proceed_automated"
    proceed_with_review = "proceed_with_review"
    escalate_human = "escalate_human"


class PartyRole(str, enum.Enum):
    claimant = "claimant"
    respondent = "respondent"
    accused = "accused"
    prosecution = "prosecution"


class EvidenceType(str, enum.Enum):
    documentary = "documentary"
    testimonial = "testimonial"
    physical = "physical"
    digital = "digital"
    expert = "expert"


class EvidenceStrength(str, enum.Enum):
    strong = "strong"
    medium = "medium"
    weak = "weak"


class FactConfidence(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"
    disputed = "disputed"


class FactStatus(str, enum.Enum):
    agreed = "agreed"
    disputed = "disputed"


class PrecedentSource(str, enum.Enum):
    curated = "curated"
    live_search = "live_search"


class ArgumentSide(str, enum.Enum):
    prosecution = "prosecution"
    defense = "defense"
    claimant = "claimant"
    respondent = "respondent"


class ReopenRequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Case(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cases"

    domain: Mapped[CaseDomain] = mapped_column(Enum(CaseDomain), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    filed_date: Mapped[date | None] = mapped_column(Date)
    claim_amount: Mapped[float | None] = mapped_column(Float)
    consent_to_higher_claim_limit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    offence_code: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus), nullable=False, server_default=CaseStatus.pending.value
    )
    jurisdiction_valid: Mapped[bool | None] = mapped_column(Boolean)
    complexity: Mapped[CaseComplexity | None] = mapped_column(Enum(CaseComplexity))
    route: Mapped[CaseRoute | None] = mapped_column(Enum(CaseRoute))
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id"), nullable=True
    )
    domain_ref: Mapped[Domain | None] = relationship(
        "Domain", foreign_keys=[domain_id], lazy="select"
    )

    # Anchor for What-If rehydration: the run_id of the most recent terminal
    # pipeline run for this case. Written by persist_case_results at the end
    # of a successful run; read by what_if/stability to load the real
    # CaseState from pipeline_checkpoints rather than synthesizing an empty one.
    latest_run_id: Mapped[str | None] = mapped_column(String(36))
    gate_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    judicial_decision: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )

    # Relationships
    created_by_user: Mapped[User] = relationship(back_populates="cases")
    parties: Mapped[list[Party]] = relationship(back_populates="case", cascade="all, delete-orphan")
    documents: Mapped[list[Document]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    facts: Mapped[list[Fact]] = relationship(back_populates="case", cascade="all, delete-orphan")
    witnesses: Mapped[list[Witness]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    legal_rules: Mapped[list[LegalRule]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    precedents: Mapped[list[Precedent]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    arguments: Mapped[list[Argument]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    hearing_analyses: Mapped[list[HearingAnalysis]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    hearing_notes: Mapped[list[HearingNote]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    reopen_requests: Mapped[list[ReopenRequest]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )


class Party(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "parties"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[PartyRole] = mapped_column(Enum(PartyRole), nullable=False)
    contact_info: Mapped[dict | None] = mapped_column(JSONB)

    case: Mapped[Case] = relationship(back_populates="parties")
    witnesses: Mapped[list[Witness]] = relationship(back_populates="party")


class Document(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "documents"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    openai_file_id: Mapped[str | None] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(100))
    pages: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped[Case] = relationship(back_populates="documents")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="document")
    facts: Mapped[list[Fact]] = relationship(back_populates="source_document")


class Evidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "evidence"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id")
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(Enum(EvidenceType), nullable=False)
    strength: Mapped[EvidenceStrength | None] = mapped_column(Enum(EvidenceStrength))
    admissibility_flags: Mapped[dict | None] = mapped_column(JSONB)
    linked_claims: Mapped[dict | None] = mapped_column(JSONB)

    case: Mapped[Case] = relationship(back_populates="evidence")
    document: Mapped[Document | None] = relationship(back_populates="evidence")


class Fact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "facts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    event_date: Mapped[date | None] = mapped_column(Date)
    event_time: Mapped[time | None] = mapped_column(Time)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id")
    )
    confidence: Mapped[FactConfidence | None] = mapped_column(Enum(FactConfidence))
    status: Mapped[FactStatus | None] = mapped_column(Enum(FactStatus))
    corroboration: Mapped[dict | None] = mapped_column(JSONB)

    case: Mapped[Case] = relationship(back_populates="facts")
    source_document: Mapped[Document | None] = relationship(back_populates="facts")


class Witness(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "witnesses"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(255))
    party_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"))
    credibility_score: Mapped[int | None] = mapped_column(Integer)
    bias_indicators: Mapped[dict | None] = mapped_column(JSONB)
    simulated_testimony: Mapped[str | None] = mapped_column(Text)

    case: Mapped[Case] = relationship(back_populates="witnesses")
    party: Mapped[Party | None] = relationship(back_populates="witnesses")


class LegalRule(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "legal_rules"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    statute_name: Mapped[str] = mapped_column(String(255), nullable=False)
    section: Mapped[str | None] = mapped_column(String(255))
    verbatim_text: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[float | None] = mapped_column(Float)
    application: Mapped[str | None] = mapped_column(Text)

    case: Mapped[Case] = relationship(back_populates="legal_rules")


class Precedent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "precedents"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    citation: Mapped[str] = mapped_column(String(255), nullable=False)
    court: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str | None] = mapped_column(String(255))
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    similarity_score: Mapped[float | None] = mapped_column(Float)
    distinguishing_factors: Mapped[str | None] = mapped_column(Text)
    source: Mapped[PrecedentSource | None] = mapped_column(Enum(PrecedentSource))
    url: Mapped[str | None] = mapped_column(String(255))

    case: Mapped[Case] = relationship(back_populates="precedents")


class Argument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "arguments"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[ArgumentSide] = mapped_column(Enum(ArgumentSide), nullable=False)
    legal_basis: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_evidence: Mapped[dict | None] = mapped_column(JSONB)
    weaknesses: Mapped[str | None] = mapped_column(Text)
    suggested_questions: Mapped[dict | None] = mapped_column(JSONB)

    case: Mapped[Case] = relationship(back_populates="arguments")


class HearingAnalysis(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "hearing_analyses"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    reasoning_chain: Mapped[dict | None] = mapped_column(JSONB)
    preliminary_conclusion: Mapped[str | None] = mapped_column(Text)
    uncertainty_flags: Mapped[dict | None] = mapped_column(JSONB)
    confidence_score: Mapped[int | None] = mapped_column(Integer)

    case: Mapped[Case] = relationship(back_populates="hearing_analyses")


class HearingNote(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "hearing_notes"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    judge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    section_reference: Mapped[str | None] = mapped_column(String(255))
    note_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[Case] = relationship(back_populates="hearing_notes")
    judge: Mapped[User] = relationship()


class ReopenRequest(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "reopen_requests"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ReopenRequestStatus] = mapped_column(
        Enum(ReopenRequestStatus), nullable=False, server_default=ReopenRequestStatus.pending.value
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[Case] = relationship(back_populates="reopen_requests")
    requester: Mapped[User] = relationship(foreign_keys=[requested_by])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])


# Avoid circular import — import here for relationship resolution
from src.models.audit import AuditLog  # noqa: E402, F811
from src.models.user import User  # noqa: E402, F811
