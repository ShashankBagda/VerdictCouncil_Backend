"""Unit tests for src.tools.search_precedents."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import redis.asyncio as aioredis

from src.shared.circuit_breaker import CircuitState
from src.tools.search_precedents import (
    SearchResult,
    search_precedents,
    search_precedents_with_meta,
)
from src.tools.vector_store_fallback import VectorStoreError


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


@pytest.fixture
def mock_breaker_open():
    """Yield a mock circuit breaker in OPEN state."""
    breaker = AsyncMock()
    breaker.check_recovery = AsyncMock(return_value=CircuitState.OPEN)
    return breaker


_FALLBACK_RESULTS = [
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


# ================================================================== #
# Happy path
# ================================================================== #


@pytest.mark.asyncio
async def test_happy_path_returns_structured_results(mock_redis, mock_breaker_closed):
    response = _mock_httpx_response(_pair_api_response())

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
    ):
        results = await search_precedents("breach of contract deposit refund")

    assert len(results) == 2
    assert results[0]["similarity_score"] >= results[1]["similarity_score"]
    assert results[0]["citation"] == "[2025] SGHC 42"
    assert results[0]["source"] == "live_search"
    assert "url" in results[0]
    mock_breaker_closed.record_success.assert_called_once()


# ================================================================== #
# API timeout -> fallback
# ================================================================== #


@pytest.mark.asyncio
async def test_api_timeout_falls_back_to_vector_store(mock_redis, mock_breaker_closed):
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents._call_pair_api",
            AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=[]),
        ),
    ):
        results = await search_precedents("timeout query")

    assert results == []
    mock_breaker_closed.record_failure.assert_called_once()


# ================================================================== #
# Cache tests
# ================================================================== #


@pytest.mark.asyncio
async def test_cache_hit_new_format(mock_redis):
    """Cache hit with new {precedents, metadata} format returns both."""
    cached_payload = {
        "precedents": [
            {
                "citation": "[2023] SGHC 99",
                "court": "SGHC",
                "outcome": "",
                "reasoning_summary": "Cached result",
                "similarity_score": 0.95,
                "url": "https://judiciary.gov.sg/case/99",
                "source": "live_search",
            }
        ],
        "metadata": {"source_failed": False, "fallback_used": False, "pair_status": "ok"},
    }
    mock_redis.get = AsyncMock(return_value=json.dumps(cached_payload))

    with patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis):
        result = await search_precedents_with_meta("cached query")

    assert isinstance(result, SearchResult)
    assert len(result.precedents) == 1
    assert result.precedents[0]["citation"] == "[2023] SGHC 99"
    assert result.metadata["source_failed"] is False


@pytest.mark.asyncio
async def test_cache_hit_legacy_format(mock_redis):
    """Cache hit with legacy plain-list format still works."""
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

    with patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis):
        results = await search_precedents("cached query")

    assert len(results) == 1
    assert results[0]["citation"] == "[2023] SGHC 99"


@pytest.mark.asyncio
async def test_cache_roundtrip_preserves_metadata(mock_redis, mock_breaker_closed):
    """Metadata is cached and survives a round-trip."""
    response = _mock_httpx_response(_pair_api_response())
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
    ):
        await search_precedents_with_meta("test query")

    # Verify cached payload includes metadata
    cached_call = mock_redis.setex.call_args
    cached_json = json.loads(cached_call[0][2])
    assert "precedents" in cached_json
    assert "metadata" in cached_json
    assert cached_json["metadata"]["source_failed"] is False


# ================================================================== #
# Empty results from PAIR API
# ================================================================== #


@pytest.mark.asyncio
async def test_empty_results_from_api(mock_redis, mock_breaker_closed):
    response = _mock_httpx_response({"searchResults": []})
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
    ):
        result = await search_precedents_with_meta("obscure legal question")

    assert result.precedents == []
    assert result.metadata["source_failed"] is False


# ================================================================== #
# Rate limiting
# ================================================================== #


@pytest.mark.asyncio
async def test_rate_limiting_allows_first_two_requests(mock_redis, mock_breaker_closed):
    mock_redis.incr = AsyncMock(return_value=1)
    response = _mock_httpx_response(_pair_api_response())
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
    ):
        results = await search_precedents("rate limit test")

    assert len(results) == 2
    mock_redis.expire.assert_called_once()


# ================================================================== #
# Circuit breaker integration
# ================================================================== #


@pytest.mark.asyncio
async def test_breaker_closed_pair_success(mock_redis, mock_breaker_closed):
    response = _mock_httpx_response(_pair_api_response())
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
    ):
        results = await search_precedents("test query")

    assert len(results) == 2
    mock_breaker_closed.record_success.assert_called_once()
    assert all("fallback_used" not in r for r in results)


@pytest.mark.asyncio
async def test_breaker_open_uses_fallback(mock_redis, mock_breaker_open):
    mock_call_pair = AsyncMock()

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_open,
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=list(_FALLBACK_RESULTS)),
        ),
        patch("src.tools.search_precedents._call_pair_api", mock_call_pair),
    ):
        results = await search_precedents("test query")

    assert len(results) == 1
    assert results[0]["source"] == "vector_store_fallback"
    assert results[0]["fallback_used"] is True
    mock_call_pair.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_results_tagged(mock_redis, mock_breaker_closed):
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
        patch(
            "src.tools.search_precedents._call_pair_api",
            AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=list(_FALLBACK_RESULTS)),
        ),
    ):
        results = await search_precedents("test query")

    assert len(results) == 1
    assert results[0]["fallback_used"] is True


# ================================================================== #
# Source failure metadata tests (8 scenarios)
# ================================================================== #


@pytest.mark.asyncio
async def test_pair_fail_vector_store_fail_source_failed_true(mock_redis, mock_breaker_closed):
    """PAIR HTTPError + VectorStoreError -> source_failed=true"""
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
        patch(
            "src.tools.search_precedents._call_pair_api",
            AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(side_effect=VectorStoreError("not configured")),
        ),
    ):
        result = await search_precedents_with_meta("test query")

    assert result.metadata["source_failed"] is True
    assert result.precedents == []


@pytest.mark.asyncio
async def test_pair_fail_vector_store_empty_source_failed_false(mock_redis, mock_breaker_closed):
    """PAIR HTTPError + vector store returns [] -> source_failed=false"""
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
        patch(
            "src.tools.search_precedents._call_pair_api",
            AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=[]),
        ),
    ):
        result = await search_precedents_with_meta("test query")

    assert result.metadata["source_failed"] is False
    assert result.metadata["fallback_used"] is True


@pytest.mark.asyncio
async def test_pair_fail_vector_store_returns_results(mock_redis, mock_breaker_closed):
    """PAIR HTTPError + vector store returns results -> source_failed=false, fallback_used=true"""
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
        patch(
            "src.tools.search_precedents._call_pair_api",
            AsyncMock(side_effect=httpx.TimeoutException("timed out")),
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=list(_FALLBACK_RESULTS)),
        ),
    ):
        result = await search_precedents_with_meta("test query")

    assert result.metadata["source_failed"] is False
    assert result.metadata["fallback_used"] is True
    assert len(result.precedents) == 1


@pytest.mark.asyncio
async def test_breaker_open_vector_store_error_source_failed_true(mock_redis, mock_breaker_open):
    """Circuit breaker OPEN + VectorStoreError -> source_failed=true"""
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_open,
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(side_effect=VectorStoreError("not configured")),
        ),
    ):
        result = await search_precedents_with_meta("test query")

    assert result.metadata["source_failed"] is True
    assert result.precedents == []


@pytest.mark.asyncio
async def test_breaker_open_vector_store_empty_source_failed_true(mock_redis, mock_breaker_open):
    """Circuit breaker OPEN + vector store returns [] -> source_failed=true"""
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_open,
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=[]),
        ),
    ):
        result = await search_precedents_with_meta("test query")

    assert result.metadata["source_failed"] is True
    assert result.precedents == []


@pytest.mark.asyncio
async def test_breaker_open_vector_store_returns_results(mock_redis, mock_breaker_open):
    """Circuit breaker OPEN + vector store returns results -> source_failed=false"""
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_open,
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(return_value=list(_FALLBACK_RESULTS)),
        ),
    ):
        result = await search_precedents_with_meta("test query")

    assert result.metadata["source_failed"] is False
    assert result.metadata["fallback_used"] is True
    assert len(result.precedents) == 1


@pytest.mark.asyncio
async def test_pair_succeeds_empty_source_failed_false(mock_redis, mock_breaker_closed):
    """PAIR returns [] normally -> source_failed=false"""
    response = _mock_httpx_response({"searchResults": []})
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch("src.tools.search_precedents.httpx.AsyncClient", return_value=mock_http_client),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_closed,
        ),
    ):
        result = await search_precedents_with_meta("obscure query")

    assert result.metadata["source_failed"] is False
    assert result.metadata["fallback_used"] is False
    assert result.precedents == []


@pytest.mark.asyncio
async def test_source_failure_not_cached(mock_redis, mock_breaker_open):
    """Source failures are NOT cached to avoid extending outages."""
    with (
        patch("src.tools.search_precedents._get_redis_client", return_value=mock_redis),
        patch(
            "src.tools.search_precedents.get_pair_search_breaker",
            return_value=mock_breaker_open,
        ),
        patch(
            "src.tools.search_precedents.vector_store_search",
            AsyncMock(side_effect=VectorStoreError("not configured")),
        ),
    ):
        result = await search_precedents_with_meta("outage query")

    assert result.metadata["source_failed"] is True
    # Verify setex was NOT called — failures should not be cached
    mock_redis.setex.assert_not_called()
