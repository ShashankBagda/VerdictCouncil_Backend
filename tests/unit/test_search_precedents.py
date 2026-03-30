"""Unit tests for src.tools.search_precedents."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import redis.asyncio as aioredis

from src.shared.circuit_breaker import CircuitState
from src.tools.search_precedents import search_precedents


def _pair_api_response(results: list[dict] | None = None):
    """Build a fake PAIR API JSON response."""
    if results is None:
        results = [
            {
                "citationNum": "[2025] SGHC 42",
                "court": "SGHC",
                "snippet": "The court held that...",
                "matchScore": {"score": 0.87},
                "url": "https://judiciary.gov.sg/case/42",
            },
            {
                "citationNum": "[2024] SGCA 15",
                "court": "SGCA",
                "snippet": "On appeal, the court found...",
                "matchScore": {"score": 0.72},
                "url": "https://judiciary.gov.sg/case/15",
            },
        ]
    return {"searchResults": results}


def _mock_httpx_response(payload: dict, status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def mock_redis():
    """Yield a mocked async Redis client."""
    r = AsyncMock(spec=aioredis.Redis)
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    return r


@pytest.fixture
def mock_breaker_closed():
    """Yield a mock circuit breaker in CLOSED state."""
    breaker = AsyncMock()
    breaker.check_recovery = AsyncMock(return_value=CircuitState.CLOSED)
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock(return_value=CircuitState.CLOSED)
    return breaker


# ------------------------------------------------------------------ #
# Happy path: mock httpx returns results
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_happy_path_returns_structured_results(mock_redis, mock_breaker_closed):
    response = _mock_httpx_response(_pair_api_response())

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "src.tools.search_precedents._get_redis_client",
            return_value=mock_redis,
        ),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch("src.tools.search_precedents.get_pair_search_breaker", return_value=mock_breaker_closed),
    ):
        results = await search_precedents("breach of contract deposit refund")

    assert len(results) == 2
    # Sorted by similarity descending
    assert results[0]["similarity_score"] >= results[1]["similarity_score"]
    assert results[0]["citation"] == "[2025] SGHC 42"
    assert results[0]["source"] == "live_search"
    assert "url" in results[0]
    mock_breaker_closed.record_success.assert_called_once()


# ------------------------------------------------------------------ #
# API timeout -> empty list returned with warning
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_api_timeout_falls_back_to_vector_store(mock_redis, mock_breaker_closed):
    """When the PAIR API times out, search_precedents falls back to vector store."""
    with (
        patch(
            "src.tools.search_precedents._get_redis_client",
            return_value=mock_redis,
        ),
        patch(
            "src.tools.search_precedents._call_pair_api",
            AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ),
        patch("src.tools.search_precedents.get_pair_search_breaker", return_value=mock_breaker_closed),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=[]),
        ),
    ):
        results = await search_precedents("timeout query")

    assert results == []
    mock_breaker_closed.record_failure.assert_called_once()


# ------------------------------------------------------------------ #
# Redis cache hit -> no HTTP call made
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_cache_hit_skips_http_call(mock_redis):
    cached_data = [
        {
            "citation": "[2023] SGHC 99",
            "court": "SGHC",
            "outcome": "",
            "reasoning_summary": "Cached result",
            "similarity_score": 0.95,
            "url": "https://judiciary.gov.sg/case/99",
            "source": "live_search",
        }
    ]
    mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock()

    with (
        patch(
            "src.tools.search_precedents._get_redis_client",
            return_value=mock_redis,
        ),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
    ):
        results = await search_precedents("cached query")

    assert len(results) == 1
    assert results[0]["citation"] == "[2023] SGHC 99"
    # HTTP client's post should NOT have been called
    mock_http_client.post.assert_not_called()


# ------------------------------------------------------------------ #
# Empty results from PAIR API
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_empty_results_from_api(mock_redis, mock_breaker_closed):
    response = _mock_httpx_response({"searchResults": []})

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "src.tools.search_precedents._get_redis_client",
            return_value=mock_redis,
        ),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch("src.tools.search_precedents.get_pair_search_breaker", return_value=mock_breaker_closed),
    ):
        results = await search_precedents("obscure legal question no results")

    assert results == []


# ------------------------------------------------------------------ #
# Rate limiting: Redis incr shows count > 2
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_rate_limiting_allows_first_two_requests(mock_redis, mock_breaker_closed):
    """Rate limiter should allow requests when count <= 2."""
    mock_redis.incr = AsyncMock(return_value=1)

    response = _mock_httpx_response(_pair_api_response())
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "src.tools.search_precedents._get_redis_client",
            return_value=mock_redis,
        ),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch("src.tools.search_precedents.get_pair_search_breaker", return_value=mock_breaker_closed),
    ):
        results = await search_precedents("rate limit test")

    assert len(results) == 2
    # expire should be called when count == 1
    mock_redis.expire.assert_called_once()


# ================================================================== #
# Circuit breaker integration tests
# ================================================================== #


# ------------------------------------------------------------------ #
# Breaker CLOSED + PAIR success -> record_success called
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_breaker_closed_pair_success(mock_redis, mock_breaker_closed):
    """When breaker is CLOSED and PAIR succeeds, record_success is called."""
    response = _mock_httpx_response(_pair_api_response())
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch("src.tools.search_precedents.get_pair_search_breaker", return_value=mock_breaker_closed),
    ):
        results = await search_precedents("test query")

    assert len(results) == 2
    mock_breaker_closed.record_success.assert_called_once()
    assert all("fallback_used" not in r for r in results)


# ------------------------------------------------------------------ #
# Breaker OPEN -> fallback called directly
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_breaker_open_uses_fallback(mock_redis):
    """When breaker is OPEN, vector store fallback is called without trying PAIR."""
    mock_breaker = AsyncMock()
    mock_breaker.check_recovery = AsyncMock(return_value=CircuitState.OPEN)

    fallback_results = [
        {
            "citation": "fallback_case.pdf",
            "court": "",
            "outcome": "",
            "reasoning_summary": "Fallback result",
            "similarity_score": 0.75,
            "url": "",
            "source": "vector_store_fallback",
        }
    ]

    mock_call_pair = AsyncMock()

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.get_pair_search_breaker", return_value=mock_breaker),
        patch("src.tools.search_precedents.vector_store_search", AsyncMock(return_value=fallback_results)),
        patch("src.tools.search_precedents._call_pair_api", mock_call_pair),
    ):
        results = await search_precedents("test query")

    assert len(results) == 1
    assert results[0]["source"] == "vector_store_fallback"
    assert results[0]["fallback_used"] is True
    mock_call_pair.assert_not_called()


# ------------------------------------------------------------------ #
# Fallback results tagged with fallback_used: true
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_fallback_results_tagged(mock_redis, mock_breaker_closed):
    """When PAIR fails and fallback is used, results have fallback_used=True."""
    fallback_results = [
        {
            "citation": "fallback_case.pdf",
            "court": "",
            "outcome": "",
            "reasoning_summary": "Fallback result",
            "similarity_score": 0.8,
            "url": "",
            "source": "vector_store_fallback",
        }
    ]

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.get_pair_search_breaker", return_value=mock_breaker_closed),
        patch(
            "src.tools.search_precedents._call_pair_api",
            AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ),
        patch("src.tools.search_precedents.vector_store_search", AsyncMock(return_value=fallback_results)),
    ):
        results = await search_precedents("test query")

    assert len(results) == 1
    assert results[0]["fallback_used"] is True
