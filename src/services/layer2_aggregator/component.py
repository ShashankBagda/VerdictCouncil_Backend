"""SAM component that bridges native A2A responses to the Layer2Aggregator.

Subscribes to `verdictcouncil/a2a/v1/agent/response/layer2-aggregator/>`
(the wildcard reply-to the mesh runner sets on L2 requests). Each
inbound message is a `SendTaskResponse` envelope; the component:

1. Pulls the sub_task_id from the trailing topic segment.
2. Looks up `vc:aggregator:sub_task:<sub_task_id>` in Redis for the
   `(agent_key, case_id, run_id)` tuple written by the mesh runner
   before it published the L2 requests.
3. Looks up `vc:aggregator:run:<case_id>:<run_id>:meta` for the
   original CaseState (``base_state``) and the mesh runner's
   ``mesh_reply_to`` topic.
4. Calls `Layer2Aggregator.receive_output(...)` to update the Redis
   barrier. If the barrier fires, builds a fresh SendTaskResponse and
   emits it to ``mesh_reply_to`` so the runner can resume the chain.

Because `ComponentBase.invoke` is synchronous and the aggregator is
async, the component owns a dedicated asyncio event loop running on
a daemon thread (same pattern as `SamAgentApp`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import redis.asyncio as redis
from solace_ai_connector.components.component_base import ComponentBase

from .a2a import (
    build_send_task_response,
    parse_send_task_response,
    sub_task_id_from_topic,
)
from .aggregator import Layer2Aggregator

logger = logging.getLogger(__name__)


info = {
    "class_name": "Layer2AggregatorComponent",
    "description": (
        "Fan-in barrier for the three parallel Layer-2 agents. Subscribes "
        "to the native A2A response wildcard for the aggregator, merges "
        "outputs per (case_id, run_id), and publishes a SendTaskResponse "
        "envelope to the mesh runner's reply-to once all three report."
    ),
    "config_parameters": [
        {
            "name": "redis_url",
            "required": True,
            "type": "string",
            "description": "Redis connection string (e.g. redis://host:6379/1).",
        },
    ],
    "input_schema": {
        "type": "object",
        "description": "JSON-RPC 2.0 SendTaskResponse envelope from an L2 agent.",
    },
    "output_schema": {
        "type": "object",
        "description": (
            "JSON-RPC 2.0 SendTaskResponse envelope carrying the merged "
            "CaseState, only emitted once all three L2 agents report."
        ),
    },
}


# Redis keys written by the mesh runner and read here.
SUB_TASK_KEY_PREFIX = "vc:aggregator:sub_task:"
RUN_META_KEY_PREFIX = "vc:aggregator:run:"


class Layer2AggregatorComponent(ComponentBase):
    """Bridges native A2A response topics to the async `Layer2Aggregator`."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(info, **kwargs)

        redis_url: str = self.get_config("redis_url")
        if not redis_url:
            raise ValueError("Layer2AggregatorComponent requires redis_url")

        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="layer2-aggregator-loop", daemon=True
        )
        self._loop_thread.start()

        self._redis = redis.Redis.from_url(redis_url, decode_responses=False)
        self._aggregator = Layer2Aggregator(self._redis, publisher=None)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.component_config.get(key, default)

    def invoke(self, message: Any, data: Any) -> Any:
        topic = self._extract_topic(message)
        sub_task_id = sub_task_id_from_topic(topic)
        if not sub_task_id:
            logger.warning(
                "Layer2AggregatorComponent received message with no sub_task_id "
                "(topic=%r); dropping",
                topic,
            )
            return None

        correlation = asyncio.run_coroutine_threadsafe(
            self._lookup_correlation(sub_task_id),
            self._loop,
        ).result()
        if correlation is None:
            logger.warning(
                "Layer2AggregatorComponent has no correlation entry for sub_task_id=%s "
                "(orphan response or expired TTL); dropping",
                sub_task_id,
            )
            return None
        agent_key, case_id, run_id = correlation

        output = parse_send_task_response(data)
        if not output:
            logger.error(
                "Empty/unparseable agent output for sub_task_id=%s agent_key=%s; dropping",
                sub_task_id,
                agent_key,
            )
            return None

        run_meta = asyncio.run_coroutine_threadsafe(
            self._lookup_run_meta(case_id, run_id),
            self._loop,
        ).result()
        if run_meta is None:
            logger.error(
                "No run metadata for case_id=%s run_id=%s; cannot merge (mesh runner "
                "must stash base_state + mesh_reply_to before publishing L2 requests)",
                case_id,
                run_id,
            )
            return None
        base_state, mesh_reply_to = run_meta

        merged = asyncio.run_coroutine_threadsafe(
            self._aggregator.receive_output(
                agent_key=agent_key,
                case_id=case_id,
                run_id=run_id,
                output=output,
                base_state=base_state,
            ),
            self._loop,
        ).result()

        if merged is None:
            return None

        logger.info(
            "Layer2 barrier met for case_id=%s run_id=%s — emitting SendTaskResponse to %s",
            case_id,
            run_id,
            mesh_reply_to,
        )
        envelope = build_send_task_response(
            task_id=f"layer2-{case_id}-{run_id}",
            session_id=run_id,
            merged_state=merged,
        )
        return {"payload": envelope, "topic": mesh_reply_to}

    async def _lookup_correlation(self, sub_task_id: str) -> tuple[str, str, str] | None:
        """Read `agent_key|case_id|run_id` for an L2 sub-task id."""
        raw = await self._redis.get(SUB_TASK_KEY_PREFIX + sub_task_id)
        if raw is None:
            return None
        value = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        parts = value.split("|")
        if len(parts) != 3:
            logger.error(
                "Malformed sub_task correlation for %s (expected 3 pipe-separated fields, got %r)",
                sub_task_id,
                value,
            )
            return None
        return parts[0], parts[1], parts[2]

    async def _lookup_run_meta(self, case_id: str, run_id: str) -> tuple[dict, str] | None:
        """Read `{base_state, mesh_reply_to}` for a pipeline run."""
        key = f"{RUN_META_KEY_PREFIX}{case_id}:{run_id}:meta"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        try:
            meta = json.loads(text)
        except json.JSONDecodeError:
            logger.error("Malformed run meta for %s: %r", key, text)
            return None
        base_state = meta.get("base_state") or {}
        mesh_reply_to = meta.get("mesh_reply_to") or ""
        if not mesh_reply_to:
            logger.error("Run meta for %s missing mesh_reply_to", key)
            return None
        return base_state, mesh_reply_to

    @staticmethod
    def _extract_topic(message: Any) -> str:
        if message is None:
            return ""
        topic = getattr(message, "get_topic", None)
        if callable(topic):
            return topic() or ""
        direct = getattr(message, "topic", None)
        return direct or ""

    def stop_component(self) -> None:
        try:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread.is_alive():
                self._loop_thread.join(timeout=2)
        finally:
            super().stop_component()
