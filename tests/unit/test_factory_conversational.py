"""Q1.4 — `conversational` flag in `_make_node`.

Two contracts:

1. **Default path is byte-identical.** `conversational=False` (today's
   only path) emits `llm_chunk` events with the existing wire shape.
   No regression to the SSE bridge or the structured-response
   contract.

2. **Conversational path swaps the wire format.** `conversational=True`:
   - builds the agent WITHOUT `response_format` (no ToolStrategy /
     strict schema — the model emits prose, not bound JSON),
   - prose deltas go through the Q1.1 coalescer → `llm_token` events,
   - tool-call chunks emit as `tool_call_delta` events,
   - NO `llm_chunk` events (those are JSON-mode).

The `message_id` field is per-assistant-turn so the frontend (Q1.8)
can concatenate prose across deltas.
"""

from __future__ import annotations

import pytest


def _patch_noop_structuring(monkeypatch, factory_module) -> None:
    """Mock `_init_structuring_model` to a no-op so tests that focus
    on streaming wire shape don't need to set up the structuring
    pass. The Q1.5-specific tests still patch it explicitly to
    exercise the artifact emission path."""

    class _StructuredModel:
        async def ainvoke(self, _messages):
            return None

    class _StructuringModel:
        def with_structured_output(self, _schema, strict=False):  # noqa: ARG002
            return _StructuredModel()

    monkeypatch.setattr(
        factory_module, "_init_structuring_model", lambda *_a, **_k: _StructuringModel()
    )


class TestConversationalFlagDefault:
    @pytest.mark.asyncio
    async def test_make_phase_node_intake_default_path_unchanged(self, monkeypatch):
        """Q1.4 must not regress today's behavior. `make_phase_node("intake")`
        with no flag passed → emits `llm_chunk` events, sets
        response_format, returns structured output."""
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        captured_kwargs: dict = {}

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="prose"), {}))
                    yield ("values", {"structured_response": {"jurisdiction": "sct"}})
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called")

        def _create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return _FakeAgent()

        monkeypatch.setattr(factory, "create_agent", _create_agent)
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory.make_phase_node("intake")

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}
        result = await node(state)

        # Default path: response_format IS set on the agent.
        assert captured_kwargs.get("response_format") is not None

        # Default path: emits `llm_chunk`, NOT `llm_token`.
        chunks = [e for e in published if e.get("event") == "llm_chunk"]
        tokens = [e for e in published if e.get("event") == "llm_token"]
        assert len(chunks) == 1
        assert chunks[0]["delta"] == "prose"
        assert tokens == []

        assert result == {"intake_output": {"jurisdiction": "sct"}}


class TestConversationalFlagOn:
    @pytest.mark.asyncio
    async def test_conversational_emits_llm_token_not_llm_chunk(self, monkeypatch):
        """`conversational=True` swaps prose emission from `llm_chunk`
        to `llm_token` (post-coalescer batched). No `llm_chunk` events
        should fire on this path."""
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)
        _patch_noop_structuring(monkeypatch, factory)

        captured_kwargs: dict = {}

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="Examining "), {}))
                    yield ("messages", (AIMessageChunk(content="the notice."), {}))
                    yield ("values", {"structured_response": None, "messages": []})
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called")

        def _create_agent(**kwargs):
            captured_kwargs.update(kwargs)
            return _FakeAgent()

        monkeypatch.setattr(factory, "create_agent", _create_agent)
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory._make_node(
            phase_or_scope="intake",
            allowed_tool_names=["parse_document"],
            schema=dict,
            use_strict_response_format=False,
            conversational=True,
        )

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}
        await node(state)

        # Conversational path: response_format is NOT set.
        assert captured_kwargs.get("response_format") is None

        chunks = [e for e in published if e.get("event") == "llm_chunk"]
        tokens = [e for e in published if e.get("event") == "llm_token"]

        assert chunks == []  # no JSON-mode events
        assert tokens, "expected at least one llm_token event"
        # Coalescer batches on close — full prose recoverable across deltas.
        assert "".join(t["delta"] for t in tokens) == "Examining the notice."
        # Every token event carries phase + a stable message_id for accumulation.
        assert all(t["phase"] == "intake" for t in tokens)
        assert all(t["message_id"] for t in tokens)

    @pytest.mark.asyncio
    async def test_conversational_emits_tool_call_delta_for_tool_chunks(
        self, monkeypatch
    ):
        """Tool-call chunks from the messages stream emit as
        `tool_call_delta` events so the frontend (Q1.9) can render
        the `<ToolCallChip>` with args streaming in."""
        from langchain_core.messages import AIMessageChunk
        from langchain_core.messages.tool import ToolCallChunk

        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)
        _patch_noop_structuring(monkeypatch, factory)

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="About to call. "), {}))
                    yield (
                        "messages",
                        (
                            AIMessageChunk(
                                content="",
                                tool_call_chunks=[
                                    ToolCallChunk(
                                        name="parse_document",
                                        args='{"file_id": "fil',
                                        id="tc-1",
                                        index=0,
                                    )
                                ],
                            ),
                            {},
                        ),
                    )
                    yield (
                        "messages",
                        (
                            AIMessageChunk(
                                content="",
                                tool_call_chunks=[
                                    ToolCallChunk(
                                        name=None,
                                        args='e-abc"}',
                                        id="tc-1",
                                        index=0,
                                    )
                                ],
                            ),
                            {},
                        ),
                    )
                    yield ("values", {"structured_response": None, "messages": []})
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called")

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory._make_node(
            phase_or_scope="intake",
            allowed_tool_names=["parse_document"],
            schema=dict,
            use_strict_response_format=False,
            conversational=True,
        )

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}
        await node(state)

        deltas = [e for e in published if e.get("event") == "tool_call_delta"]
        assert len(deltas) == 2
        # Concatenate the args_delta payloads → reconstruct the full args JSON.
        full_args = "".join(d["args_delta"] for d in deltas)
        assert full_args == '{"file_id": "file-abc"}'
        # tool_call_id stable across both deltas.
        assert {d["tool_call_id"] for d in deltas} == {"tc-1"}
        # Tool name carried on the first chunk (where `name` was set).
        assert deltas[0]["name"] == "parse_document"

        # Prose still emits as `llm_token` (separate from the tool-call deltas).
        tokens = [e for e in published if e.get("event") == "llm_token"]
        assert tokens, "expected prose llm_token events alongside tool_call_delta"

    @pytest.mark.asyncio
    async def test_conversational_runs_structuring_pass_after_stream(self, monkeypatch):
        """Q1.5: after the conversational `astream` loop ends, the
        factory runs `model.with_structured_output(schema, strict=True)
        .ainvoke(messages)` to produce the schema-bound artifact.
        Result lands in `result["structured_response"]` exactly like
        the JSON-mode path."""
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        history_seen: list = []

        class _StructuredModel:
            async def ainvoke(self, messages):
                history_seen.extend(messages)
                return {"jurisdiction": "sct", "domain": "small_claims"}

        class _StructuringModel:
            def with_structured_output(self, schema, strict=False):
                assert strict is True, "structuring pass must use strict=True"
                assert schema is dict, "structuring pass must use the phase schema"
                return _StructuredModel()

        monkeypatch.setattr(
            factory, "_init_structuring_model", lambda *_a, **_k: _StructuringModel()
        )

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="Reasoning."), {}))
                    yield (
                        "values",
                        {
                            "messages": [
                                {"role": "user", "content": "extract"},
                                {"role": "assistant", "content": "Reasoning."},
                            ],
                            "structured_response": None,
                        },
                    )
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called")

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory._make_node(
            phase_or_scope="intake",
            allowed_tool_names=[],
            schema=dict,
            use_strict_response_format=False,
            conversational=True,
        )

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}
        result = await node(state)

        # Structured artifact lives at the existing key.
        assert result["intake_output"] == {"jurisdiction": "sct", "domain": "small_claims"}
        # Structuring pass saw the same message history the conversational stream produced.
        assert len(history_seen) == 2

        # `structured_artifact` SSE event emitted with the artifact JSON.
        artifacts = [e for e in published if e.get("event") == "structured_artifact"]
        assert len(artifacts) == 1
        assert artifacts[0]["artifact"] == {"jurisdiction": "sct", "domain": "small_claims"}
        assert artifacts[0]["phase"] == "intake"

    @pytest.mark.asyncio
    async def test_structuring_pass_failure_raises(self, monkeypatch):
        """Structuring pass failure has no fallback — Q1.2's
        agent_failed policy already covers this; the structured
        response must not silently default to None."""
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        class _StructuredModel:
            async def ainvoke(self, messages):
                raise ValueError("schema validation failed")

        class _StructuringModel:
            def with_structured_output(self, schema, strict=False):
                return _StructuredModel()

        monkeypatch.setattr(
            factory, "_init_structuring_model", lambda *_a, **_k: _StructuringModel()
        )

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="x"), {}))
                    yield ("values", {"messages": [], "structured_response": None})
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called")

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory._make_node(
            phase_or_scope="intake",
            allowed_tool_names=[],
            schema=dict,
            use_strict_response_format=False,
            conversational=True,
        )

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}

        with pytest.raises(ValueError, match="schema validation failed"):
            await node(state)

        # No structured_artifact emitted on failure.
        artifacts = [e for e in published if e.get("event") == "structured_artifact"]
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_default_path_does_not_run_structuring_pass(self, monkeypatch):
        """JSON mode keeps using the agent's bound `structured_response`.
        No structuring pass call — that path would double-charge OpenAI."""
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents import factory

        async def _fake_publish(_cid, _ev):
            pass

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        struct_called = {"count": 0}

        def _init(*_a, **_k):
            struct_called["count"] += 1
            raise AssertionError("structuring pass must not run in JSON mode")

        monkeypatch.setattr(factory, "_init_structuring_model", _init)

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="x"), {}))
                    yield ("values", {"structured_response": {"jurisdiction": "sct"}})
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called")

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory.make_phase_node("intake")

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}
        result = await node(state)

        assert result == {"intake_output": {"jurisdiction": "sct"}}
        assert struct_called["count"] == 0

    @pytest.mark.asyncio
    async def test_conversational_message_id_changes_across_assistant_turns(
        self, monkeypatch
    ):
        """A new assistant turn (separated by a tool result in the
        message stream) gets a fresh `message_id` so the consumer
        renders distinct bubbles."""
        from langchain_core.messages import AIMessageChunk, ToolMessage

        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)
        _patch_noop_structuring(monkeypatch, factory)

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="Step 1."), {}))
                    yield (
                        "messages",
                        (ToolMessage(content="result", tool_call_id="t1"), {}),
                    )
                    yield ("messages", (AIMessageChunk(content="Step 2."), {}))
                    yield ("values", {"structured_response": None, "messages": []})
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called")

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory._make_node(
            phase_or_scope="intake",
            allowed_tool_names=[],
            schema=dict,
            use_strict_response_format=False,
            conversational=True,
        )

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}
        await node(state)

        tokens = [e for e in published if e.get("event") == "llm_token"]
        message_ids = {t["message_id"] for t in tokens}
        assert len(message_ids) == 2, (
            f"expected 2 distinct message_ids across the tool boundary, got {message_ids}"
        )
