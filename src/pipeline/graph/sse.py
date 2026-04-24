"""SSE bridge utilities for the LangGraph pipeline.

SSE events are emitted directly from _run_agent_node via publish_progress
and publish_agent_event, so this module is intentionally thin. It provides
helper types and a streaming wrapper for callers that want to consume
LangGraph's astream_events alongside the Redis pub/sub SSE channel.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any


async def astream_graph_events(
    compiled_graph: Any,
    input_state: dict,
    config: dict,
) -> AsyncGenerator[dict, None]:
    """Yield raw LangGraph v2 events for debugging and integration tests.

    The primary SSE channel (Redis pub/sub, consumed by the frontend) is
    written directly inside _run_agent_node. This stream is for out-of-band
    consumers (e.g., shadow runner diffs, test harnesses) that want a
    machine-readable event feed from the graph engine itself.
    """
    async for event in compiled_graph.astream_events(input_state, config=config, version="v2"):
        yield event
