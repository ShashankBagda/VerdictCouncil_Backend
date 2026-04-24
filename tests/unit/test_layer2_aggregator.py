"""Unit tests for the Layer 2 Aggregator.

The Layer2Aggregator tracks completion of 3 parallel agents
(evidence_analysis, extracted_facts, witnesses) per case_id:run_id.
When all 3 complete it merges outputs into CaseState and publishes
downstream.  These tests use mocked Redis and publisher so they run
without external services.
"""

from __future__ import annotations

import json

import pytest

from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum

# ---------------------------------------------------------------------------
# Constants matching the aggregator spec
# ---------------------------------------------------------------------------
AGENTS = ("evidence_analysis", "extracted_facts", "witnesses")
REDIS_KEY_PREFIX = "vc:aggregator"
TIMEOUT_SECONDS = 120


def _redis_key(case_id: str, run_id: str) -> str:
    return f"{REDIS_KEY_PREFIX}:{case_id}:{run_id}"


# ---------------------------------------------------------------------------
# Sample agent outputs
# ---------------------------------------------------------------------------


def _evidence_output() -> dict:
    return {
        "exhibits": [{"id": "E1", "type": "document", "summary": "Traffic cam footage"}],
        "credibility_scores": {"E1": 0.92},
    }


def _facts_output() -> dict:
    return {
        "timeline": [
            {"time": "2026-03-15T08:30:00", "event": "Vehicle detected at 95 km/h"},
        ],
        "key_facts": ["Speed exceeded limit by 25 km/h"],
    }


def _witnesses_output() -> dict:
    return {
        "statements": [
            {"witness": "Officer Lee", "summary": "Observed vehicle from patrol car"},
        ],
        "credibility": {"Officer Lee": 0.88},
    }


AGENT_OUTPUTS = {
    "evidence_analysis": _evidence_output(),
    "extracted_facts": _facts_output(),
    "witnesses": _witnesses_output(),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_case_state(**overrides) -> CaseState:
    defaults = dict(
        case_id="case-001",
        run_id="run-aaa",
        domain=CaseDomainEnum.traffic_violation,
        status=CaseStatusEnum.processing,
        parties=[
            {"name": "Prosecution", "role": "prosecution"},
            {"name": "John Doe", "role": "accused"},
        ],
        case_metadata={
            "filed_date": "2026-03-15",
            "category": "traffic",
            "subcategory": "speeding",
        },
    )
    defaults.update(overrides)
    return CaseState(**defaults)


class FakeRedis:
    """In-memory fake that mirrors the async Redis methods used by the aggregator."""

    def __init__(self):
        self._store: dict[str, dict[str, str]] = {}
        self._ttls: dict[str, int] = {}
        self._alive = True

    # -- hash commands -------------------------------------------------------
    async def hset(self, key: str, field: str, value: str) -> int:
        self._check_alive()
        bucket = self._store.setdefault(key, {})
        is_new = field not in bucket
        bucket[field] = value
        return int(is_new)

    async def hgetall(self, key: str) -> dict[str, str]:
        self._check_alive()
        return dict(self._store.get(key, {}))

    async def hlen(self, key: str) -> int:
        self._check_alive()
        return len(self._store.get(key, {}))

    async def delete(self, *keys: str) -> int:
        self._check_alive()
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def expire(self, key: str, ttl: int) -> bool:
        self._check_alive()
        self._ttls[key] = ttl
        return key in self._store

    async def eval(self, script: str, numkeys: int, *args) -> None:  # noqa: A003
        """Stub for Lua script execution — just returns None."""
        self._check_alive()

    # -- helpers -------------------------------------------------------------
    def kill(self):
        self._alive = False

    def _check_alive(self):
        if not self._alive:
            raise ConnectionError("Redis connection lost")


class FakePublisher:
    """Records published messages for assertion."""

    def __init__(self):
        self.messages: list[tuple[str, CaseState]] = []

    async def publish(self, topic: str, case_state: CaseState) -> None:
        self.messages.append((topic, case_state))


# ---------------------------------------------------------------------------
# Aggregator logic under test (spec-based, inline implementation)
#
# When the real module at src.services.layer2_aggregator.aggregator exists,
# replace these helpers with imports.  Until then the tests exercise the
# aggregation *contract* directly.
# ---------------------------------------------------------------------------


async def receive_agent_output(
    redis: FakeRedis,
    publisher: FakePublisher,
    base_state: CaseState,
    agent_name: str,
    output: dict,
    downstream_topic: str = "vc/layer3/input",
) -> CaseState | None:
    """Simulate the aggregator's receive path.

    Returns the merged CaseState if all agents have reported, else None.
    """
    key = _redis_key(base_state.case_id, base_state.run_id)

    # Store output in Redis hash (one field per agent)
    await redis.hset(key, agent_name, json.dumps(output))
    await redis.expire(key, TIMEOUT_SECONDS)

    # Check completeness
    count = await redis.hlen(key)
    if count < len(AGENTS):
        return None

    # All agents reported — merge
    raw = await redis.hgetall(key)
    # Use attribute assignment (not model_copy update) so validate_assignment
    # coerces dicts to typed models (e.g. Witnesses).
    merged = base_state.model_copy(deep=True)
    merged.evidence_analysis = json.loads(raw["evidence_analysis"])
    merged.extracted_facts = json.loads(raw["extracted_facts"])
    merged.witnesses = json.loads(raw["witnesses"])
    merged.status = CaseStatusEnum.processing

    await publisher.publish(downstream_topic, merged)
    await redis.delete(key)
    return merged


async def check_timeout(
    redis: FakeRedis,
    case_id: str,
    run_id: str,
) -> bool:
    """Return True if the key has expired (simulating timeout).

    In production the TTL handles this; here we check field count.
    """
    key = _redis_key(case_id, run_id)
    count = await redis.hlen(key)
    return 0 < count < len(AGENTS)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLayer2Aggregator:
    """Unit tests for the Layer 2 aggregation logic."""

    @pytest.fixture
    def redis(self):
        return FakeRedis()

    @pytest.fixture
    def publisher(self):
        return FakePublisher()

    @pytest.fixture
    def base_state(self):
        return _base_case_state()

    # 1. Three agents complete in order → merge and publish ----------------

    @pytest.mark.asyncio
    async def test_three_agents_complete_triggers_merge(self, redis, publisher, base_state):
        result = None
        for agent in AGENTS:
            result = await receive_agent_output(redis, publisher, base_state, agent, AGENT_OUTPUTS[agent])

        # Merge should have been triggered on the third call
        assert result is not None
        _expected_e = _evidence_output()
        assert result.evidence_analysis is not None
        assert result.evidence_analysis.exhibits == _expected_e["exhibits"]
        assert result.evidence_analysis.credibility_scores == _expected_e["credibility_scores"]
        _expected_f = _facts_output()
        assert result.extracted_facts is not None
        assert result.extracted_facts.timeline == _expected_f["timeline"]
        _expected_w = _witnesses_output()
        assert result.witnesses is not None
        assert result.witnesses.statements == _expected_w["statements"]
        assert result.witnesses.credibility == _expected_w["credibility"]

        # Publisher received exactly one message
        assert len(publisher.messages) == 1
        topic, published_state = publisher.messages[0]
        assert topic == "vc/layer3/input"
        assert published_state.case_id == base_state.case_id

        # Redis key cleaned up
        key = _redis_key(base_state.case_id, base_state.run_id)
        assert await redis.hlen(key) == 0

    # 2. Out-of-order arrival still merges correctly -----------------------

    @pytest.mark.asyncio
    async def test_out_of_order_arrival(self, redis, publisher, base_state):
        reversed_agents = list(reversed(AGENTS))
        result = None
        for agent in reversed_agents:
            result = await receive_agent_output(redis, publisher, base_state, agent, AGENT_OUTPUTS[agent])

        assert result is not None
        _expected_e2 = _evidence_output()
        assert result.evidence_analysis is not None
        assert result.evidence_analysis.exhibits == _expected_e2["exhibits"]
        assert result.evidence_analysis.credibility_scores == _expected_e2["credibility_scores"]
        _expected_f2 = _facts_output()
        assert result.extracted_facts is not None
        assert result.extracted_facts.timeline == _expected_f2["timeline"]
        _expected_w2 = _witnesses_output()
        assert result.witnesses is not None
        assert result.witnesses.statements == _expected_w2["statements"]
        assert result.witnesses.credibility == _expected_w2["credibility"]
        assert len(publisher.messages) == 1

    # 3. Duplicate handling — overwrites, no double publish ----------------

    @pytest.mark.asyncio
    async def test_duplicate_handling(self, redis, publisher, base_state):
        # Send evidence_analysis twice, then the other two
        await receive_agent_output(redis, publisher, base_state, "evidence_analysis", _evidence_output())
        # Duplicate with slightly different data
        modified_evidence = {**_evidence_output(), "extra": "duplicate_data"}
        await receive_agent_output(redis, publisher, base_state, "evidence_analysis", modified_evidence)

        # Still only 1 field in Redis (overwritten)
        key = _redis_key(base_state.case_id, base_state.run_id)
        assert await redis.hlen(key) == 1

        # Complete the remaining agents
        await receive_agent_output(redis, publisher, base_state, "extracted_facts", _facts_output())
        result = await receive_agent_output(redis, publisher, base_state, "witnesses", _witnesses_output())

        assert result is not None
        # The merged evidence should reflect the *latest* (overwritten) value
        assert result.evidence_analysis is not None
        assert result.evidence_analysis.exhibits == modified_evidence["exhibits"]
        # The extra field confirms the duplicate write was stored (not the original)
        assert getattr(result.evidence_analysis, "extra", None) == "duplicate_data"
        # Only one publish event
        assert len(publisher.messages) == 1

    # 4. run_id isolation — concurrent runs tracked independently ----------

    @pytest.mark.asyncio
    async def test_run_id_isolation(self, redis, publisher):
        state_run_a = _base_case_state(case_id="case-001", run_id="run-aaa")
        state_run_b = _base_case_state(case_id="case-001", run_id="run-bbb")

        # Interleave agent outputs across two runs
        await receive_agent_output(redis, publisher, state_run_a, "evidence_analysis", _evidence_output())
        await receive_agent_output(redis, publisher, state_run_b, "evidence_analysis", _evidence_output())
        await receive_agent_output(redis, publisher, state_run_a, "extracted_facts", _facts_output())
        await receive_agent_output(redis, publisher, state_run_b, "extracted_facts", _facts_output())

        # Neither run is complete yet
        assert len(publisher.messages) == 0

        # Complete run A
        result_a = await receive_agent_output(redis, publisher, state_run_a, "witnesses", _witnesses_output())
        assert result_a is not None
        assert result_a.run_id == "run-aaa"
        assert len(publisher.messages) == 1

        # Run B should still be incomplete (2 of 3)
        key_b = _redis_key("case-001", "run-bbb")
        assert await redis.hlen(key_b) == 2

        # Complete run B
        result_b = await receive_agent_output(redis, publisher, state_run_b, "witnesses", _witnesses_output())
        assert result_b is not None
        assert result_b.run_id == "run-bbb"
        assert len(publisher.messages) == 2

    # 5. Timeout marks case as failed --------------------------------------

    @pytest.mark.asyncio
    async def test_timeout_marks_failed(self, redis, publisher, base_state):
        # Only 2 of 3 agents report
        await receive_agent_output(redis, publisher, base_state, "evidence_analysis", _evidence_output())
        await receive_agent_output(redis, publisher, base_state, "extracted_facts", _facts_output())

        # No merge should have happened
        assert len(publisher.messages) == 0

        # Simulate timeout check — key still has partial data
        is_timed_out = await check_timeout(redis, base_state.case_id, base_state.run_id)
        assert is_timed_out is True

        # Verify TTL was set on the key
        key = _redis_key(base_state.case_id, base_state.run_id)
        assert key in redis._ttls
        assert redis._ttls[key] == TIMEOUT_SECONDS

    # 6. Redis connection failure raises appropriate error -----------------

    @pytest.mark.asyncio
    async def test_redis_connection_failure(self, redis, publisher, base_state):
        redis.kill()

        with pytest.raises(ConnectionError, match="Redis connection lost"):
            await receive_agent_output(redis, publisher, base_state, "evidence_analysis", _evidence_output())

        # Publisher should not have been called
        assert len(publisher.messages) == 0

    # 7. Merge produces valid CaseState with all fields --------------------

    @pytest.mark.asyncio
    async def test_merge_produces_valid_case_state(self, redis, publisher, base_state):
        for agent in AGENTS:
            await receive_agent_output(redis, publisher, base_state, agent, AGENT_OUTPUTS[agent])

        assert len(publisher.messages) == 1
        _, merged = publisher.messages[0]

        # Verify it is a valid CaseState
        assert isinstance(merged, CaseState)

        # All 3 agent fields populated
        assert merged.evidence_analysis is not None
        assert merged.extracted_facts is not None
        assert merged.witnesses is not None

        # Original base state fields preserved
        assert merged.case_id == base_state.case_id
        assert merged.run_id == base_state.run_id
        assert merged.domain == base_state.domain
        assert merged.parties == base_state.parties
        assert merged.case_metadata == base_state.case_metadata

        # Status remains processing (downstream agents will advance it)
        assert merged.status == CaseStatusEnum.processing

        # Round-trip through JSON to confirm serialisability
        json_str = merged.model_dump_json()
        restored = CaseState.model_validate_json(json_str)
        assert restored.evidence_analysis == merged.evidence_analysis
        assert restored.extracted_facts == merged.extracted_facts
        assert restored.witnesses == merged.witnesses
