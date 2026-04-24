"""Minimal async A2A client abstraction for the mesh pipeline runner.

The runner publishes JSON-RPC 2.0 SendTaskRequest envelopes to each
agent's request topic and awaits the matching SendTaskResponse on its
own response wildcard. This module defines:

- `A2AClient`: the async Protocol the runner depends on.
- `FakeA2AClient`: an in-memory implementation for unit tests. Tracks
  publishes so tests can assert topic / replyTo / envelope shape, and
  lets tests resolve responses by task id.
- `build_send_task_request`: builds the SendTaskRequest envelope
  carrying the CaseState as a single DataPart.

The real Solace-backed implementation lives alongside in a follow-up
commit; keeping the Protocol here lets us unit-test the orchestrator
without standing up a broker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any, Protocol

from src.services.layer2_aggregator.a2a import DATA_PART_TYPE


def build_send_task_request(
    task_id: str,
    session_id: str,
    payload: dict,
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Build a JSON-RPC 2.0 SendTaskRequest carrying `payload` as DataPart."""
    return {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "tasks/send",
        "params": {
            "id": task_id,
            "sessionId": session_id,
            "message": {
                "role": "user",
                "parts": [{"type": DATA_PART_TYPE, "data": payload}],
            },
            "acceptedOutputModes": ["data", "text"],
            "metadata": metadata,
        },
    }


def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class A2AClient(Protocol):
    """Contract the mesh runner depends on for Solace pub/sub."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def publish(
        self,
        topic: str,
        envelope: dict,
        reply_to: str | None = None,
        status_topic: str | None = None,
    ) -> str: ...

    async def await_response(self, task_id: str, timeout: float) -> dict: ...


class FakeA2AClient:
    """In-memory A2AClient for unit tests.

    - `publishes` records every `(topic, envelope, reply_to)` tuple so
      tests can assert request construction.
    - `resolve(task_id, envelope)` delivers a response envelope to a
      waiting `await_response` caller.
    - `auto_resolver` (if set) is called on each publish with
      `(topic, envelope, reply_to)`; it may return a response envelope
      to be delivered immediately (keyed on `envelope["id"]`), or None
      to leave the task pending.
    """

    def __init__(self) -> None:
        self.publishes: list[tuple[str, dict, str | None]] = []
        # Parallel log that includes the optional status_topic — kept
        # separate so existing tests that unpack 3-tuples keep passing.
        self.publishes_with_status: list[tuple[str, dict, str | None, str | None]] = []
        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._delivered: dict[str, dict] = {}
        self.auto_resolver = None  # callable | None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def publish(
        self,
        topic: str,
        envelope: dict,
        reply_to: str | None = None,
        status_topic: str | None = None,
    ) -> str:
        self.publishes.append((topic, envelope, reply_to))
        self.publishes_with_status.append((topic, envelope, reply_to, status_topic))
        payload_hash = hashlib.sha256(json.dumps(envelope, sort_keys=True).encode("utf-8")).hexdigest()
        if self.auto_resolver is not None:
            response = self.auto_resolver(topic, envelope, reply_to)
            if response is not None:
                resolve_id = response.get("id") or envelope.get("id")
                self.resolve(resolve_id, response)
        return payload_hash

    async def await_response(self, task_id: str, timeout: float) -> dict:
        async with self._lock:
            if task_id in self._delivered:
                return self._delivered.pop(task_id)
            fut: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
            self._pending[task_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(task_id, None)

    def resolve(self, task_id: str, envelope: dict) -> None:
        """Deliver a response envelope for `task_id`.

        Safe to call before or after `await_response` begins waiting.
        Called from the event loop thread (test code).
        """
        fut = self._pending.get(task_id)
        if fut is not None and not fut.done():
            fut.set_result(envelope)
            return
        self._delivered[task_id] = envelope
