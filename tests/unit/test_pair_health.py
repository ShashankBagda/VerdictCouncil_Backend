"""Unit tests for src.tools.pair_health."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.tools.pair_health import check_pair_health


def _mock_httpx_response(status_code=200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = {"searchResults": []}
    resp.raise_for_status = MagicMock()
    return resp


# ------------------------------------------------------------------ #
# Probe success -> records success
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_probe_success_records_success():
    response = _mock_httpx_response(200)

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(return_value=response)
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    mock_breaker = AsyncMock()

    with (
        patch("src.tools.pair_health.httpx.AsyncClient", return_value=mock_http_client),
        patch("src.tools.pair_health._pair_breaker", mock_breaker),
    ):
        result = await check_pair_health()

    assert result["status"] == "healthy"
    assert result["response_code"] == 200
    mock_breaker.record_success.assert_called_once()


# ------------------------------------------------------------------ #
# Probe failure -> records failure
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_probe_failure_records_failure():
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock()
    ))
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    mock_breaker = AsyncMock()

    with (
        patch("src.tools.pair_health.httpx.AsyncClient", return_value=mock_http_client),
        patch("src.tools.pair_health._pair_breaker", mock_breaker),
    ):
        result = await check_pair_health()

    assert result["status"] == "unhealthy"
    assert "error" in result
    mock_breaker.record_failure.assert_called_once()


# ------------------------------------------------------------------ #
# Timeout -> records failure
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_timeout_records_failure():
    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=False)

    mock_breaker = AsyncMock()

    with (
        patch("src.tools.pair_health.httpx.AsyncClient", return_value=mock_http_client),
        patch("src.tools.pair_health._pair_breaker", mock_breaker),
    ):
        result = await check_pair_health()

    assert result["status"] == "unhealthy"
    mock_breaker.record_failure.assert_called_once()
