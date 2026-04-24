"""Unit tests for POST /precedents/search (US-016)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.api.deps import get_current_user, get_db
from src.models.user import User, UserRole
from src.tools.search_precedents import SearchResult

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
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    user = MagicMock(spec=User)
    for k, v in defaults.items():
        setattr(user, k, v)
    return user


def _app_with_overrides(mock_user):
    app = create_app()
    # precedent_search route doesn't use DB; override with no-op
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: mock_user
    return app


_SAMPLE_PRECEDENTS = [
    {
        "citation": "ABC v DEF [2023] SGDC 1",
        "court": "District Court",
        "outcome": "Judgment for plaintiff",
        "reasoning_summary": "Contract breach established.",
        "similarity_score": 0.87,
        "url": "https://example.com/abc",
        "source": "live_search",
    }
]

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_search_precedents_success():
    user = _make_user()
    search_result = SearchResult(
        precedents=_SAMPLE_PRECEDENTS,
        metadata={"source_failed": False, "fallback_used": False, "pair_status": "ok"},
    )

    with patch(
        "src.api.routes.precedent_search.search_precedents_with_meta",
        new=AsyncMock(return_value=search_result),
    ):
        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/precedents/search",
                json={"query": "contract breach damages", "jurisdiction": "small_claims"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["citation"] == "ABC v DEF [2023] SGDC 1"
    assert data["metadata"]["source_failed"] is False
    assert data["metadata"]["fallback_used"] is False


async def test_search_precedents_fallback_used():
    user = _make_user()
    search_result = SearchResult(
        precedents=_SAMPLE_PRECEDENTS,
        metadata={"source_failed": False, "fallback_used": True, "pair_status": "circuit_open"},
    )

    with patch(
        "src.api.routes.precedent_search.search_precedents_with_meta",
        new=AsyncMock(return_value=search_result),
    ):
        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/precedents/search",
                json={"query": "contract breach", "jurisdiction": "small_claims"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["metadata"]["fallback_used"] is True
    assert data["metadata"]["pair_status"] == "circuit_open"


async def test_search_precedents_empty_results():
    user = _make_user()
    search_result = SearchResult(
        precedents=[],
        metadata={"source_failed": True, "fallback_used": True, "pair_status": "failed (open)"},
    )

    with patch(
        "src.api.routes.precedent_search.search_precedents_with_meta",
        new=AsyncMock(return_value=search_result),
    ):
        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/precedents/search",
                json={"query": "traffic fine appeal"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["metadata"]["source_failed"] is True


async def test_search_precedents_query_too_short():
    user = _make_user()
    app = _app_with_overrides(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/precedents/search",
            json={"query": "ab"},
        )

    assert resp.status_code == 422


async def test_search_precedents_non_judge_forbidden():
    clerk = _make_user(role=UserRole.admin)
    app = _app_with_overrides(clerk)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/precedents/search",
            json={"query": "contract breach"},
        )

    assert resp.status_code == 403


async def test_search_precedents_max_results_validation():
    user = _make_user()
    app = _app_with_overrides(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/precedents/search",
            json={"query": "contract breach", "max_results": 0},
        )

    assert resp.status_code == 422


async def test_search_precedents_service_unavailable():
    user = _make_user()

    with patch(
        "src.api.routes.precedent_search.search_precedents_with_meta",
        new=AsyncMock(side_effect=Exception("Redis connection refused")),
    ):
        app = _app_with_overrides(user)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/precedents/search",
                json={"query": "contract breach damages"},
            )

    assert resp.status_code == 503


async def test_search_precedents_query_stripped_too_short():
    """Query that passes Pydantic min_length=3 but sanitizes to < 3 chars returns 422."""
    user = _make_user()
    app = _app_with_overrides(user)
    # Three null bytes pass Pydantic length check (len=3) but strip to empty string
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/v1/precedents/search",
            json={"query": "\x00\x00\x00"},
        )

    assert resp.status_code == 422
