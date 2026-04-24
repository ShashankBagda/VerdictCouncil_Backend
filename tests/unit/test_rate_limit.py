"""Tests for the rate limiting middleware."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.middleware.rate_limit import RateLimitMiddleware


def _make_app(requests_per_minute: int = 60) -> FastAPI:
    """Create a minimal FastAPI app with the rate limiter attached."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, requests_per_minute=requests_per_minute)

    @app.get("/ping")
    async def ping():
        return {"status": "ok"}

    return app


@pytest.mark.anyio
async def test_requests_within_limit_pass():
    app = _make_app(requests_per_minute=10)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [await client.get("/ping") for _ in range(5)]
    assert all(r.status_code == 200 for r in responses)


@pytest.mark.anyio
async def test_requests_exceed_limit_blocked():
    app = _make_app(requests_per_minute=3)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [await client.get("/ping") for _ in range(5)]
    # First 3 should pass, last 2 should be 429
    assert [r.status_code for r in responses] == [200, 200, 200, 429, 429]


@pytest.mark.anyio
async def test_retry_after_header_present():
    app = _make_app(requests_per_minute=1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/ping")  # use up the limit
        response = await client.get("/ping")  # should be blocked
    assert response.status_code == 429
    assert "retry-after" in response.headers
    retry_after = int(response.headers["retry-after"])
    assert retry_after > 0
