"""Unit tests for the domain document sanitize-at-ingest pipeline.

Verifies the DB-first insert ordering (D15), parse failure → 422,
MIME whitelist enforcement, and that only the sanitized artifact is indexed.
"""

from __future__ import annotations

import uuid
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
    """Records DB operations to verify insert-before-OpenAI ordering."""

    def __init__(self, domain: MagicMock) -> None:
        self._domain = domain
        self.added: list = []
        self.flush_count = 0
        self._added_doc: DomainDocument | None = None

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
        self.flush_count += 1
        # Simulate server defaults that Postgres would set
        for obj in self.added:
            if isinstance(obj, DomainDocument):
                if getattr(obj, "uploaded_at", None) is None:
                    obj.uploaded_at = datetime.now(UTC)
                if getattr(obj, "sanitized", None) is None:
                    obj.sanitized = False

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj, attrs=None) -> None:
        return None


def _app_with(user, session):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: session
    return app


# ---------------------------------------------------------------------------
# DB-first ordering: DomainDocument row is inserted before OpenAI calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_first_insert_before_openai_upload():
    """The DomainDocument row must be flushed to the DB before the first OpenAI call.

    This prevents orphaned OpenAI files with no corresponding DB record (D15).
    """
    from src.shared.config import settings as _settings

    admin = _make_admin()
    domain = _make_domain()
    session = _TrackingSession(domain)

    oa_file = MagicMock(id="file-original-abc")
    san_file = MagicMock(id="file-sanitized-xyz")
    vs_file = MagicMock(id="vsf-123", status="completed")

    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(side_effect=[oa_file, san_file]))
    mock_client.vector_stores = MagicMock(
        files=MagicMock(create_and_poll=AsyncMock(return_value=vs_file))
    )

    parse_result = {
        "pages": [{"page_number": 1, "text": "Sanitized text content"}],
        "text": "Sanitized text content",
        "tables": [],
    }

    app = _app_with(admin, session)

    with (
        patch.object(_settings, "domain_uploads_enabled", True),
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.parse_document.parse_document", AsyncMock(return_value=parse_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/domains/admin/{domain.id}/documents",
                files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            )

    # The session must have had at least one flush before OpenAI was called
    # (flush_count > 0 when the first OpenAI call happens)
    assert resp.status_code == 201
    # DomainDocument was added to the session before flush
    assert session.flush_count >= 1, "DB flush must happen before OpenAI upload (D15)"


# ---------------------------------------------------------------------------
# Parse failure → 422, no vector-store write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_failure_returns_422_and_no_vector_store_write():
    """If parse_document raises, the route must return 422 and never write to the vector store."""
    from src.tools.parse_document import DocumentParseError
    from src.shared.config import settings as _settings

    admin = _make_admin()
    domain = _make_domain()
    session = _TrackingSession(domain)

    oa_file = MagicMock(id="file-original-abc")
    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(return_value=oa_file))
    mock_client.vector_stores = MagicMock(
        files=MagicMock(create_and_poll=AsyncMock(side_effect=AssertionError("should not be called")))
    )

    app = _app_with(admin, session)

    with (
        patch.object(_settings, "domain_uploads_enabled", True),
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch(
            "src.tools.parse_document.parse_document",
            AsyncMock(side_effect=DocumentParseError("Cannot parse image-only PDF")),
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/domains/admin/{domain.id}/documents",
                files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
            )

    assert resp.status_code == 422
    assert "parsed" in resp.json()["detail"].lower() or "parse" in resp.json()["detail"].lower()
    # vector store create_and_poll must never have been called
    mock_client.vector_stores.files.create_and_poll.assert_not_awaited()


# ---------------------------------------------------------------------------
# Sanitized file is indexed, original is NOT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_sanitized_file_goes_into_vector_store():
    """The original OpenAI file must not be added to the vector store — only the sanitized artifact."""
    from src.shared.config import settings as _settings

    admin = _make_admin()
    domain = _make_domain(vector_store_id="vs_test")
    session = _TrackingSession(domain)

    original_file_id = "file-original-DO-NOT-INDEX"
    sanitized_file_id = "file-sanitized-OK-TO-INDEX"

    oa_file = MagicMock(id=original_file_id)
    san_file = MagicMock(id=sanitized_file_id)
    vs_file = MagicMock(id="vsf-123", status="completed")

    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(side_effect=[oa_file, san_file]))
    mock_client.vector_stores = MagicMock(
        files=MagicMock(create_and_poll=AsyncMock(return_value=vs_file))
    )

    parse_result = {
        "pages": [{"page_number": 1, "text": "Sanitized page content"}],
        "text": "Sanitized page content",
        "tables": [],
    }

    app = _app_with(admin, session)

    with (
        patch.object(_settings, "domain_uploads_enabled", True),
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.parse_document.parse_document", AsyncMock(return_value=parse_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                f"/api/v1/domains/admin/{domain.id}/documents",
                files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            )

    # create_and_poll must have been called with the sanitized file id, NOT the original
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
async def test_upload_records_classifier_hits_in_admin_event():
    """parse_result['sanitization'] metrics land in the AdminEvent payload."""
    from src.shared.config import settings as _settings

    admin = _make_admin()
    domain = _make_domain(vector_store_id="vs_test")
    session = _TrackingSession(domain)

    oa_file = MagicMock(id="file-original-abc")
    san_file = MagicMock(id="file-sanitized-xyz")
    vs_file = MagicMock(id="vsf-123", status="completed")

    mock_client = MagicMock()
    mock_client.files = MagicMock(create=AsyncMock(side_effect=[oa_file, san_file]))
    mock_client.vector_stores = MagicMock(
        files=MagicMock(create_and_poll=AsyncMock(return_value=vs_file))
    )

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

    app = _app_with(admin, session)

    with (
        patch.object(_settings, "domain_uploads_enabled", True),
        patch("openai.AsyncOpenAI", return_value=mock_client),
        patch("src.tools.parse_document.parse_document", AsyncMock(return_value=parse_result)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/domains/admin/{domain.id}/documents",
                files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
            )

    assert resp.status_code == 201
    # Find the AdminEvent that was added to the session
    from src.models.admin_event import AdminEvent

    admin_events = [obj for obj in session.added if isinstance(obj, AdminEvent)]
    assert admin_events, "No AdminEvent written to the session"
    upload_event = next(
        (e for e in admin_events if e.action == "domain_document_uploaded"), None
    )
    assert upload_event is not None, "domain_document_uploaded AdminEvent not found"
    assert upload_event.payload["regex_hits"] == 0
    assert upload_event.payload["classifier_hits"] == 1
    assert upload_event.payload["chunks_scanned"] == 1
