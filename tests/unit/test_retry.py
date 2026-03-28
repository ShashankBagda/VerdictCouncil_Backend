import pytest

from src.shared.retry import MaxRetriesError, retry_with_backoff


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01)
        async def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await succeed()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_transient_failure_then_success(self):
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01)
        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("transient")
            return "ok"

        result = await fail_then_succeed()
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        @retry_with_backoff(max_retries=2, base_delay=0.01)
        async def always_fail():
            raise ConnectionError("permanent")

        with pytest.raises(MaxRetriesError):
            await always_fail()

    @pytest.mark.asyncio
    async def test_non_retryable_exception_not_retried(self):
        call_count = 0

        @retry_with_backoff(max_retries=2, base_delay=0.01, retryable_exceptions=(ConnectionError,))
        async def raise_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            await raise_value_error()
        assert call_count == 1
