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
    r.aclose = AsyncMock()
    r.script_load = AsyncMock(return_value="fake_sha")
    r.evalsha = AsyncMock(return_value="closed")
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
    cb._redis = mock_redis
    return cb


# ------------------------------------------------------------------ #
# Default state is CLOSED
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_default_state_is_closed(breaker):
    state = await breaker.get_state()
    assert state == CircuitState.CLOSED


# ------------------------------------------------------------------ #
# CLOSED -> OPEN after N failures (via Lua script)
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_closed_to_open_after_threshold_failures(breaker, mock_redis):
    mock_redis.evalsha = AsyncMock(return_value="open")

    new_state = await breaker.record_failure()
    assert new_state == CircuitState.OPEN


# ------------------------------------------------------------------ #
# Failures below threshold stay CLOSED
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_failures_below_threshold_stay_closed(breaker, mock_redis):
    mock_redis.evalsha = AsyncMock(return_value="closed")

    new_state = await breaker.record_failure()
    assert new_state == CircuitState.CLOSED


# ------------------------------------------------------------------ #
# OPEN -> HALF_OPEN after recovery timeout (via check_recovery)
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_open_to_half_open_after_timeout(breaker, mock_redis):
    opened_at = str(time.time() - 120)  # 120s ago, timeout is 60s

    async def get_side_effect(key):
        if ":state" in key:
            return CircuitState.OPEN.value
        if ":opened_at" in key:
            return opened_at
        return None

    mock_redis.get = AsyncMock(side_effect=get_side_effect)

    state = await breaker.check_recovery()
    assert state == CircuitState.HALF_OPEN
    mock_redis.set.assert_called()


# ------------------------------------------------------------------ #
# get_state is read-only (OPEN stays OPEN even after timeout)
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_get_state_is_read_only(breaker, mock_redis):
    """get_state should NOT transition OPEN -> HALF_OPEN."""
    mock_redis.get = AsyncMock(return_value=CircuitState.OPEN.value)

    state = await breaker.get_state()
    assert state == CircuitState.OPEN
    mock_redis.set.assert_not_called()


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
# HALF_OPEN -> OPEN on probe failure (via Lua script)
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_half_open_to_open_on_probe_failure(breaker, mock_redis):
    mock_redis.evalsha = AsyncMock(return_value="open")

    new_state = await breaker.record_failure()
    assert new_state == CircuitState.OPEN


# ------------------------------------------------------------------ #
# Redis unavailable -> defaults to CLOSED
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_redis_unavailable_defaults_to_closed():
    cb = CircuitBreaker(service_name="test_service")
    cb._redis = AsyncMock(spec=aioredis.Redis)
    cb._redis.get = AsyncMock(side_effect=aioredis.RedisError("connection refused"))

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
    cb._redis = AsyncMock(spec=aioredis.Redis)
    cb._redis.get = AsyncMock(side_effect=aioredis.RedisError("connection refused"))

    status = await cb.get_status()
    assert status["service"] == "test_service"
    assert status["state"] == "unknown"
    assert status["failure_count"] == -1
    assert "error" in status


# ------------------------------------------------------------------ #
# close() cleans up Redis connection
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_close_cleans_up_redis(breaker, mock_redis):
    await breaker.close()
    mock_redis.aclose.assert_called_once()
    assert breaker._redis is None
    assert breaker._lua_sha is None


# ------------------------------------------------------------------ #
# Lazy initialization: _get_redis creates client once
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_get_redis_reuses_connection():
    cb = CircuitBreaker(service_name="test_service", redis_url="redis://localhost:6379")
    with patch("src.shared.circuit_breaker.redis.Redis.from_url") as mock_from_url:
        mock_client = AsyncMock()
        mock_from_url.return_value = mock_client
        r1 = await cb._get_redis()
        r2 = await cb._get_redis()
        assert r1 is r2
        mock_from_url.assert_called_once()


# ------------------------------------------------------------------ #
# record_failure passes correct keys to Lua script
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_record_failure_uses_lua_script(breaker, mock_redis):
    mock_redis.evalsha = AsyncMock(return_value="closed")

    await breaker.record_failure()

    mock_redis.evalsha.assert_called_once()
    call_args = mock_redis.evalsha.call_args
    # Should pass 3 keys
    assert call_args[0][1] == 3
    # Keys should be failures, state, opened_at
    assert ":failures" in call_args[0][2]
    assert ":state" in call_args[0][3]
    assert ":opened_at" in call_args[0][4]
