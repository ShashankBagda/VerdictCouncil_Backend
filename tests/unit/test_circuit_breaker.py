"""Unit tests for src.shared.circuit_breaker."""

import time
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as aioredis

from src.shared.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture
def mock_redis():
    """Yield a mocked async Redis client with default CLOSED state."""
    r = AsyncMock(spec=aioredis.Redis)
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    r.delete = AsyncMock()
    pipe = AsyncMock()
    pipe.set = AsyncMock()
    pipe.delete = AsyncMock()
    pipe.execute = AsyncMock()
    r.pipeline = lambda: pipe
    r._pipe = pipe  # expose for assertions
    return r


@pytest.fixture
def breaker(mock_redis):
    cb = CircuitBreaker(
        service_name="test_service",
        failure_threshold=3,
        recovery_timeout=60,
    )
    cb._get_redis = AsyncMock(return_value=mock_redis)
    return cb


# ------------------------------------------------------------------ #
# Default state is CLOSED
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_default_state_is_closed(breaker):
    state = await breaker.get_state()
    assert state == CircuitState.CLOSED


# ------------------------------------------------------------------ #
# CLOSED -> OPEN after N failures
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_closed_to_open_after_threshold_failures(breaker, mock_redis):
    mock_redis.incr = AsyncMock(return_value=3)
    # get_state returns CLOSED (default)
    mock_redis.get = AsyncMock(return_value=None)

    new_state = await breaker.record_failure()
    assert new_state == CircuitState.OPEN


# ------------------------------------------------------------------ #
# Failures below threshold stay CLOSED
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_failures_below_threshold_stay_closed(breaker, mock_redis):
    mock_redis.incr = AsyncMock(return_value=2)
    mock_redis.get = AsyncMock(return_value=None)

    new_state = await breaker.record_failure()
    assert new_state == CircuitState.CLOSED


# ------------------------------------------------------------------ #
# OPEN -> HALF_OPEN after recovery timeout
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_open_to_half_open_after_timeout(breaker, mock_redis):
    opened_at = str(time.time() - 120)  # 120s ago, timeout is 60s

    call_count = 0

    async def get_side_effect(key):
        nonlocal call_count
        if ":state" in key:
            call_count += 1
            if call_count == 1:
                return CircuitState.OPEN.value
            # After transition
            return CircuitState.HALF_OPEN.value
        if ":opened_at" in key:
            return opened_at
        if ":failures" in key:
            return "3"
        return None

    mock_redis.get = AsyncMock(side_effect=get_side_effect)

    state = await breaker.get_state()
    assert state == CircuitState.HALF_OPEN


# ------------------------------------------------------------------ #
# HALF_OPEN -> CLOSED on success
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_half_open_to_closed_on_success(breaker, mock_redis):
    mock_redis.get = AsyncMock(return_value=CircuitState.HALF_OPEN.value)

    await breaker.record_success()

    # Verify pipeline was used to set CLOSED state
    pipe = mock_redis._pipe
    pipe.execute.assert_called()


# ------------------------------------------------------------------ #
# HALF_OPEN -> OPEN on probe failure
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_half_open_to_open_on_probe_failure(breaker, mock_redis):
    mock_redis.incr = AsyncMock(return_value=4)

    async def get_side_effect(key):
        if ":state" in key:
            return CircuitState.HALF_OPEN.value
        if ":opened_at" in key:
            return None
        if ":failures" in key:
            return "4"
        return None

    mock_redis.get = AsyncMock(side_effect=get_side_effect)

    new_state = await breaker.record_failure()
    assert new_state == CircuitState.OPEN


# ------------------------------------------------------------------ #
# Redis unavailable -> defaults to CLOSED
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_redis_unavailable_defaults_to_closed():
    cb = CircuitBreaker(service_name="test_service")
    cb._get_redis = AsyncMock(side_effect=aioredis.RedisError("connection refused"))

    state = await cb.get_state()
    assert state == CircuitState.CLOSED


# ------------------------------------------------------------------ #
# get_status returns correct dict
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_get_status_returns_correct_dict(breaker, mock_redis):
    opened_at = str(time.time() - 30)

    async def get_side_effect(key):
        if ":state" in key:
            return CircuitState.CLOSED.value
        if ":failures" in key:
            return "2"
        if ":opened_at" in key:
            return opened_at
        return None

    mock_redis.get = AsyncMock(side_effect=get_side_effect)

    status = await breaker.get_status()
    assert status["service"] == "test_service"
    assert status["state"] == "closed"
    assert status["failure_count"] == 2
    assert status["failure_threshold"] == 3
    assert status["recovery_timeout_seconds"] == 60
    assert status["opened_at"] == float(opened_at)


# ------------------------------------------------------------------ #
# get_status when Redis unavailable
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_get_status_redis_unavailable():
    cb = CircuitBreaker(service_name="test_service")
    cb._get_redis = AsyncMock(side_effect=aioredis.RedisError("connection refused"))

    status = await cb.get_status()
    assert status["service"] == "test_service"
    assert status["state"] == "unknown"
    assert status["failure_count"] == -1
    assert "error" in status
