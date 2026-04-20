"""Layer 2 Fan-In Aggregator for VerdictCouncil.

Subscribes to response topics from Evidence Analysis, Fact Reconstruction,
and Witness Analysis agents. Tracks completion per case_id:run_id in Redis.
When all 3 agents complete, merges outputs into a unified CaseState and
publishes to the Legal Knowledge agent's input topic.

Uses a Redis Lua script for atomic check-and-publish to prevent
duplicate downstream messages.
"""

import copy
import json
import logging
import time
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Lua script for atomic barrier check.
# KEYS[1] = hash key (vc:aggregator:{case_id}:{run_id})
# KEYS[2] = created-timestamp key (vc:aggregator:{case_id}:{run_id}:created)
# ARGV[1] = agent_key
# ARGV[2] = agent output JSON
# ARGV[3] = current timestamp
# ARGV[4] = full CaseState JSON (for original state storage)
_LUA_RECEIVE = """
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('HSET', KEYS[1], ARGV[1] .. '_ts', ARGV[3])
if redis.call('EXISTS', KEYS[2]) == 0 then
    redis.call('SET', KEYS[2], ARGV[3])
    redis.call('EXPIRE', KEYS[2], 300)
end
-- Store original CaseState on first receipt
if redis.call('HEXISTS', KEYS[1], '_original_case_state') == 0 then
    redis.call('HSET', KEYS[1], '_original_case_state', ARGV[4])
end
-- Check if all required agents have reported
local fields = redis.call('HKEYS', KEYS[1])
local agent_count = 0
for _, f in ipairs(fields) do
    if not string.find(f, '_ts$') and f ~= '_original_case_state' and f ~= '_published' then
        agent_count = agent_count + 1
    end
end
if agent_count >= 3 and redis.call('HEXISTS', KEYS[1], '_published') == 0 then
    redis.call('HSET', KEYS[1], '_published', '1')
    return 1
end
return 0
"""


class Layer2Aggregator:
    """Stateful barrier that waits for 3 agent outputs before forwarding.

    The Redis key includes both case_id and run_id to isolate concurrent
    pipeline executions (e.g., what-if scenario runs) from each other.
    """

    REQUIRED_AGENTS = frozenset(
        [
            "evidence_analysis",
            "extracted_facts",
            "witnesses",
        ]
    )
    TIMEOUT_SECONDS = 120
    REDIS_KEY_PREFIX = "vc:aggregator:"
    REDIS_TTL = 300

    def __init__(self, redis_client: redis.Redis, publisher: Any = None):
        """Initialize the aggregator.

        Args:
            redis_client: Connected Redis client instance.
            publisher: Callable or object with a publish(topic, payload) method
                       for sending the merged CaseState downstream.
        """
        self.redis = redis_client
        self.publisher = publisher
        self._lua_sha: str | None = None

    def _key(self, case_id: str, run_id: str) -> str:
        """Redis key scoped to both case and run to isolate concurrent executions."""
        return f"{self.REDIS_KEY_PREFIX}{case_id}:{run_id}"

    async def _ensure_lua_loaded(self) -> str:
        """Load the Lua script into Redis and cache its SHA."""
        if self._lua_sha is None:
            self._lua_sha = await self.redis.script_load(_LUA_RECEIVE)
        return self._lua_sha

    async def receive_output(
        self,
        agent_key: str,
        case_id: str,
        run_id: str,
        output: dict,
        base_state: dict,
    ) -> dict | None:
        """Store an agent's output. Returns merged CaseState if barrier is met.

        Uses a Redis Lua script for atomic check-and-publish to prevent
        duplicate publishes when multiple agents complete near-simultaneously.

        Args:
            agent_key: One of 'evidence_analysis', 'extracted_facts', 'witnesses'.
            case_id: The case identifier.
            run_id: UUID for this pipeline execution (or scenario_id for what-if runs).
            output: The agent's output — the CaseState fragment for this agent's field.
            base_state: The full CaseState as received from the upstream agent.
                        Stored on first receipt to serve as the merge base.

        Returns:
            Merged CaseState dict if all 3 agents have completed, else None.
        """
        if agent_key not in self.REQUIRED_AGENTS:
            raise ValueError(f"Unknown agent_key: {agent_key}")

        key = self._key(case_id, run_id)
        now = str(time.time())

        ready = await self.redis.evalsha(
            await self._ensure_lua_loaded(),
            2,
            key,
            key + ":created",
            agent_key,
            json.dumps(output),
            now,
            json.dumps(base_state),
        )

        if ready == 1:
            merged = await self._merge_and_cleanup(case_id, run_id)
            if self.publisher is not None:
                topic = "verdictcouncil/a2a/v1/agent/request/legal-knowledge"
                logger.info(
                    "Barrier met for case_id=%s run_id=%s — publishing to %s",
                    case_id,
                    run_id,
                    topic,
                )
                self.publisher.publish(topic, merged)
            return merged

        return None

    async def _check_and_merge(self, case_id: str, run_id: str) -> bool:
        """Manually check if all agents have reported and merge if ready.

        Returns True if the barrier was met and the merged state was published.
        """
        key = self._key(case_id, run_id)
        all_data = await self.redis.hgetall(key)
        if not all_data:
            return False

        stored_agents = self._extract_agent_keys(all_data)
        if stored_agents >= self.REQUIRED_AGENTS:
            merged = await self._merge_and_cleanup(case_id, run_id)
            if self.publisher is not None:
                topic = "verdictcouncil/a2a/v1/agent/request/legal-knowledge"
                self.publisher.publish(topic, merged)
            return True
        return False

    async def check_timeouts(self) -> None:
        """Scan for stale aggregator entries and fail timed-out cases.

        Iterates over all vc:aggregator:*:created keys. For each that has
        exceeded TIMEOUT_SECONDS, determines which agents are missing,
        logs the failure, and cleans up Redis state.

        The caller is responsible for updating case status to 'failed'
        in the database and notifying the gateway.
        """
        pattern = f"{self.REDIS_KEY_PREFIX}*:created"
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            for created_key in keys:
                created_key_str = (
                    created_key.decode() if isinstance(created_key, bytes) else created_key
                )
                created_raw = await self.redis.get(created_key_str)
                if not created_raw:
                    continue

                created = float(created_raw)
                if time.time() - created <= self.TIMEOUT_SECONDS:
                    continue

                # Extract case_id:run_id from the created key
                # Key format: vc:aggregator:{case_id}:{run_id}:created
                suffix = created_key_str[len(self.REDIS_KEY_PREFIX) :]
                parts = suffix.rsplit(":created", 1)[0]
                hash_key = f"{self.REDIS_KEY_PREFIX}{parts}"

                all_data = await self.redis.hgetall(hash_key)
                stored_agents = self._extract_agent_keys(all_data)
                missing = self.REQUIRED_AGENTS - stored_agents

                logger.error(
                    "Layer2Aggregator TIMEOUT for %s. "
                    "Missing agents: %s. Setting case status to FAILED.",
                    parts,
                    sorted(missing),
                )

                # Cleanup Redis state — do NOT publish partial results.
                # Incomplete analysis is worse than no analysis in a judicial context.
                await self.redis.delete(hash_key)
                await self.redis.delete(created_key_str)

            if cursor == 0:
                break

    async def _merge_and_cleanup(self, case_id: str, run_id: str) -> dict:
        """Merge agent outputs into the original CaseState.

        Deep-copies the original CaseState received at pipeline entry, then
        updates only the three designated fields (evidence_analysis,
        extracted_facts, witnesses) from the agent outputs. All other
        CaseState fields (case_id, domain, parties, raw_documents, etc.)
        are preserved.
        """
        key = self._key(case_id, run_id)
        all_data = await self.redis.hgetall(key)

        # Recover the original CaseState stored on first receipt
        original_raw = all_data.get(
            b"_original_case_state"
            if isinstance(next(iter(all_data.keys())), bytes)
            else "_original_case_state"
        )
        original_case_state = json.loads(original_raw) if original_raw else {}

        # Deep-copy to avoid mutating cached data
        merged = copy.deepcopy(original_case_state)

        # Merge only the designated agent output fields into the full CaseState
        for agent_key in self.REQUIRED_AGENTS:
            raw = all_data.get(
                agent_key.encode() if isinstance(next(iter(all_data.keys())), bytes) else agent_key
            )
            if raw:
                fragment = json.loads(raw)
                # Update the designated CaseState field with the agent's output
                merged[agent_key] = fragment.get(agent_key, fragment)

        # Cleanup
        await self.redis.delete(key)
        await self.redis.delete(key + ":created")

        return merged

    @staticmethod
    def _extract_agent_keys(all_data: dict) -> set[str]:
        """Extract agent keys from a Redis hash, filtering out metadata fields."""
        return {
            k.decode() if isinstance(k, bytes) else k
            for k in all_data
            if not (k.decode() if isinstance(k, bytes) else k).endswith("_ts")
            and (k.decode() if isinstance(k, bytes) else k)
            not in ("_original_case_state", "_published")
        }
