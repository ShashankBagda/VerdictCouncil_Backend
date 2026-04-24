"""Redis pub/sub fan-out for case-intake events.

Separate channel from the 9-agent pipeline progress stream so the intake
chat surface on the frontend can subscribe without being spammed by
pipeline events (and vice versa). Message shape follows the Vercel AI SDK
UI-message-stream format so `useChat` on the frontend wires up with a
custom transport pointed at the FastAPI SSE endpoint — no shim layer.

Terminal events close the stream:
  - `type == "done"`  (extractor finished, fields proposed)
  - `type == "error"` (extractor failed)
  - `type == "confirmed"` (judge confirmed; case has left intake)
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from src.tools.search_precedents import _get_redis_client

logger = logging.getLogger(__name__)

_TERMINAL_TYPES = {"done", "error", "confirmed"}


def _channel(case_id: str | object) -> str:
    return f"vc:case:{case_id}:intake"


async def publish_intake_event(case_id: str | object, event: dict[str, Any]) -> None:
    """Fire-and-forget publish. Never raise — observability is best-effort."""
    try:
        r = await _get_redis_client()
        await r.publish(_channel(case_id), json.dumps(event))
    except Exception:
        logger.exception("Failed to publish intake event")


async def subscribe_intake_events(case_id: str | object) -> AsyncGenerator[str, None]:
    """Yield JSON-serialized intake events until a terminal type arrives."""
    r = await _get_redis_client()
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(_channel(case_id))
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            payload = message["data"]
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            yield payload
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if parsed.get("type") in _TERMINAL_TYPES:
                return
    finally:
        await pubsub.unsubscribe(_channel(case_id))
        await pubsub.aclose()
