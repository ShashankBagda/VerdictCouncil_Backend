"""Unit tests for GET /knowledge-base/status (US-017, extended for per-judge store)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.user import User, UserRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(**overrides) -> MagicMock:
    defaults = {
        "id": uuid.uuid4(),
        "name": "Justice Bao",
        "email": "bao@example.com",
        "role": UserRole.judge,
        "password_hash": "hashed",
        "knowledge_base_vector_store_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


_CLOSED_STATUS = {
    "service": "pair_search",
    "state": "closed",
    "failure_count": 0,
    "failure_threshold": 3,
    "recovery_timeout_seconds": 60,
    "opened_at": None,
}

_OPEN_STATUS = {
    "service": "pair_search",
    "state": "open",
    "failure_count": 3,
    "failure_threshold": 3,
    "recovery_timeout_seconds": 60,
    "opened_at": 1700000000.0,
}


def _mock_openai_client(retrieve_mock: AsyncMock | None = None) -> MagicMock:
    if retrieve_mock is None:
        retrieve_mock = AsyncMock()
    return MagicMock(vector_stores=MagicMock(retrieve=retrieve_mock))


def _app_with_overrides(mock_user):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_knowledge_base_status_healthy():
    user = _make_user()
    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=_CLOSED_STATUS)
    mock_client = _mock_openai_client()

    with (
        patch("src.api.routes.knowledge_base.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.api.routes.knowledge_base.settings") as mock_settings,
        patch("src.api.routes.knowledge_base._get_openai_client", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_abc123"

        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/knowledge-base/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["pair_api"]["state"] == "closed"
    assert data["pair_api"]["failure_count"] == 0
    assert data["vector_store"]["configured"] is True
    assert data["vector_store"]["status"] == "healthy"
    assert data["vector_store"]["store_id"] == "vs_abc123"
    assert data["initialized"] is False
    assert data["documents_count"] is None
    assert data["chunks_count"] is None
    assert data["last_updated_at"] is None
    assert "last_checked" in data


async def test_knowledge_base_status_uses_vector_stores_retrieve_not_beta():
    """Regression: status must use vector_stores.retrieve, not beta.vector_stores.retrieve."""
    user = _make_user()
    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=_CLOSED_STATUS)
    retrieve_mock = AsyncMock()
    mock_client = _mock_openai_client(retrieve_mock=retrieve_mock)

    with (
        patch("src.api.routes.knowledge_base.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.api.routes.knowledge_base.settings") as mock_settings,
        patch("src.api.routes.knowledge_base._get_openai_client", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_abc123"

        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/knowledge-base/status")

    assert resp.status_code == 200
    retrieve_mock.assert_awaited()
    # User not initialized — only the global store is fetched.
    assert retrieve_mock.await_count == 1
    assert retrieve_mock.await_args.args == ("vs_abc123",)


async def test_knowledge_base_status_pair_open():
    user = _make_user()
    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=_OPEN_STATUS)
    mock_client = _mock_openai_client()

    with (
        patch("src.api.routes.knowledge_base.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.api.routes.knowledge_base.settings") as mock_settings,
        patch("src.api.routes.knowledge_base._get_openai_client", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_abc123"

        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/knowledge-base/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["pair_api"]["state"] == "open"
    assert data["pair_api"]["failure_count"] == 3


async def test_knowledge_base_status_vector_store_not_configured():
    user = _make_user()
    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=_CLOSED_STATUS)

    with (
        patch("src.api.routes.knowledge_base.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.api.routes.knowledge_base.settings") as mock_settings,
    ):
        mock_settings.openai_vector_store_id = None

        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/knowledge-base/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["vector_store"]["configured"] is False
    assert data["vector_store"]["status"] == "not_configured"


async def test_knowledge_base_status_vector_store_unavailable():
    user = _make_user()
    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=_CLOSED_STATUS)
    mock_client = _mock_openai_client(
        retrieve_mock=AsyncMock(side_effect=Exception("Connection refused"))
    )

    with (
        patch("src.api.routes.knowledge_base.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.api.routes.knowledge_base.settings") as mock_settings,
        patch("src.api.routes.knowledge_base._get_openai_client", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_abc123"

        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/knowledge-base/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["vector_store"]["status"] == "unavailable"
    assert "Vector store health check failed" in data["vector_store"]["error"]


async def test_knowledge_base_status_per_judge_store_populates_fields():
    user = _make_user(knowledge_base_vector_store_id="vs_judge_xyz")
    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=_CLOSED_STATUS)

    # retrieve is called twice: once for global store, once for judge store.
    # Returns vary based on argument.
    global_store = MagicMock()  # content irrelevant — success means "healthy"
    judge_store = MagicMock(
        file_counts=MagicMock(total=7),
        last_active_at=1700000000,
    )

    async def retrieve_side_effect(store_id):
        if store_id == "vs_judge_xyz":
            return judge_store
        return global_store

    retrieve_mock = AsyncMock(side_effect=retrieve_side_effect)
    mock_client = _mock_openai_client(retrieve_mock=retrieve_mock)

    with (
        patch("src.api.routes.knowledge_base.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.api.routes.knowledge_base.settings") as mock_settings,
        patch("src.api.routes.knowledge_base._get_openai_client", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_abc123"

        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/knowledge-base/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["initialized"] is True
    assert data["documents_count"] == 7
    assert data["chunks_count"] is None
    assert data["last_updated_at"] is not None
    # Exactly two retrieve calls: global + judge — NOT list_kb_files.
    assert retrieve_mock.await_count == 2


async def test_knowledge_base_status_per_judge_retrieve_failure_stays_initialized():
    user = _make_user(knowledge_base_vector_store_id="vs_judge_xyz")
    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=_CLOSED_STATUS)

    async def retrieve_side_effect(store_id):
        if store_id == "vs_judge_xyz":
            raise Exception("Judge store is sad")
        return MagicMock()

    retrieve_mock = AsyncMock(side_effect=retrieve_side_effect)
    mock_client = _mock_openai_client(retrieve_mock=retrieve_mock)

    with (
        patch("src.api.routes.knowledge_base.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.api.routes.knowledge_base.settings") as mock_settings,
        patch("src.api.routes.knowledge_base._get_openai_client", return_value=mock_client),
    ):
        mock_settings.openai_vector_store_id = "vs_abc123"

        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/v1/knowledge-base/status")

    data = resp.json()
    # initialized stays true — the column is set, the metadata fetch just failed.
    assert data["initialized"] is True
    assert data["documents_count"] is None
    assert data["last_updated_at"] is None


async def test_knowledge_base_status_non_judge_forbidden():
    clerk = _make_user(role=UserRole.admin)
    app = _app_with_overrides(clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/v1/knowledge-base/status")

    assert resp.status_code == 403
