"""Async Redis-backed circuit breaker for external API resilience."""

import logging
import time
from enum import Enum

import redis.asyncio as redis

from src.shared.config import settings

logger = logging.getLogger(__name__)


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

    async def _get_redis(self) -> redis.Redis:
        return redis.Redis.from_url(self._redis_url, decode_responses=True)

    async def get_state(self) -> CircuitState:
        """Get current circuit state."""
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
        """Record a failed call. Returns the new circuit state."""
        try:
            r = await self._get_redis()
            failures = await r.incr(f"{self._key_prefix}:failures")
            current_state = await self.get_state()

            if current_state == CircuitState.HALF_OPEN:
                pipe = r.pipeline()
                pipe.set(f"{self._key_prefix}:state", CircuitState.OPEN.value)
                pipe.set(f"{self._key_prefix}:opened_at", str(time.time()))
                await pipe.execute()
                logger.warning(
                    "Circuit breaker %s: HALF_OPEN -> OPEN (probe failed)",
                    self.service_name,
                )
                return CircuitState.OPEN

            if failures >= self.failure_threshold:
                pipe = r.pipeline()
                pipe.set(f"{self._key_prefix}:state", CircuitState.OPEN.value)
                pipe.set(f"{self._key_prefix}:opened_at", str(time.time()))
                await pipe.execute()
                logger.warning(
                    "Circuit breaker %s: CLOSED -> OPEN after %d failures",
                    self.service_name,
                    failures,
                )
                return CircuitState.OPEN

            return CircuitState.CLOSED
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
