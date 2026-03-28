"""Integration tests for the distributed SAM pipeline.

These tests require Solace and Redis running (via docker-compose.infra.yml).
They are skipped in CI unless INTEGRATION_TESTS=1 is set.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Integration tests require infrastructure (set INTEGRATION_TESTS=1)",
)


class TestDistributedPipeline:
    async def test_placeholder(self):
        """Placeholder — full distributed pipeline test requires Solace."""
        pytest.skip("Not yet implemented — requires SAM runtime")
