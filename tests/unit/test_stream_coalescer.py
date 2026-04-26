"""Q1.1 — token coalescer + fire-and-forget publisher.

Two primitives, both transport-agnostic so Q1.4 can wire them to the
SSE publish path without touching the coalescer's logic.

`StreamCoalescer` buffers per-token deltas and flushes on three
triggers: time window, char threshold, or explicit boundary
(typically: the agent is about to call a tool).

`FireAndForgetPublisher` decouples the agent's `feed()` call from the
Redis publish path — the per-token publish must NEVER block the agent
loop on backpressure (Risk #2). A bounded queue + drain task absorbs
short stalls; sustained backpressure drops with a counter so we can
observe it in metrics rather than discover it via a hung pipeline.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from src.api.middleware.metrics import metrics_store
from src.pipeline.graph.agents.stream_coalescer import (
    FireAndForgetPublisher,
    StreamCoalescer,
)

# ---------------------------------------------------------------------------
# StreamCoalescer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coalescer_collapses_2000_single_char_deltas_to_at_most_50_batches():
    """The whole point of the coalescer: under sustained streaming the
    publish path sees an order-of-magnitude fewer events than the agent
    feeds. 2000 / 64-char window → ~32 batches; cap at 50 leaves
    headroom for time-window flushes."""
    emitted: list[str] = []

    async def _on_emit(text: str) -> None:
        emitted.append(text)

    coalescer = StreamCoalescer(on_emit=_on_emit, window_seconds=0.05, max_chars=64)
    for _ in range(2000):
        await coalescer.feed("x")
    await coalescer.flush()

    assert len(emitted) <= 50
    assert "".join(emitted) == "x" * 2000  # no data loss


@pytest.mark.asyncio
async def test_coalescer_flushes_on_explicit_boundary():
    """Caller signals tool-call boundaries via `flush()`. The test feeds
    a few prose deltas, flushes (simulating the agent calling its
    tool), feeds more, and expects two distinct emissions — proving
    the coalescer didn't silently merge across the boundary."""
    emitted: list[str] = []

    async def _on_emit(text: str) -> None:
        emitted.append(text)

    coalescer = StreamCoalescer(on_emit=_on_emit, window_seconds=10.0, max_chars=1024)
    await coalescer.feed("Reasoning step 1. ")
    await coalescer.feed("Reasoning step 2. ")
    await coalescer.flush()  # agent about to call parse_document
    await coalescer.feed("After tool call: result is X.")
    await coalescer.flush()

    assert emitted == [
        "Reasoning step 1. Reasoning step 2. ",
        "After tool call: result is X.",
    ]


@pytest.mark.asyncio
async def test_coalescer_flushes_on_max_chars_threshold():
    """A burst of one large delta exceeds the char cap → flush
    immediately rather than waiting for the time window."""
    emitted: list[str] = []

    async def _on_emit(text: str) -> None:
        emitted.append(text)

    coalescer = StreamCoalescer(on_emit=_on_emit, window_seconds=10.0, max_chars=64)
    await coalescer.feed("x" * 100)

    assert len(emitted) == 1
    assert emitted[0] == "x" * 100


@pytest.mark.asyncio
async def test_coalescer_flushes_on_time_window():
    """A trickle of deltas under the char cap still flushes once the
    window elapses."""
    emitted: list[str] = []

    async def _on_emit(text: str) -> None:
        emitted.append(text)

    coalescer = StreamCoalescer(on_emit=_on_emit, window_seconds=0.02, max_chars=1024)
    await coalescer.feed("a")
    await asyncio.sleep(0.05)
    await coalescer.feed("b")  # this feed observes the elapsed window

    assert emitted, "expected a window-driven flush"
    assert "a" in emitted[0]


@pytest.mark.asyncio
async def test_coalescer_close_drains_pending():
    """`close()` is the end-of-stream contract — anything pending must
    flush so the consumer sees the full prose."""
    emitted: list[str] = []

    async def _on_emit(text: str) -> None:
        emitted.append(text)

    coalescer = StreamCoalescer(on_emit=_on_emit, window_seconds=10.0, max_chars=1024)
    await coalescer.feed("trailing text")
    await coalescer.close()

    assert emitted == ["trailing text"]


@pytest.mark.asyncio
async def test_coalescer_empty_feed_is_a_noop():
    """Defensive: empty deltas appear when streams have content-only
    chunks. Don't bump timers or buffers on them."""
    emitted: list[str] = []

    async def _on_emit(text: str) -> None:
        emitted.append(text)

    coalescer = StreamCoalescer(on_emit=_on_emit, window_seconds=10.0, max_chars=1024)
    await coalescer.feed("")
    await coalescer.flush()
    assert emitted == []


# ---------------------------------------------------------------------------
# FireAndForgetPublisher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publisher_submit_returns_under_5ms_when_drain_is_stalled():
    """Risk #2 hard constraint: the agent loop must NEVER await the
    Redis publish. Even when the drain task is stuck on a 200ms publish,
    `submit()` returns synchronously in microseconds."""
    drain_event = asyncio.Event()

    async def _stalled_drain(_event: dict) -> None:
        await drain_event.wait()  # block forever until the test releases

    publisher = FireAndForgetPublisher(
        on_drain=_stalled_drain, queue_size=256, phase="intake"
    )
    await publisher.start()

    # Submit a flood of events while the drain is stuck on the first one.
    start = time.perf_counter()
    for i in range(50):
        publisher.submit({"event": "llm_token", "delta": str(i)})
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert elapsed_ms < 5.0, f"submit() blocked for {elapsed_ms:.2f}ms"

    # Release and clean up so the test exits cleanly.
    drain_event.set()
    await publisher.close()


@pytest.mark.asyncio
async def test_publisher_drops_with_metric_when_queue_overflows():
    """Sustained backpressure → drop, count, observe via /metrics. The
    user sees a slightly choppy stream (Q1.1 doc), NOT a hung agent."""
    drain_event = asyncio.Event()

    async def _stalled_drain(_event: dict) -> None:
        await drain_event.wait()

    publisher = FireAndForgetPublisher(
        on_drain=_stalled_drain, queue_size=8, phase="overflow_test"
    )
    await publisher.start()

    metric_before = metrics_store.get_stream_publish_dropped("overflow_test")

    # Queue holds 8; first one is in-flight on the stalled drain. Submit
    # 100 → at least 90 must drop.
    accepted = 0
    for i in range(100):
        if publisher.submit({"event": "llm_token", "delta": str(i)}):
            accepted += 1

    metric_after = metrics_store.get_stream_publish_dropped("overflow_test")

    assert accepted <= 9  # 1 in-flight + 8 in queue
    assert metric_after - metric_before == 100 - accepted

    drain_event.set()
    await publisher.close()


@pytest.mark.asyncio
async def test_publisher_drains_queued_events_in_order():
    """Steady state: drain runs, events publish in submission order."""
    received: list[dict] = []

    async def _drain(event: dict) -> None:
        received.append(event)

    publisher = FireAndForgetPublisher(on_drain=_drain, queue_size=256, phase="intake")
    await publisher.start()

    for i in range(10):
        publisher.submit({"i": i})

    await publisher.close()

    assert [e["i"] for e in received] == list(range(10))


@pytest.mark.asyncio
async def test_publisher_submit_after_close_is_a_noop_drop():
    """Closed publisher must not accept new work. Drops silently
    (no exception so the agent loop doesn't crash on a late event
    after a phase teardown)."""
    publisher = FireAndForgetPublisher(
        on_drain=lambda _: asyncio.sleep(0), queue_size=8, phase="closed"
    )
    await publisher.start()
    await publisher.close()

    accepted = publisher.submit({"event": "llm_token"})
    assert accepted is False
