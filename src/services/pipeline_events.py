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
    - ``pipeline`` + ``cancelled`` is emitted when the judge explicitly
      cancels a running pipeline via POST /cases/{id}/cancel.
    """
    agent = parsed.get("agent")
    phase = parsed.get("phase")
    if agent == "hearing-governance" and phase in _GOVERNANCE_TERMINAL_PHASES:
        return True
    return agent == "pipeline" and phase in {"terminal", "awaiting_review", "cancelled"}


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


async def publish_agent_event(case_id: str | object, event: dict) -> None:
    """Publish a fine-grained agent event (thinking / tool_call / tool_result / llm_response).

    This is the free-form counterpart to `publish_progress`. The
    sequential runner emits these from inside its LLM+tool loop so the
    `/case/<id>/building` UI can show what's actually happening beyond
    agent lifecycle transitions. The event dict is forwarded verbatim
    to SSE subscribers — keys like `event`, `agent`, `content`,
    `tool_name`, `args`, `result` are what the frontend's `EventLine`
    already branches on.

    Failures are logged but never raised: telemetry must never break a
    running pipeline.
    """
    try:
        r = await _get_redis_client()
        await r.publish(_channel(case_id), json.dumps(event, default=str))
    except Exception:
        logger.exception("Failed to publish agent event")


_CANCEL_KEY_TTL = 86400  # 24 hours


def _cancel_key(case_id: str | object) -> str:
    return f"vc:case:{case_id}:cancel_requested"


async def set_cancel_flag(case_id: str | object) -> None:
    """Signal that the pipeline for this case should stop at the next inter-turn check."""
    try:
        r = await _get_redis_client()
        await r.set(_cancel_key(case_id), "1", ex=_CANCEL_KEY_TTL)
    except Exception:
        logger.exception("Failed to set cancel flag for case %s", case_id)


async def check_cancel_flag(case_id: str | object) -> bool:
    """Return True if cancellation has been requested for this case's pipeline."""
    try:
        r = await _get_redis_client()
        return bool(await r.exists(_cancel_key(case_id)))
    except Exception:
        logger.exception("Failed to check cancel flag for case %s", case_id)
        return False


async def clear_cancel_flag(case_id: str | object) -> None:
    """Remove the cancellation flag after the pipeline has handled it."""
    try:
        r = await _get_redis_client()
        await r.delete(_cancel_key(case_id))
    except Exception:
        logger.exception("Failed to clear cancel flag for case %s", case_id)


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
