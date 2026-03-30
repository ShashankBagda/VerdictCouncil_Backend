"""Unit tests for the /api/v1/health endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app


@pytest.mark.asyncio
async def test_get_pair_health_returns_circuit_state():
    """GET /api/v1/health/pair returns circuit breaker status dict."""
    mock_status = {
        "service": "pair_search",
        "state": "closed",
        "failure_count": 0,
        "failure_threshold": 3,
        "recovery_timeout_seconds": 60,
        "opened_at": None,
    }

    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=mock_status)

    with patch(
        "src.api.routes.health.get_pair_search_breaker",
        return_value=mock_breaker,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health/pair")

    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "pair_search"
    assert data["state"] == "closed"
    assert "failure_count" in data
    assert "failure_threshold" in data
    assert "recovery_timeout_seconds" in data


@pytest.mark.asyncio
async def test_get_pair_health_returns_proper_json_structure():
    """Response has all expected fields and correct types."""
    mock_status = {
        "service": "pair_search",
        "state": "open",
        "failure_count": 5,
        "failure_threshold": 3,
        "recovery_timeout_seconds": 60,
        "opened_at": 1711800000.0,
    }

    mock_breaker = AsyncMock()
    mock_breaker.get_status = AsyncMock(return_value=mock_status)

    with patch(
        "src.api.routes.health.get_pair_search_breaker",
        return_value=mock_breaker,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health/pair")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["service"], str)
    assert isinstance(data["state"], str)
    assert isinstance(data["failure_count"], int)
    assert isinstance(data["failure_threshold"], int)
    assert isinstance(data["recovery_timeout_seconds"], int)
    assert isinstance(data["opened_at"], float)
