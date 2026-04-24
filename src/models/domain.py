"""Domain and DomainDocument models for per-domain RAG pipeline."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DomainDocumentStatus(str, enum.Enum):
    pending = "pending"
    uploading = "uploading"
    parsed = "parsed"
    indexing = "indexing"
    indexed = "indexed"
    failed = "failed"


class Domain(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "domains"

    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    vector_store_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # Provisioning tracking
    provisioning_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provisioning_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    documents: Mapped[list[DomainDocument]] = relationship(
        back_populates="domain", cascade="all, delete-orphan"
    )


class DomainDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "domain_documents"

    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_domain_document_idempotency"),)

    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    openai_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sanitized_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sanitized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )  # noqa: E501
    status: Mapped[DomainDocumentStatus] = mapped_column(
        String(20), nullable=False, server_default=DomainDocumentStatus.pending.value
    )
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    domain: Mapped[Domain] = relationship(back_populates="documents")
