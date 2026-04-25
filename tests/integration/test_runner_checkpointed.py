"""Sprint 1 1.A1.PG — checkpointer compile-time wiring (P0 codex finding 2).

These tests stay tight on the wiring contract:
  1. `build_graph(checkpointer=...)` accepts a checkpointer and surfaces it
     on the compiled graph.
  2. `build_graph()` (no kwarg) reads the module-level singleton set by
     `set_checkpointer` (the lifespan hook does this in production).
  3. `GraphPipelineRunner` invocations pass a stable `thread_id` in
     `config.configurable` so the checkpointer can persist state across
     turns (HITL gates need this from day one — codex P0-2).

Production uses `AsyncPostgresSaver`; tests run with `InMemorySaver` so
they don't need a Postgres instance. See
`tasks/source-audit-2026-04-25-sprint-0-1.md` F-1/F-1b for the rationale
behind picking the async variant + lifespan-managed CM.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.pipeline.graph import checkpointer as cp_module
from src.pipeline.graph.builder import build_graph


def test_build_graph_compiles_without_checkpointer() -> None:
    """Default `build_graph()` must still compile (None checkpointer)."""
    graph = build_graph(checkpointer=None)
    assert graph is not None
    assert getattr(graph, "checkpointer", None) is None


def test_build_graph_attaches_explicit_checkpointer() -> None:
    """An explicitly-passed checkpointer must be on the compiled graph."""
    saver = InMemorySaver()
    graph = build_graph(checkpointer=saver)
    assert graph.checkpointer is saver


def test_build_graph_reads_module_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no kwarg is passed, the module-level singleton wins."""
    saver = InMemorySaver()
    monkeypatch.setattr(cp_module, "_checkpointer", saver)
    graph = build_graph()
    assert graph.checkpointer is saver


def test_set_checkpointer_round_trip() -> None:
    """`set_checkpointer` / `get_checkpointer` must round-trip cleanly."""
    saver = InMemorySaver()
    cp_module.set_checkpointer(saver)
    try:
        assert cp_module.get_checkpointer() is saver
    finally:
        cp_module.set_checkpointer(None)
    assert cp_module.get_checkpointer() is None


def test_runner_invocations_thread_thread_id() -> None:
    """Runner must pass a stable thread_id keyed on case_id.

    The checkpointer needs `config.configurable.thread_id` to persist
    state. Without this, `interrupt()` / `Command(resume=...)` cannot
    work — which Sprint 1's gate stubs depend on (codex P0-2).

    Sprint 1 1.A1.8: the runner now drives the graph via `astream`
    (through `stream_to_sse`) and reads terminal state via
    `aget_state`. Both must receive the same case-id-keyed config.
    """
    import asyncio
    from unittest.mock import patch

    from src.pipeline.graph.runner import GraphPipelineRunner
    from src.shared.case_state import CaseDomainEnum, CaseState

    case = CaseState(
        case_id="11111111-1111-1111-1111-111111111111",
        domain=CaseDomainEnum.traffic_violation,
        parties=[
            {"name": "Prosecution", "role": "prosecution"},
            {"name": "John Doe", "role": "accused"},
        ],
        case_metadata={
            "filed_date": "2026-03-15",
            "category": "traffic",
            "subcategory": "speeding",
            "offence_code": "RTA-S64",
            "jurisdiction_valid": True,
            "jurisdiction_issues": [],
        },
    )

    runner = GraphPipelineRunner(checkpointer=InMemorySaver())

    astream_configs: list = []
    aget_state_configs: list = []

    async def _fake_astream(_state, *, config=None, stream_mode=None):  # type: ignore[no-untyped-def]
        astream_configs.append(config)
        if False:  # generator marker
            yield None

    class _FakeSnapshot:
        values = {"case": case}

    async def _fake_aget_state(config):  # type: ignore[no-untyped-def]
        aget_state_configs.append(config)
        return _FakeSnapshot()

    with (
        patch.object(runner._graph, "astream", new=_fake_astream),
        patch.object(runner._graph, "aget_state", new=_fake_aget_state),
    ):
        asyncio.run(runner.run(case))

    expected = str(case.case_id)
    assert astream_configs and astream_configs[0] is not None, (
        "runner must pass config to astream (via stream_to_sse)"
    )
    assert astream_configs[0].get("configurable", {}).get("thread_id") == expected, (
        f"astream thread_id should be the case_id; got {astream_configs[0]!r}"
    )
    # Sprint 1 1.C3a.1 — every run must carry env metadata so LangSmith
    # traces are filterable per environment.
    metadata = astream_configs[0].get("metadata") or {}
    assert metadata.get("env"), (
        f"astream config must include metadata.env (1.C3a.1); got {metadata!r}"
    )
    assert metadata.get("case_id") == expected, (
        f"astream config metadata must include case_id; got {metadata!r}"
    )
    assert aget_state_configs and aget_state_configs[0] is not None, (
        "runner must pass config to aget_state for terminal state read"
    )
    assert aget_state_configs[0].get("configurable", {}).get("thread_id") == expected, (
        f"aget_state thread_id should be the case_id; got {aget_state_configs[0]!r}"
    )
