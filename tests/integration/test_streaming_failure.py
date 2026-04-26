"""Q1.11 — Risk #1 regression: no double-call when astream fails
mid-stream.

The Q1.2 `streaming_started` flag in `_make_node` (factory.py) gates
the post-failure fallback. Once `astream` has emitted at least one
chunk, any subsequent failure must NOT fall back to `ainvoke` — that
would re-execute tools and double-charge the model. Instead,
`agent_failed` is published and the exception re-raises so the
orchestrator's failure path takes over.

This canary locks the contract in. Reverting Q1.2 (e.g. replacing the
gated branch with a broad `except → ainvoke`) makes this test fail in
two specific ways:
  1. `ainvoke` gets called → asserted-zero counter trips.
  2. `agent_failed` event is missing → asserted-present check fails.

Note on placement: this lives in `tests/integration/` because it
exercises the failure-path interaction between `astream`, the
`streaming_started` gate, the SSE emission contract, and the
orchestrator hand-off — territory the unit suite (`test_factory_
conversational.py`) stops short of. It does not require a live DB or
Redis: `publish_agent_event` is captured via monkeypatch, and the
fault-injecting fake agent stands in for a real LLM. A second-tier
e2e test against real Postgres + a fault-injecting LangChain model
remains queued as a follow-up; this canary is the CI-cheap version
that runs on every push.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FaultInjected(RuntimeError):
    """Tagged exception type so the test can distinguish a controlled
    mid-stream failure from any other RuntimeError that might surface."""


@pytest.mark.asyncio
async def test_astream_failure_after_first_chunk_does_not_fall_back_to_ainvoke(
    monkeypatch,
):
    """Astream yields one chunk (streaming_started=True), then raises.
    Factory must:
      - NOT call `ainvoke` (no double-execute),
      - publish exactly one `agent_failed` event for phase=intake,
      - re-raise the original exception.
    """
    monkeypatch.setenv("PIPELINE_CONVERSATIONAL_STREAMING_PHASES", "intake")

    from langchain_core.messages import AIMessageChunk

    from src.pipeline.graph.agents import factory

    published: list[dict] = []

    async def _fake_publish(case_id, event):
        published.append({"case_id": case_id, **event})

    monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

    ainvoke_calls = 0

    class _FaultInjectingAgent:
        def astream(self, *_args, **_kwargs):
            async def _gen():
                # First yield: a prose chunk. This sets
                # streaming_started=True inside the factory loop.
                yield ("messages", (AIMessageChunk(content="thinking…"), {}))
                # Second yield: simulated upstream failure mid-stream.
                raise _FaultInjected("simulated upstream failure")

            return _gen()

        async def ainvoke(self, *_args, **_kwargs):
            nonlocal ainvoke_calls
            ainvoke_calls += 1
            return {"structured_response": None, "messages": []}

    monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FaultInjectingAgent())
    monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
    monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

    node = factory.make_phase_node("intake")

    state = {"case": SimpleNamespace(case_id="case-q111"), "extra_instructions": {}}

    with pytest.raises(_FaultInjected):
        await node(state)

    # Risk #1 contract — ainvoke must NEVER be called when the stream
    # has already emitted side effects. A revert of Q1.2's
    # streaming_started gate would land here as ainvoke_calls == 1.
    assert ainvoke_calls == 0, (
        f"ainvoke fallback fired after streaming_started=True "
        f"(ainvoke_calls={ainvoke_calls}); Q1.2 gate appears reverted"
    )

    # SSE bridge contract — exactly one agent_failed event for the
    # right phase, carrying the error class (never the message).
    failures = [e for e in published if e.get("event") == "agent_failed"]
    assert len(failures) == 1, (
        f"expected exactly 1 agent_failed event, got {len(failures)}: {failures}"
    )
    assert failures[0]["agent"] == "intake"
    assert failures[0]["error_class"] == "_FaultInjected"
    # Defense in depth: the message must NOT leak into the SSE payload
    # (potential PII vector — see factory.py:409 comment).
    assert "simulated upstream failure" not in str(failures[0])

    # The presence of agent_failed itself proves streaming_started
    # latched: factory.py only emits agent_failed on the post-chunk
    # failure branch (line 410). Pre-chunk failures fall back to
    # ainvoke and never publish agent_failed. So `len(failures)==1`
    # above is also the witness that we exercised the right branch
    # — no separate llm_token/llm_chunk witness needed.
