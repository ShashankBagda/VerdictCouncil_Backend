"""Unit tests for src.api.routes.domains — CRUD, auth guards, and schema scoping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.domain import Domain
from src.models.user import User, UserRole


def _make_user(role: UserRole = UserRole.admin, **overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Admin User",
        "email": "admin@example.com",
        "role": role,
        "password_hash": "hashed",
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def _make_domain(**overrides) -> MagicMock:
    domain_id = uuid.uuid4()
    defaults = {
        "id": domain_id,
        "code": "small_claims",
        "name": "Small Claims Tribunal",
        "description": "SCT jurisdiction",
        "vector_store_id": "vs_test123",
        "is_active": True,
        "provisioning_started_at": None,
        "provisioning_attempts": 0,
        "created_by": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    domain = MagicMock(spec=Domain)
    for k, v in defaults.items():
        setattr(domain, k, v)
    return domain


class _FakeSession:
    def __init__(self, scalar_result=None) -> None:
        self.added: list = []
        self.deleted: list = []
        self._scalar_result = scalar_result

    def add(self, obj) -> None:
        self.added.append(obj)

    async def delete(self, obj) -> None:
        self.deleted.append(obj)

    async def get(self, model, pk):
        if self._scalar_result is not None:
            return self._scalar_result
        return None

    async def execute(self, stmt):
        mock = MagicMock()
        mock.scalars.return_value.all.return_value = []
        mock.scalar_one_or_none.return_value = None
        mock.scalars.return_value.first.return_value = None
        if self._scalar_result is not None:
            mock.scalar_one_or_none.return_value = self._scalar_result
        return mock

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, obj, _=None) -> None:
        return None


def _app_with_overrides(user, session):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: session
    return app


# ---------------------------------------------------------------------------
# Public endpoint — GET /api/v1/domains
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_list_domains_returns_200():
    """Authenticated (any role) user can list active domains."""
    judge = _make_user(role=UserRole.judge)
    domain = _make_domain()
    session = _FakeSession()
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[domain]))))
    )
    app = _app_with_overrides(judge, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/domains/")

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_public_list_domains_omits_vector_store_id():
    """Public domain list must not expose vector_store_id to judges."""
    judge = _make_user(role=UserRole.judge)
    domain = _make_domain(vector_store_id="vs_secret_store")
    session = _FakeSession()
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[domain]))))
    )
    app = _app_with_overrides(judge, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/domains/")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    for item in body:
        assert "vector_store_id" not in item, "vector_store_id must not appear in public response"
        assert "is_active" not in item, "is_active must not appear in public response"


# ---------------------------------------------------------------------------
# Admin list — GET /api/v1/domains/admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_list_domains_judge_gets_403():
    """Non-admin user must receive 403 on admin domain list endpoint."""
    judge = _make_user(role=UserRole.judge)
    session = _FakeSession()
    app = _app_with_overrides(judge, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/domains/admin")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_domains_admin_gets_200():
    """Admin user receives 200 on admin domain list endpoint."""
    admin = _make_user(role=UserRole.admin)
    domain = _make_domain()
    session = _FakeSession()
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[domain]))))
    )
    app = _app_with_overrides(admin, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/domains/admin")

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Create domain — POST /api/v1/domains/admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_domain_judge_gets_403():
    """Non-admin must not be able to create domains."""
    judge = _make_user(role=UserRole.judge)
    session = _FakeSession()
    app = _app_with_overrides(judge, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/domains/admin",
            json={"code": "test_domain", "name": "Test Domain"},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_domain_duplicate_code_returns_400():
    """Creating a domain with an existing code returns 400."""
    admin = _make_user(role=UserRole.admin)
    existing = _make_domain(code="small_claims")
    session = _FakeSession(scalar_result=existing)
    app = _app_with_overrides(admin, session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/domains/admin",
            json={"code": "small_claims", "name": "Duplicate"},
        )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Update domain — PATCH /api/v1/domains/admin/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_domain_judge_gets_403():
    """Non-admin cannot update domains."""
    judge = _make_user(role=UserRole.judge)
    session = _FakeSession()
    app = _app_with_overrides(judge, session)
    domain_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/domains/admin/{domain_id}",
            json={"name": "New Name"},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_domain_not_found_returns_404():
    """PATCH on nonexistent domain returns 404."""
    admin = _make_user(role=UserRole.admin)
    session = _FakeSession(scalar_result=None)
    app = _app_with_overrides(admin, session)
    domain_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/api/v1/domains/admin/{domain_id}",
            json={"name": "New Name"},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Document upload auth guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_document_judge_gets_403():
    """Non-admin cannot upload documents."""
    judge = _make_user(role=UserRole.judge)
    session = _FakeSession()
    app = _app_with_overrides(judge, session)
    domain_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/v1/domains/admin/{domain_id}/documents",
            files={"file": ("test.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_upload_document_unsupported_mime_returns_415():
    """Uploading an unsupported MIME type returns 415."""
    from src.shared.config import settings as _settings

    admin = _make_user(role=UserRole.admin)
    domain = _make_domain(vector_store_id="vs_test")
    session = _FakeSession(scalar_result=domain)
    app = _app_with_overrides(admin, session)
    domain_id = domain.id

    with patch.object(_settings, "domain_uploads_enabled", True):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/domains/admin/{domain_id}/documents",
                files={"file": ("test.exe", b"\x00\x01\x02", "application/x-msdownload")},
            )

    assert resp.status_code == 415
