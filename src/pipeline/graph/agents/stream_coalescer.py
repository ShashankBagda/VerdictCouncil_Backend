"""Q1.1 — token coalescer + fire-and-forget publisher.

Two transport-agnostic primitives the conversational-streaming path
(Q1.4) wires to the SSE publish layer.

`StreamCoalescer` buffers per-token deltas from the LLM stream and
emits a batched event when ANY of three triggers fires:
- the time window elapses (default 50ms),
- the buffered char count crosses the cap (default 64),
- the caller signals an explicit boundary via `flush()` (typically
  before a tool call so the prose-up-to-here reaches the UI before
  the tool-call chip renders).

`FireAndForgetPublisher` decouples `feed()` from the Redis publish
path. The agent loop calls `submit()` which returns synchronously in
microseconds; a drain task pulls events from a bounded queue and
publishes them. Sustained backpressure drops with a counter rather
than blocking the agent — the user sees a slightly choppy stream,
not a hung pipeline (Risk #2).

Nothing here is wired into the live pipeline yet — Q1.4 does that.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from src.api.middleware.metrics import metrics_store

logger = logging.getLogger(__name__)


class StreamCoalescer:
    """Buffer per-token deltas; flush on time / size / boundary."""

    def __init__(
        self,
        *,
        on_emit: Callable[[str], Awaitable[None]],
        window_seconds: float = 0.05,
        max_chars: int = 64,
    ) -> None:
        self._on_emit = on_emit
        self._window = window_seconds
        self._max_chars = max_chars
        self._buf: list[str] = []
        self._buf_chars = 0
        self._first_delta_ts: float | None = None

    async def feed(self, delta: str) -> None:
        """Buffer `delta`. Flushes when the time window or char cap is hit."""
        if not delta:
            return
        if self._first_delta_ts is None:
            self._first_delta_ts = time.monotonic()
        self._buf.append(delta)
        self._buf_chars += len(delta)

        if self._buf_chars >= self._max_chars:
            await self.flush()
            return

        elapsed = time.monotonic() - self._first_delta_ts
        if elapsed >= self._window:
            await self.flush()

    async def flush(self) -> None:
        """Emit any buffered text. Called explicitly at boundaries."""
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf = []
        self._buf_chars = 0
        self._first_delta_ts = None
        await self._on_emit(text)

    async def close(self) -> None:
        """End-of-stream — drain pending text."""
        await self.flush()


class FireAndForgetPublisher:
    """Bounded-queue publisher. `submit()` is synchronous + non-blocking.

    The agent loop must NEVER await on Redis backpressure (Risk #2 hard
    constraint), so this class owns the drain task and the bounded
    queue. When the queue is full, `submit()` increments
    `pipeline_stream_publish_dropped_total{phase=...}` and returns
    `False` — the caller (typically the coalescer's `on_emit`) treats
    that as best-effort delivery.
    """

    def __init__(
        self,
        *,
        on_drain: Callable[[Any], Awaitable[None]],
        queue_size: int = 256,
        phase: str = "unknown",
    ) -> None:
        self._on_drain = on_drain
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_size)
        self._phase = phase
        self._task: asyncio.Task | None = None
        self._closed = False

    async def start(self) -> None:
        """Launch the drain task. Idempotent."""
        if self._task is None:
            self._task = asyncio.create_task(
                self._drain(), name=f"stream-publisher-{self._phase}"
            )

    def submit(self, event: Any) -> bool:
        """Non-blocking enqueue. Returns False on drop (queue full or closed)."""
        if self._closed:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            metrics_store.inc_stream_publish_dropped(self._phase)
            return False

    async def _drain(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._on_drain(event)
            except Exception:
                logger.exception(
                    "fire-and-forget publish failed (phase=%s)", self._phase
                )
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        """Block until the queue drains, then cancel the task. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._task is None:
            return
        await self._queue.join()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
