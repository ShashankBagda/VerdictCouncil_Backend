"""Redis pub/sub fan-out for pipeline progress events (US-002).

Reuses the Redis singleton from ``src.tools.search_precedents`` to avoid
opening a second connection pool. Subscribers get an async generator
that yields events until the case reaches a terminal status (the
``hearing-governance`` agent completing or failing closes the stream).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.tools.search_precedents import _get_redis_client

logger = logging.getLogger(__name__)

_GOVERNANCE_TERMINAL_PHASES = {"completed", "failed"}


def _channel(case_id: str | object) -> str:
    return f"vc:case:{case_id}:progress"


def _is_terminal_event(parsed: dict) -> bool:
    """Close the stream on either the happy path or any halt path.

    - ``hearing-governance`` + ``completed``/``failed`` is the happy-path
      close signal left over from US-002.
    - ``pipeline`` + ``terminal`` is the run-level halt signal the mesh
      runner emits on escalation, barrier timeout, governance halt, etc.
    - ``pipeline`` + ``awaiting_review`` is the gate-pause signal emitted
      after each gate completes. The SSE client closes and reconnects when
      the judge advances to the next gate.
    """
    agent = parsed.get("agent")
    phase = parsed.get("phase")
    if agent == "hearing-governance" and phase in _GOVERNANCE_TERMINAL_PHASES:
        return True
    return agent == "pipeline" and phase in {"terminal", "awaiting_review"}


async def publish_progress(event: PipelineProgressEvent) -> None:
    """Fire-and-forget publish of a pipeline progress event.

    Failures are logged but never raised — pipeline execution must not
    be blocked by the observability sidecar.
    """
    try:
        r = await _get_redis_client()
        await r.publish(_channel(event.case_id), event.model_dump_json())
    except Exception:
        logger.exception("Failed to publish pipeline progress event")


async def subscribe(case_id: str | object) -> AsyncGenerator[str, None]:
    """Yield JSON-serialized events for a case until hearing-governance closes."""
    r = await _get_redis_client()
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(_channel(case_id))
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            payload = message["data"]
            yield payload
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if _is_terminal_event(parsed):
                return
    finally:
        await pubsub.unsubscribe(_channel(case_id))
        await pubsub.aclose()
