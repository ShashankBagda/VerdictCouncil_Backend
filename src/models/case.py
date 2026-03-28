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

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CaseDomain(str, enum.Enum):
    civil = "civil"
    criminal = "criminal"
    family = "family"
    commercial = "commercial"
    administrative = "administrative"


class CaseStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready_for_review = "ready_for_review"
    decided = "decided"
    rejected = "rejected"
    escalated = "escalated"
    closed = "closed"
    failed = "failed"


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


class RecommendationType(str, enum.Enum):
    compensation = "compensation"
    repair = "repair"
    dismiss = "dismiss"
    guilty = "guilty"
    not_guilty = "not_guilty"
    reduced = "reduced"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Case(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cases"

    domain: Mapped[CaseDomain] = mapped_column(Enum(CaseDomain), nullable=False)
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus), nullable=False, server_default=CaseStatus.pending.value
    )
    jurisdiction_valid: Mapped[bool | None] = mapped_column(Boolean)
    complexity: Mapped[CaseComplexity | None] = mapped_column(Enum(CaseComplexity))
    route: Mapped[CaseRoute | None] = mapped_column(Enum(CaseRoute))
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
    deliberations: Mapped[list[Deliberation]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    verdicts: Mapped[list[Verdict]] = relationship(
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


class Deliberation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "deliberations"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    reasoning_chain: Mapped[dict | None] = mapped_column(JSONB)
    preliminary_conclusion: Mapped[str | None] = mapped_column(Text)
    uncertainty_flags: Mapped[dict | None] = mapped_column(JSONB)
    confidence_score: Mapped[int | None] = mapped_column(Integer)

    case: Mapped[Case] = relationship(back_populates="deliberations")


class Verdict(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "verdicts"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    recommendation_type: Mapped[RecommendationType] = mapped_column(
        Enum(RecommendationType), nullable=False
    )
    recommended_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    sentence: Mapped[dict | None] = mapped_column(JSONB)
    confidence_score: Mapped[int | None] = mapped_column(Integer)
    alternative_outcomes: Mapped[dict | None] = mapped_column(JSONB)
    fairness_report: Mapped[dict | None] = mapped_column(JSONB)

    case: Mapped[Case] = relationship(back_populates="verdicts")


# Avoid circular import — import here for relationship resolution
from src.models.audit import AuditLog  # noqa: E402, F811
from src.models.user import User  # noqa: E402, F811
