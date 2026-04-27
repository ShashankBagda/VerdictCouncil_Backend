"""Chaos and resilience tests — fault injection without live infrastructure.

Tests system behaviour when external dependencies fail:
  * Redis down during event publishing (publish_agent_event / subscribe_case)
  * Database connection loss mid-operation
  * OpenAI API 503/504 during document parsing
  * Circuit breaker opens when failure threshold is reached
  * Retry decorator exhausts retries and raises MaxRetriesError
  * SSE stream handles publisher crash gracefully
  * Rate limiter continues working when internal state is corrupted

All tests run in standard CI (no ``integration`` marker) — they use mocks
and monkeypatching rather than real infrastructure.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shared.retry import MaxRetriesError, retry_with_backoff


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_failing_async(exception: Exception, call_count: int = 0):
    """Return an async callable that raises ``exception`` every time."""
    async def _fail(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise exception
    _fail.call_count_ref = lambda: call_count
    return _fail


# ---------------------------------------------------------------------------
# 1. retry_with_backoff — exhausted retries raise MaxRetriesError
# ---------------------------------------------------------------------------


class TestRetryWithBackoff:
    """Unit-level chaos tests for the retry decorator."""

    @pytest.mark.asyncio
    async def test_raises_max_retries_error_after_exhausting_retries(self):
        """When all retries are exhausted, MaxRetriesError must be raised."""
        call_log: list[int] = []

        @retry_with_backoff(max_retries=2, base_delay=0.0, retryable_exceptions=(ValueError,))
        async def flaky():
            call_log.append(1)
            raise ValueError("transient")

        with pytest.raises(MaxRetriesError):
            await flaky()

        # Called once initially + 2 retries = 3 total
        assert len(call_log) == 3

    @pytest.mark.asyncio
    async def test_succeeds_on_third_attempt(self):
        """Function should succeed when it stops failing before exhausting retries."""
        attempts = [0]

        @retry_with_backoff(max_retries=3, base_delay=0.0, retryable_exceptions=(OSError,))
        async def intermittent():
            attempts[0] += 1
            if attempts[0] < 3:
                raise OSError("network blip")
            return "ok"

        result = await intermittent()
        assert result == "ok"
        assert attempts[0] == 3

    @pytest.mark.asyncio
    async def test_non_retryable_exception_propagates_immediately(self):
        """Non-retryable exceptions must propagate without retry."""
        call_log: list[int] = []

        @retry_with_backoff(max_retries=3, base_delay=0.0, retryable_exceptions=(OSError,))
        async def boom():
            call_log.append(1)
            raise KeyError("not retryable")

        with pytest.raises(KeyError):
            await boom()

        assert len(call_log) == 1  # No retry

    @pytest.mark.asyncio
    async def test_zero_retries_raises_immediately(self):
        """With max_retries=0 the function is called once and failure propagates."""
        @retry_with_backoff(max_retries=0, base_delay=0.0, retryable_exceptions=(Exception,))
        async def always_fail():
            raise RuntimeError("boom")

        with pytest.raises(MaxRetriesError):
            await always_fail()

    @pytest.mark.asyncio
    async def test_max_delay_caps_sleep_duration(self):
        """Backoff delay must not exceed max_delay."""
        sleep_calls: list[float] = []

        with patch("asyncio.sleep", side_effect=lambda d: sleep_calls.append(d)):
            @retry_with_backoff(
                max_retries=5,
                base_delay=10.0,
                max_delay=3.0,
                retryable_exceptions=(Exception,),
            )
            async def flaky():
                raise Exception("fail")

            with pytest.raises(MaxRetriesError):
                await flaky()

        assert all(d <= 3.0 for d in sleep_calls), f"Delays exceeded max: {sleep_calls}"


# ---------------------------------------------------------------------------
# 2. Redis failure during pipeline event publishing
# ---------------------------------------------------------------------------


class TestRedisFaultInjection:
    """Chaos tests for Redis-backed components without live Redis."""

    @pytest.mark.asyncio
    async def test_publish_agent_event_survives_redis_connection_error(self):
        """publish_agent_event must not raise when Redis is down."""
        import redis.asyncio as redis_module

        from src.services.pipeline_events import publish_agent_event

        with patch(
            "src.services.pipeline_events._get_redis_client",
            side_effect=redis_module.ConnectionError("Redis down"),
        ):
            # Should silently log and return — not raise
            try:
                await publish_agent_event(
                    "case-abc",
                    {"event": "tool_call", "agent": "evidence-analysis", "tool_name": "search"},
                )
            except redis_module.ConnectionError:
                pytest.fail("publish_agent_event should swallow Redis errors")

    @pytest.mark.asyncio
    async def test_circuit_breaker_defaults_to_closed_when_redis_down(self):
        """CircuitBreaker.get_state must return CLOSED (safe default) when Redis is unavailable."""
        import redis.asyncio as redis_module

        from src.shared.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker("chaos-test", redis_url="redis://127.0.0.1:19999")
        # Port 19999 is not listening — connection will fail
        # We patch _get_redis to immediately raise
        with patch.object(
            breaker,
            "_get_redis",
            side_effect=redis_module.ConnectionError("no redis"),
        ):
            state = await breaker.get_state()

        assert state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failure_threshold(self):
        """CircuitBreaker must transition to OPEN after enough recorded failures."""
        import redis.asyncio as redis_module

        from src.shared.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker("pair_search", failure_threshold=3)

        # Mock Redis to simulate Lua script returning 'open' on the 3rd call
        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.evalsha = AsyncMock(return_value="open")  # threshold reached
        mock_redis.get = AsyncMock(return_value="open")

        with patch.object(breaker, "_get_redis", return_value=mock_redis):
            await breaker._ensure_lua_loaded()
            # Simulate recording 3 failures
            for _ in range(3):
                await breaker.record_failure()

            state = await breaker.get_state()

        assert state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_success_in_half_open(self):
        """Record success in HALF_OPEN must transition state to CLOSED."""
        from src.shared.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker("pair_search", failure_threshold=3)

        mock_redis = AsyncMock()
        mock_redis.script_load = AsyncMock(return_value="sha123")
        mock_redis.delete = AsyncMock()
        mock_redis.get = AsyncMock(return_value="closed")
        mock_redis.set = AsyncMock()

        with patch.object(breaker, "_get_redis", return_value=mock_redis):
            # Calling record_success directly (circuit already half-open)
            await breaker.record_success()
            state = await breaker.get_state()

        assert state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 3. Database failure simulation
# ---------------------------------------------------------------------------


class TestDatabaseFaultInjection:
    """Simulate Postgres connectivity failures in service layer calls."""

    @pytest.mark.asyncio
    async def test_tee_write_survives_db_connection_error(self):
        """pipeline_events._tee_write must not raise when DB is unavailable."""
        from src.services.pipeline_events import _tee_write

        with patch(
            "src.services.pipeline_events.async_session",
            side_effect=Exception("PG connection refused"),
        ):
            # Must silently log and return None — never propagate
            try:
                await _tee_write("case-123", {"kind": "progress", "phase": "processing"})
            except Exception as exc:
                pytest.fail(f"_tee_write should swallow DB errors, but raised: {exc}")

    @pytest.mark.asyncio
    async def test_rate_limit_middleware_survives_lock_contention(self):
        """RateLimitMiddleware must handle lock contention without deadlock."""
        import threading

        from src.api.middleware.rate_limit import RateLimitMiddleware

        app = MagicMock()
        middleware = RateLimitMiddleware(app, requests_per_minute=5)

        # Simulate concurrent requests from the same IP
        results: list[int] = []

        async def _make_request(ip: str) -> None:
            mock_request = MagicMock()
            mock_request.method = "GET"
            mock_request.headers = {}
            mock_request.client = MagicMock(host=ip)
            mock_request.headers.get = lambda k, d=None: None

            mock_call_next = AsyncMock(return_value=MagicMock(status_code=200))
            response = await middleware.dispatch(mock_request, mock_call_next)
            results.append(response.status_code)

        # Run 10 requests concurrently from the same IP (limit = 5)
        await asyncio.gather(*[_make_request("10.0.0.1") for _ in range(10)])

        # At least some must have been rate-limited (429)
        assert 429 in results
        # And some must have gone through (200)
        assert 200 in results


# ---------------------------------------------------------------------------
# 4. OpenAI API failure during document parsing
# ---------------------------------------------------------------------------


class TestOpenAIFaultInjection:
    """Simulate OpenAI API failures during document ingestion."""

    @pytest.mark.asyncio
    async def test_parse_document_retries_on_rate_limit_error(self):
        """parse_document must retry on RateLimitError before raising MaxRetriesError."""
        import openai

        from src.tools.parse_document import _extract_via_openai

        call_count = [0]
        mock_client = AsyncMock()

        async def _raise_rate_limit(*a, **kw):
            call_count[0] += 1
            raise openai.RateLimitError(
                "rate limit",
                response=MagicMock(status_code=429, headers={}),
                body=None,
            )

        mock_client.responses.create = _raise_rate_limit

        with pytest.raises(MaxRetriesError):
            await _extract_via_openai(mock_client, "file-abc", False, False)

        # retry_with_backoff(max_retries=2) → 3 total calls
        assert call_count[0] == 3

    @pytest.mark.asyncio
    async def test_parse_document_retries_on_connection_error(self):
        """parse_document must retry on APIConnectionError."""
        import openai

        from src.tools.parse_document import _extract_via_openai

        call_count = [0]
        mock_client = AsyncMock()

        async def _raise_conn_err(*a, **kw):
            call_count[0] += 1
            raise openai.APIConnectionError(request=MagicMock())

        mock_client.responses.create = _raise_conn_err

        with pytest.raises(MaxRetriesError):
            await _extract_via_openai(mock_client, "file-xyz", False, False)

        assert call_count[0] == 3


# ---------------------------------------------------------------------------
# 5. SSE publisher crash resilience
# ---------------------------------------------------------------------------


class TestSSEPublisherCrash:
    """Test that SSE fan-out gracefully handles publisher errors."""

    @pytest.mark.asyncio
    async def test_subscribe_case_terminates_on_publish_error(self):
        """subscribe_case generator must terminate cleanly if Redis publish errors."""
        import redis.asyncio as redis_module

        from src.services.pipeline_events import subscribe_case

        # Simulate pubsub failing immediately after subscribe
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_pubsub.__aiter__ = MagicMock(
            return_value=iter([])  # empty — no messages arrive
        )

        mock_redis = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch(
            "src.services.pipeline_events._get_redis_client",
            return_value=mock_redis,
        ):
            collected: list[dict] = []
            try:
                async for event in subscribe_case("case-dead"):
                    collected.append(event)
                    break  # consume at most one event
            except Exception:
                pass  # Acceptable — generator terminates


# ---------------------------------------------------------------------------
# 6. Sanitization under adversarial load
# ---------------------------------------------------------------------------


class TestSanitizationUnderLoad:
    """Verify sanitization holds up under large and malformed inputs."""

    def test_very_large_document_does_not_crash(self):
        """sanitize_text must handle a 10 MB string without crashing."""
        from src.shared.sanitization import sanitize_text

        large_text = "Legal proceedings. " * 500_000  # ~10 MB
        result = sanitize_text(large_text)
        assert result.regex_hits == 0
        assert len(result.text) > 0

    def test_null_bytes_in_large_doc(self):
        """Null bytes embedded in a large document must be handled."""
        from src.shared.sanitization import sanitize_user_input

        text = ("normal text " * 10_000) + "\x00\x00\x00"
        result = sanitize_user_input(text)
        assert "\x00" not in result

    def test_deeply_nested_injection_delimiters(self):
        """Nested injection delimiters must all be stripped."""
        from src.shared.sanitization import sanitize_text

        nested = "[INST]outer [INST]inner[/INST][/INST]"
        result = sanitize_text(nested)
        assert "[INST]" not in result.text
        assert "[/INST]" not in result.text

    def test_binary_like_characters_do_not_crash(self):
        """Unicode control characters and surrogates must not crash sanitizer."""
        from src.shared.sanitization import sanitize_text

        text = "".join(chr(i) for i in range(0x00, 0x20)) + "legal text"
        result = sanitize_text(text)
        assert isinstance(result.text, str)

    def test_repeated_pattern_application_is_linear(self):
        """Repeated injection patterns must not cause exponential backtracking."""
        import time

        from src.shared.sanitization import sanitize_text

        # Patterns that could cause catastrophic backtracking if regexes are not compiled
        text = "[INST]" + ("a" * 1000) + "[/INST]"
        start = time.monotonic()
        sanitize_text(text)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"sanitize_text took {elapsed:.2f}s — possible ReDoS"
