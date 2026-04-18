"""SAM component that bridges broker messages to the Layer2Aggregator.

The component subscribes to the 3 parallel-fan-in topics (evidence analysis,
fact reconstruction, witness analysis) via the Solace AI Connector framework,
and delegates to the existing async `Layer2Aggregator` in `aggregator.py` for
the Redis-backed barrier logic.

Because `ComponentBase.invoke` is synchronous but the aggregator is async, this
component owns a dedicated asyncio event loop running in a daemon thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import redis.asyncio as redis

from solace_ai_connector.components.component_base import ComponentBase

from .aggregator import Layer2Aggregator

logger = logging.getLogger(__name__)


info = {
    "class_name": "Layer2AggregatorComponent",
    "description": (
        "Fan-in barrier for the three parallel Layer-2 agents. "
        "Waits for evidence-analysis, fact-reconstruction, and witness-analysis "
        "to complete per (case_id, run_id), then publishes the merged CaseState "
        "to the Layer-3 input topic."
    ),
    "config_parameters": [
        {
            "name": "topic_map",
            "required": True,
            "type": "object",
            "description": (
                "Mapping from subscribed topic -> CaseState field name "
                "(e.g. 'verdictcouncil/aggregator/input/evidence-analysis' -> "
                "'evidence_analysis')."
            ),
        },
        {
            "name": "output_topic",
            "required": True,
            "type": "string",
            "description": "Topic on which to publish the merged CaseState.",
        },
        {
            "name": "redis_url",
            "required": True,
            "type": "string",
            "description": "Redis connection string (e.g. redis://host:6379/1).",
        },
    ],
    "input_schema": {
        "type": "object",
        "properties": {
            "case_id": {"type": "string"},
            "run_id": {"type": "string"},
            "output": {"type": "object"},
            "base_state": {"type": "object"},
        },
        "required": ["case_id", "run_id", "output", "base_state"],
    },
    "output_schema": {
        "type": "object",
        "description": "Merged CaseState dict, only emitted once the barrier is met.",
    },
}


class Layer2AggregatorComponent(ComponentBase):
    """Bridges broker inputs to the async `Layer2Aggregator`."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(info, **kwargs)

        self._topic_map: dict[str, str] = self.get_config("topic_map") or {}
        self._output_topic: str = self.get_config("output_topic")
        redis_url: str = self.get_config("redis_url")

        if not self._topic_map:
            raise ValueError("Layer2AggregatorComponent requires non-empty topic_map")
        if not self._output_topic:
            raise ValueError("Layer2AggregatorComponent requires output_topic")
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
        agent_key = self._topic_map.get(topic)
        if agent_key is None:
            logger.warning(
                "Layer2AggregatorComponent received message on unmapped topic %s; dropping",
                topic,
            )
            return None

        payload = self._coerce_payload(data)
        case_id = payload.get("case_id")
        run_id = payload.get("run_id")
        output = payload.get("output")
        base_state = payload.get("base_state") or {}

        if not (case_id and run_id and output is not None):
            logger.error(
                "Malformed aggregator input on %s: missing case_id/run_id/output (keys=%s)",
                topic,
                sorted(payload.keys()),
            )
            return None

        future = asyncio.run_coroutine_threadsafe(
            self._aggregator.receive_output(
                agent_key=agent_key,
                case_id=case_id,
                run_id=run_id,
                output=output,
                base_state=base_state,
            ),
            self._loop,
        )
        merged = future.result()

        if merged is None:
            return None

        logger.info(
            "Layer2 barrier met for case_id=%s run_id=%s — forwarding to %s",
            case_id,
            run_id,
            self._output_topic,
        )
        return {"payload": merged, "topic": self._output_topic}

    @staticmethod
    def _extract_topic(message: Any) -> str:
        if message is None:
            return ""
        topic = getattr(message, "get_topic", None)
        if callable(topic):
            return topic() or ""
        direct = getattr(message, "topic", None)
        return direct or ""

    @staticmethod
    def _coerce_payload(data: Any) -> dict:
        if isinstance(data, dict):
            return data
        if isinstance(data, (bytes, bytearray)):
            return json.loads(data.decode("utf-8"))
        if isinstance(data, str):
            return json.loads(data)
        return {}

    def stop_component(self) -> None:
        try:
            if self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread.is_alive():
                self._loop_thread.join(timeout=2)
        finally:
            super().stop_component()
