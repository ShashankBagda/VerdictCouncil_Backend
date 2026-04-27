"""Sprint 2 2.A2.8 — exercise the LangGraph checkpointer API surface we depend on.

Production wires `AsyncPostgresSaver`, but the saver-API contract is the
same as `InMemorySaver`'s. Pin the four entrypoints `gate_run`, the
What-If controller, and ad-hoc tooling rely on:

  1. `aget_state_history(config)` — paged, newest-first checkpoint stream
  2. `aupdate_state(config, values)` with `Overwrite(...)` — bypasses the
     reducer merge for fields that have one (else dict-merge wins)
  3. `ainvoke(None, past_config)` — replay from an older checkpoint
  4. Fork via `aupdate_state(past_config, ...)` — branch off a past state

These are the APIs the cutover runbook (2.A2.9) and the post-cutover
debug flows depend on. If a LangGraph upgrade silently changes one of
these contracts, this test fails before production does.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph
from langgraph.types import Overwrite


class _CountState(TypedDict):
    # Reducer-backed: parallel writers and replays merge dicts.
    counts: Annotated[dict[str, int], lambda a, b: {**a, **b}]
    # Reducer-backed list, append semantics.
    log: Annotated[list[str], operator.add]
    # Plain field — last-writer-wins.
    note: str


def _bump_a(state: _CountState) -> dict:
    return {"counts": {"a": 1}, "log": ["bump_a"], "note": "after_a"}


def _bump_b(state: _CountState) -> dict:
    return {"counts": {"b": 2}, "log": ["bump_b"], "note": "after_b"}


def _build_graph(saver: InMemorySaver):
    builder = StateGraph(_CountState)
    builder.add_node("bump_a", _bump_a)
    builder.add_node("bump_b", _bump_b)
    builder.set_entry_point("bump_a")
    builder.add_edge("bump_a", "bump_b")
    builder.set_finish_point("bump_b")
    return builder.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_aget_state_history_returns_newest_first() -> None:
    saver = InMemorySaver()
    graph = _build_graph(saver)
    config = {"configurable": {"thread_id": "t-history"}}

    await graph.ainvoke({"counts": {}, "log": [], "note": "init"}, config)

    history = [snap async for snap in graph.aget_state_history(config)]

    assert len(history) >= 3, f"expected ≥3 checkpoints, got {len(history)}"
    # Newest first: the head reflects bump_b's writes.
    assert history[0].values["note"] == "after_b"
    assert history[0].next == ()
    # Somewhere in the chain the initial input is preserved verbatim.
    notes_seen = [s.values.get("note") for s in history]
    assert "init" in notes_seen, f"initial input missing from history: {notes_seen}"


@pytest.mark.asyncio
async def test_aupdate_state_overwrite_bypasses_reducer_merge() -> None:
    saver = InMemorySaver()
    graph = _build_graph(saver)
    config = {"configurable": {"thread_id": "t-overwrite"}}

    await graph.ainvoke({"counts": {}, "log": [], "note": "init"}, config)

    snapshot = await graph.aget_state(config)
    assert snapshot.values["counts"] == {"a": 1, "b": 2}, (
        "default reducer merges parallel writes — sanity check"
    )

    # Plain update merges through the reducer (dict union).
    await graph.aupdate_state(config, {"counts": {"c": 3}})
    merged = (await graph.aget_state(config)).values
    assert merged["counts"] == {"a": 1, "b": 2, "c": 3}

    # Overwrite skips the reducer and replaces the field wholesale.
    await graph.aupdate_state(config, {"counts": Overwrite({"only": 99})})
    overwritten = (await graph.aget_state(config)).values
    assert overwritten["counts"] == {"only": 99}, "Overwrite must replace, not merge"


@pytest.mark.asyncio
async def test_ainvoke_replays_from_past_checkpoint() -> None:
    """`ainvoke(None, past_config)` resumes execution from the supplied checkpoint."""
    saver = InMemorySaver()
    graph = _build_graph(saver)
    config = {"configurable": {"thread_id": "t-replay"}}

    await graph.ainvoke({"counts": {}, "log": [], "note": "init"}, config)

    history = [snap async for snap in graph.aget_state_history(config)]
    # Pick the checkpoint just before bump_b ran — it has bump_a's writes
    # but bump_b is still the next pending node.
    pre_b = next(
        snap
        for snap in reversed(history)
        if snap.values.get("note") == "after_a" and "bump_b" in (snap.next or ())
    )

    # Replay from that checkpoint with no new input — bump_b runs again
    # and reapplies its writes on top.
    final = await graph.ainvoke(None, pre_b.config)
    assert final["note"] == "after_b"
    assert final["counts"] == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_aupdate_state_forks_from_past_checkpoint() -> None:
    """Forking: `aupdate_state(past_config, ...)` creates a divergent branch."""
    saver = InMemorySaver()
    graph = _build_graph(saver)
    config = {"configurable": {"thread_id": "t-fork"}}

    await graph.ainvoke({"counts": {}, "log": [], "note": "init"}, config)

    history = [snap async for snap in graph.aget_state_history(config)]
    pre_b = next(
        snap
        for snap in reversed(history)
        if snap.values.get("note") == "after_a" and "bump_b" in (snap.next or ())
    )

    # Fork: write a different note onto the pre-b checkpoint. The returned
    # config points at the new branch's head.
    forked_config = await graph.aupdate_state(pre_b.config, {"note": "forked"})

    # Replay the fork to completion (bump_b is still pending in the new branch).
    final = await graph.ainvoke(None, forked_config)
    assert final["note"] == "after_b"

    # The original thread head still reflects the pre-fork run — both
    # branches coexist under the same thread_id but distinct checkpoint
    # ids.
    main_head = await graph.aget_state(config)
    assert main_head.values["note"] == "after_b"
