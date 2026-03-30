"""Async Redis-backed circuit breaker for external API resilience."""

import logging
import time
from enum import Enum

import redis.asyncio as redis

from src.shared.config import settings

logger = logging.getLogger(__name__)

# Lua script for atomic record_failure.
# KEYS[1] = failures key, KEYS[2] = state key, KEYS[3] = opened_at key
# ARGV[1] = failure_threshold, ARGV[2] = current timestamp
_LUA_RECORD_FAILURE = """
local failures = redis.call('INCR', KEYS[1])
local state = redis.call('GET', KEYS[2]) or 'closed'

if state == 'half_open' then
    redis.call('SET', KEYS[2], 'open')
    redis.call('SET', KEYS[3], ARGV[2])
    return 'open'
end

if failures >= tonumber(ARGV[1]) then
    redis.call('SET', KEYS[2], 'open')
    redis.call('SET', KEYS[3], ARGV[2])
    return 'open'
end

return 'closed'
"""


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Redis-backed circuit breaker.

    Args:
        service_name: Identifier for the service (e.g., "pair_search")
        failure_threshold: Number of consecutive failures to open circuit (default: 3)
        recovery_timeout: Seconds before OPEN transitions to HALF_OPEN (default: 60)
        redis_url: Redis connection URL (defaults to settings.redis_url)
    """

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 3,
        recovery_timeout: int = 60,
        redis_url: str | None = None,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._redis_url = redis_url or settings.redis_url
        self._key_prefix = f"vc:circuit:{service_name}"
        self._redis: redis.Redis | None = None
        self._lua_sha: str | None = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
            self._lua_sha = None

    async def _ensure_lua_loaded(self) -> str:
        """Load the Lua script into Redis and cache its SHA."""
        if self._lua_sha is None:
            r = await self._get_redis()
            self._lua_sha = await r.script_load(_LUA_RECORD_FAILURE)
        return self._lua_sha

    async def get_state(self) -> CircuitState:
        """Get current circuit state (read-only, no side effects)."""
        try:
            r = await self._get_redis()
            state = await r.get(f"{self._key_prefix}:state")
            if state == CircuitState.OPEN.value:
                return CircuitState.OPEN
            if state == CircuitState.HALF_OPEN.value:
                return CircuitState.HALF_OPEN
            return CircuitState.CLOSED
        except redis.RedisError:
            logger.warning("Redis unavailable for circuit breaker; defaulting to CLOSED")
            return CircuitState.CLOSED

    async def check_recovery(self) -> CircuitState:
        """Check if OPEN circuit should transition to HALF_OPEN.

        Call this before making a request to determine if a probe attempt
        is allowed. Transitions OPEN -> HALF_OPEN when the recovery
        timeout has elapsed.
        """
        try:
            r = await self._get_redis()
            state = await r.get(f"{self._key_prefix}:state")
            if state == CircuitState.OPEN.value:
                opened_at = await r.get(f"{self._key_prefix}:opened_at")
                if opened_at and (time.time() - float(opened_at)) >= self.recovery_timeout:
                    await r.set(f"{self._key_prefix}:state", CircuitState.HALF_OPEN.value)
                    return CircuitState.HALF_OPEN
                return CircuitState.OPEN
            if state == CircuitState.HALF_OPEN.value:
                return CircuitState.HALF_OPEN
            return CircuitState.CLOSED
        except redis.RedisError:
            logger.warning("Redis unavailable for circuit breaker; defaulting to CLOSED")
            return CircuitState.CLOSED

    async def record_success(self) -> None:
        """Record a successful call. Resets failure count and closes circuit."""
        try:
            r = await self._get_redis()
            pipe = r.pipeline()
            pipe.set(f"{self._key_prefix}:state", CircuitState.CLOSED.value)
            pipe.set(f"{self._key_prefix}:failures", 0)
            pipe.delete(f"{self._key_prefix}:opened_at")
            await pipe.execute()
        except redis.RedisError:
            logger.warning("Redis unavailable; cannot record circuit breaker success")

    async def record_failure(self) -> CircuitState:
        """Record a failed call atomically via Lua script. Returns the new state."""
        try:
            r = await self._get_redis()
            sha = await self._ensure_lua_loaded()
            result = await r.evalsha(
                sha,
                3,
                f"{self._key_prefix}:failures",
                f"{self._key_prefix}:state",
                f"{self._key_prefix}:opened_at",
                str(self.failure_threshold),
                str(time.time()),
            )
            new_state_str = result if isinstance(result, str) else result.decode()
            new_state = CircuitState(new_state_str)

            if new_state == CircuitState.OPEN:
                logger.warning(
                    "Circuit breaker %s -> OPEN",
                    self.service_name,
                )
            return new_state
        except redis.RedisError:
            logger.warning("Redis unavailable; cannot record circuit breaker failure")
            return CircuitState.CLOSED

    async def get_status(self) -> dict:
        """Get full circuit breaker status for health endpoint."""
        try:
            r = await self._get_redis()
            state = await self.get_state()
            failures = int(await r.get(f"{self._key_prefix}:failures") or 0)
            opened_at = await r.get(f"{self._key_prefix}:opened_at")
            return {
                "service": self.service_name,
                "state": state.value,
                "failure_count": failures,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout_seconds": self.recovery_timeout,
                "opened_at": float(opened_at) if opened_at else None,
            }
        except redis.RedisError:
            return {
                "service": self.service_name,
                "state": "unknown",
                "failure_count": -1,
                "error": "Redis unavailable",
            }


# --------------------------------------------------------------------------- #
# Shared singleton for PAIR Search API circuit breaker
# --------------------------------------------------------------------------- #

_pair_search_breaker: CircuitBreaker | None = None


def get_pair_search_breaker() -> CircuitBreaker:
    """Return the shared PAIR Search circuit breaker instance."""
    global _pair_search_breaker
    if _pair_search_breaker is None:
        _pair_search_breaker = CircuitBreaker(
            service_name="pair_search",
            failure_threshold=settings.pair_circuit_breaker_threshold,
            recovery_timeout=settings.pair_circuit_breaker_timeout,
        )
    return _pair_search_breaker
