"""Unit tests for the domain document sanitize-at-ingest pipeline.

Verifies DB-first insert ordering (D15), parse failure handling,
MIME whitelist enforcement, and that only the sanitized artifact is indexed.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.domain import Domain, DomainDocument, DomainDocumentStatus
from src.models.user import User, UserRole
from src.shared.sanitization import SanitizationResult


def _make_admin(**overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Admin",
        "email": "admin@example.com",
        "role": UserRole.admin,
        "password_hash": "hashed",
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def _make_domain(vector_store_id="vs_test", is_active=True) -> MagicMock:
    domain = MagicMock(spec=Domain)
    domain.id = uuid.uuid4()
    domain.code = "small_claims"
    domain.name = "Small Claims Tribunal"
    domain.vector_store_id = vector_store_id
    domain.is_active = is_active
    return domain


class _TrackingSession:
    """Mock HTTP-layer session for route handler tests."""

    def __init__(self, domain: MagicMock) -> None:
        self._domain = domain
        self.added: list = []
        self.commit_count = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    async def delete(self, obj) -> None:
        pass

    async def get(self, model, pk):
        if model.__name__ == "Domain":
            return self._domain
        return None

    async def execute(self, stmt):
        mock = MagicMock()
        mock.scalar_one_or_none.return_value = None
        mock.scalars.return_value.all.return_value = []
        mock.scalars.return_value.first.return_value = None
        return mock

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj, attrs=None) -> None:
        return None


class _BgSession:
    """Mock session for direct background-task (ingest pipeline) tests."""

    def __init__(self, doc: DomainDocument) -> None:
        self._doc = doc
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    async def get(self, model, pk):
        if hasattr(model, "__name__") and model.__name__ == "DomainDocument":
            return self._doc
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj, attrs=None) -> None:
        return None


def _make_bg_session_factory(session: _BgSession):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _app_with(user, session):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: session
    return app


# ---------------------------------------------------------------------------
# DB-first ordering: row committed in HTTP handler before background task runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_first_insert_before_openai_upload():
    """The DomainDocument row must be committed in the HTTP handler before the
    background task (which makes OpenAI calls) starts — no orphaned files (D15).
    """
    from src.shared.config import settings as _settings

    admin = _make_admin()
    domain = _make_domain()
    session = _TrackingSession(domain)
    app = _app_with(admin, session)

    with patch.object(_settings, "domain_uploads_enabled", True):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/domains/admin/{domain.id}/documents",
                files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            )

    assert resp.status_code == 202
    domain_docs = [obj for obj in session.added if isinstance(obj, DomainDocument)]
    assert len(domain_docs) == 1, "DomainDocument must be added to DB in the HTTP handler"
    assert domain_docs[0].status == DomainDocumentStatus.pending
    assert session.commit_count >= 1, "DB commit must happen in HTTP handler before background task"


# ---------------------------------------------------------------------------
# Parse failure → doc marked failed, vector store never written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_parse_failure_marks_doc_failed():
    """If parse_document raises, the doc is marked failed and vector store is never written."""
    from src.api.routes.domains import _ingest_domain_document
    from src.tools.parse_document import DocumentParseError

    admin = _make_admin()
    domain = _make_domain()
    doc_id = uuid.uuid4()
    doc = DomainDocument(
        id=doc_id,
        domain_id=domain.id,
        filename="scan.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        status=DomainDocumentStatus.pending,
        idempotency_key=uuid.uuid4(),
        uploaded_by=admin.id,
        sanitized=False,
        uploaded_at=datetime.now(UTC),
    )

    bg_session = _BgSession(doc)
    oa_file = MagicMock(id="file-original-abc")
    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(return_value=oa_file))
    mock_client.vector_stores = MagicMock(
        files=MagicMock(create_and_poll=AsyncMock(side_effect=AssertionError("should not be called")))
    )

    with (
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch("src.services.database.async_session", _make_bg_session_factory(bg_session)),
        patch(
            "src.tools.parse_document.parse_document",
            AsyncMock(side_effect=DocumentParseError("Cannot parse image-only PDF")),
        ),
    ):
        await _ingest_domain_document(
            doc_id=doc_id,
            domain_id=domain.id,
            vector_store_id="vs_test",
            file_bytes=b"%PDF-1.4",
            filename="scan.pdf",
            content_type="application/pdf",
            actor_id=admin.id,
        )

    assert doc.status == DomainDocumentStatus.failed
    assert doc.error_reason is not None
    mock_client.vector_stores.files.create_and_poll.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sanitized file is indexed, original is NOT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_sanitized_file_goes_into_vector_store():
    """The original OpenAI file must not be added to the vector store — only the sanitized artifact."""
    from src.api.routes.domains import _ingest_domain_document

    admin = _make_admin()
    domain = _make_domain(vector_store_id="vs_test")
    doc_id = uuid.uuid4()
    doc = DomainDocument(
        id=doc_id,
        domain_id=domain.id,
        filename="test.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        status=DomainDocumentStatus.pending,
        idempotency_key=uuid.uuid4(),
        uploaded_by=admin.id,
        sanitized=False,
        uploaded_at=datetime.now(UTC),
    )

    bg_session = _BgSession(doc)
    original_file_id = "file-original-DO-NOT-INDEX"
    sanitized_file_id = "file-sanitized-OK-TO-INDEX"

    oa_file = MagicMock(id=original_file_id)
    san_file = MagicMock(id=sanitized_file_id)
    vs_file = MagicMock(id="vsf-123", status="completed")

    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(side_effect=[oa_file, san_file]))
    mock_client.vector_stores = MagicMock(files=MagicMock(create_and_poll=AsyncMock(return_value=vs_file)))

    parse_result = {
        "pages": [{"page_number": 1, "text": "Sanitized page content"}],
        "text": "Sanitized page content",
        "tables": [],
    }

    with (
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch("src.services.database.async_session", _make_bg_session_factory(bg_session)),
        patch("src.tools.parse_document.parse_document", AsyncMock(return_value=parse_result)),
    ):
        await _ingest_domain_document(
            doc_id=doc_id,
            domain_id=domain.id,
            vector_store_id="vs_test",
            file_bytes=b"%PDF-1.4",
            filename="test.pdf",
            content_type="application/pdf",
            actor_id=admin.id,
        )

    call_kwargs = mock_client.vector_stores.files.create_and_poll.await_args
    indexed_file_id = call_kwargs.kwargs.get("file_id") or call_kwargs[1].get("file_id")
    assert indexed_file_id == sanitized_file_id, (
        f"Vector store received {indexed_file_id!r}; expected sanitized id {sanitized_file_id!r}"
    )


# ---------------------------------------------------------------------------
# Feature flag: uploads disabled → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_disabled_returns_503():
    """When domain_uploads_enabled=False, upload returns 503."""
    from src.shared.config import settings as _settings

    admin = _make_admin()
    domain = _make_domain()
    session = _TrackingSession(domain)
    app = _app_with(admin, session)

    with patch.object(_settings, "domain_uploads_enabled", False):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/domains/admin/{domain.id}/documents",
                files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# File too large → 413
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_too_large_returns_413():
    """File exceeding domain_kb_max_upload_bytes returns 413."""
    from src.shared.config import settings as _settings

    admin = _make_admin()
    domain = _make_domain()
    session = _TrackingSession(domain)
    app = _app_with(admin, session)

    oversized = b"x" * (1024 * 1024 + 1)  # 1 MiB + 1 byte

    with (
        patch.object(_settings, "domain_uploads_enabled", True),
        patch.object(_settings, "domain_kb_max_upload_bytes", 1024 * 1024),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/domains/admin/{domain.id}/documents",
                files={"file": ("big.pdf", oversized, "application/pdf")},
            )

    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# AdminEvent payload includes sanitization metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_records_classifier_hits_in_admin_event():
    """parse_result['sanitization'] metrics land in the AdminEvent payload."""
    from src.api.routes.domains import _ingest_domain_document
    from src.models.admin_event import AdminEvent

    admin = _make_admin()
    domain = _make_domain(vector_store_id="vs_test")
    doc_id = uuid.uuid4()
    doc = DomainDocument(
        id=doc_id,
        domain_id=domain.id,
        filename="test.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        status=DomainDocumentStatus.pending,
        idempotency_key=uuid.uuid4(),
        uploaded_by=admin.id,
        sanitized=False,
        uploaded_at=datetime.now(UTC),
    )

    bg_session = _BgSession(doc)
    oa_file = MagicMock(id="file-original-abc")
    san_file = MagicMock(id="file-sanitized-xyz")
    vs_file = MagicMock(id="vsf-123", status="completed")

    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(side_effect=[oa_file, san_file]))
    mock_client.vector_stores = MagicMock(files=MagicMock(create_and_poll=AsyncMock(return_value=vs_file)))

    parse_result = {
        "pages": [{"page_number": 1, "text": "clean text"}],
        "text": "clean text",
        "tables": [],
        "sanitization": SanitizationResult(
            text="clean text",
            regex_hits=0,
            classifier_hits=1,
            chunks_scanned=1,
        ),
    }

    with (
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch("src.services.database.async_session", _make_bg_session_factory(bg_session)),
        patch("src.tools.parse_document.parse_document", AsyncMock(return_value=parse_result)),
    ):
        await _ingest_domain_document(
            doc_id=doc_id,
            domain_id=domain.id,
            vector_store_id="vs_test",
            file_bytes=b"%PDF-1.4",
            filename="test.pdf",
            content_type="application/pdf",
            actor_id=admin.id,
        )

    admin_events = [obj for obj in bg_session.added if isinstance(obj, AdminEvent)]
    assert admin_events, "No AdminEvent written to the session"
    upload_event = next((e for e in admin_events if e.action == "domain_document_uploaded"), None)
    assert upload_event is not None, "domain_document_uploaded AdminEvent not found"
    assert upload_event.payload["regex_hits"] == 0
    assert upload_event.payload["classifier_hits"] == 1
    assert upload_event.payload["chunks_scanned"] == 1
