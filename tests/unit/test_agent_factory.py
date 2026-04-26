"""Sprint 1 1.A1.4 — phase + research subagent factory unit tests.

Asserts each phase / scope's tool list and schema explicitly. Does NOT
invoke real models — that's covered by integration tests in 1.A1.5+.
"""

from __future__ import annotations

import pytest


def test_phase_tool_names_are_explicit_and_least_privilege():
    from src.pipeline.graph.agents import factory

    assert factory.PHASE_TOOL_NAMES == {
        "intake": ["parse_document"],
        "synthesis": ["search_precedents"],
        "audit": [],
    }


def test_research_tool_names_are_explicit_and_least_privilege():
    from src.pipeline.graph.agents import factory

    assert factory.RESEARCH_TOOL_NAMES == {
        "evidence": ["parse_document"],
        "facts": ["parse_document"],
        "witnesses": ["parse_document"],
        "law": ["search_legal_rules", "search_precedents"],
    }


def test_audit_phase_has_zero_tools():
    """Auditor independence guarantee — Sprint 0.4 §2 / 0.5 §5 D-1.

    No retrieval, no document parse: the auditor inspects completed
    state and may not introduce new evidence.
    """
    from src.pipeline.graph.agents import factory

    assert factory.PHASE_TOOL_NAMES["audit"] == []


def test_intake_phase_has_only_parse_document():
    from src.pipeline.graph.agents import factory

    assert factory.PHASE_TOOL_NAMES["intake"] == ["parse_document"]


def test_law_is_only_research_subagent_with_search_tools():
    from src.pipeline.graph.agents import factory

    search_tools = {"search_legal_rules", "search_precedents"}
    for scope, names in factory.RESEARCH_TOOL_NAMES.items():
        intersection = set(names) & search_tools
        if scope == "law":
            assert intersection == search_tools, (
                f"law subagent must hold both search tools, got {names!r}"
            )
        else:
            assert intersection == set(), (
                f"non-law subagent {scope!r} cannot hold search tools, got {names!r}"
            )


def test_phase_schemas_match_expected_classes():
    from src.pipeline.graph.agents import factory
    from src.pipeline.graph.schemas import AuditOutput, IntakeOutput, SynthesisOutput

    assert {
        "intake": IntakeOutput,
        "synthesis": SynthesisOutput,
        "audit": AuditOutput,
    } == factory.PHASE_SCHEMAS


def test_research_schemas_match_expected_classes():
    from src.pipeline.graph.agents import factory
    from src.pipeline.graph.schemas import (
        EvidenceResearch,
        FactsResearch,
        LawResearch,
        WitnessesResearch,
    )

    assert {
        "evidence": EvidenceResearch,
        "facts": FactsResearch,
        "witnesses": WitnessesResearch,
        "law": LawResearch,
    } == factory.RESEARCH_SCHEMAS


def test_audit_phase_uses_strict_response_format():
    """`AuditOutput` is the one phase using OpenAI strict JSON schema
    (Sprint 0.5 §5 D-4)."""
    from src.pipeline.graph.schemas import AuditOutput

    config = AuditOutput.model_config
    assert config.get("extra") == "forbid"
    assert config.get("strict") is True


def test_other_phases_extra_forbid_but_not_strict():
    from src.pipeline.graph.schemas import IntakeOutput, SynthesisOutput

    for cls in (IntakeOutput, SynthesisOutput):
        config = cls.model_config
        assert config.get("extra") == "forbid", f"{cls.__name__} must use extra=forbid"
        assert config.get("strict") is not True, (
            f"{cls.__name__} must NOT use strict=True (only AuditOutput is strict)"
        )


def test_make_phase_node_returns_callable_for_each_phase():
    from src.pipeline.graph.agents import factory

    for phase in ("intake", "synthesis", "audit"):
        node = factory.make_phase_node(phase)
        assert callable(node)
        assert node.__name__ == f"phase_node_{phase}"


def test_make_research_subagent_returns_callable_for_each_scope():
    from src.pipeline.graph.agents import factory

    for scope in ("evidence", "facts", "witnesses", "law"):
        node = factory.make_research_subagent(scope)
        assert callable(node)
        assert node.__name__ == f"phase_node_research-{scope}"


def test_unknown_phase_raises_value_error():
    from src.pipeline.graph.agents import factory

    with pytest.raises(ValueError, match="Unknown phase"):
        factory.make_phase_node("intakeology")


def test_unknown_research_scope_raises_value_error():
    from src.pipeline.graph.agents import factory

    with pytest.raises(ValueError, match="Unknown research scope"):
        factory.make_research_subagent("paranormal")


def test_phase_middleware_stack_includes_all_four_hooks():
    from src.pipeline.graph.agents import factory
    from src.pipeline.graph.middleware import (
        audit_tool_call,
        cancel_check,
        sse_tool_emitter,
        token_usage_emitter,
    )

    assert set(factory.PHASE_MIDDLEWARE) == {
        cancel_check,
        sse_tool_emitter,
        audit_tool_call,
        token_usage_emitter,
    }


# ---------------------------------------------------------------------------
# _extract_source_ids_from_messages — Sprint 3 3.B.5
# ---------------------------------------------------------------------------


class TestExtractSourceIdsFromMessages:
    def test_picks_source_ids_off_tool_message_artifacts(self):
        from langchain_core.documents import Document
        from langchain_core.messages import ToolMessage

        from src.pipeline.graph.agents.factory import _extract_source_ids_from_messages

        msg = ToolMessage(
            content="x",
            tool_call_id="tc-1",
            artifact=[
                Document(page_content="a", metadata={"source_id": "f-1:abc"}),
                Document(page_content="b", metadata={"source_id": "f-2:def"}),
            ],
        )
        assert _extract_source_ids_from_messages([msg]) == ["f-1:abc", "f-2:def"]

    def test_dedupes_across_messages(self):
        from langchain_core.documents import Document
        from langchain_core.messages import ToolMessage

        from src.pipeline.graph.agents.factory import _extract_source_ids_from_messages

        m1 = ToolMessage(
            content="",
            tool_call_id="t1",
            artifact=[Document(page_content="a", metadata={"source_id": "f:1"})],
        )
        m2 = ToolMessage(
            content="",
            tool_call_id="t2",
            artifact=[
                Document(page_content="b", metadata={"source_id": "f:1"}),
                Document(page_content="c", metadata={"source_id": "f:2"}),
            ],
        )
        assert _extract_source_ids_from_messages([m1, m2]) == ["f:1", "f:2"]

    def test_skips_messages_without_artifact(self):
        from langchain_core.messages import AIMessage, ToolMessage

        from src.pipeline.graph.agents.factory import _extract_source_ids_from_messages

        plain = ToolMessage(content="plain", tool_call_id="t")
        ai = AIMessage(content="hi")
        assert _extract_source_ids_from_messages([plain, ai]) == []

    def test_skips_documents_without_source_id(self):
        from langchain_core.documents import Document
        from langchain_core.messages import ToolMessage

        from src.pipeline.graph.agents.factory import _extract_source_ids_from_messages

        msg = ToolMessage(
            content="",
            tool_call_id="t",
            artifact=[
                Document(page_content="a", metadata={"file_id": "no-source-id"}),
                Document(page_content="b", metadata={"source_id": "f:1"}),
            ],
        )
        assert _extract_source_ids_from_messages([msg]) == ["f:1"]


# ── Token-streaming chunk extraction (regression: empty-content
# AIMessageChunks under ToolStrategy mode silently dropped llm_chunk
# events because the structured response lives in tool_call_chunks.args).
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_returns_plain_string_content(self) -> None:
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents.factory import _chunk_text

        chunk = AIMessageChunk(content="Hello world")
        assert _chunk_text(chunk) == "Hello world"

    def test_returns_empty_for_chunk_with_no_text_or_tools(self) -> None:
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents.factory import _chunk_text

        chunk = AIMessageChunk(content="")
        assert _chunk_text(chunk) == ""

    def test_extracts_text_from_multimodal_content_parts(self) -> None:
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents.factory import _chunk_text

        chunk = AIMessageChunk(
            content=[
                {"type": "text", "text": "Reasoning: "},
                {"type": "text", "text": "the suspect was speeding"},
                {"type": "image_url", "image_url": "https://example.com/x.png"},
            ]
        )
        assert _chunk_text(chunk) == "Reasoning: the suspect was speeding"

    def test_falls_back_to_tool_call_chunks_when_content_is_empty(self) -> None:
        """Regression: with response_format=ToolStrategy(schema), the model's
        structured output streams as tool-call args, NOT as content. Without
        this fallback the SSE bridge sees zero llm_chunk events even though
        the model is actively producing tokens."""
        from langchain_core.messages import AIMessageChunk
        from langchain_core.messages.tool import ToolCallChunk

        from src.pipeline.graph.agents.factory import _chunk_text

        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                ToolCallChunk(
                    name="IntakeOutput",
                    args='{"jurisdiction":',
                    id="call_1",
                    index=0,
                )
            ],
        )
        assert _chunk_text(chunk) == '{"jurisdiction":'

    def test_concatenates_multiple_tool_call_chunk_deltas(self) -> None:
        from langchain_core.messages import AIMessageChunk
        from langchain_core.messages.tool import ToolCallChunk

        from src.pipeline.graph.agents.factory import _chunk_text

        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                ToolCallChunk(name=None, args='"sct"', id=None, index=0),
                ToolCallChunk(name=None, args=', "valid":', id=None, index=0),
                ToolCallChunk(name=None, args=" true}", id=None, index=0),
            ],
        )
        assert _chunk_text(chunk) == '"sct", "valid": true}'

    def test_prefers_text_content_over_tool_call_chunks_when_both_present(self) -> None:
        from langchain_core.messages import AIMessageChunk
        from langchain_core.messages.tool import ToolCallChunk

        from src.pipeline.graph.agents.factory import _chunk_text

        chunk = AIMessageChunk(
            content="natural language reasoning",
            tool_call_chunks=[
                ToolCallChunk(name=None, args='{"x": 1}', id=None, index=0),
            ],
        )
        assert _chunk_text(chunk) == "natural language reasoning"


# ── End-to-end node streaming: prove llm_chunk SSE events fire when the
# agent emits ToolStrategy-style chunks (the previously-broken case).
# ---------------------------------------------------------------------------


class TestNodeStreamsLlmChunks:
    @pytest.mark.asyncio
    async def test_node_publishes_llm_chunk_per_message_chunk(self, monkeypatch):
        """Drive _node with a fake agent that yields multi-mode astream
        tuples — assert one llm_chunk SSE event fires per chunk.

        Covers both content-bearing chunks and ToolStrategy chunks (where
        the structured response streams as tool_call_chunks.args).
        """
        from langchain_core.messages import AIMessageChunk
        from langchain_core.messages.tool import ToolCallChunk

        from src.pipeline.graph.agents import factory

        # Capture every event published over the SSE bridge.
        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        # Fake agent.astream yielding (mode, payload) tuples in the
        # multi-mode shape: 3 message chunks + a final values payload.
        chunks = [
            ("messages", (AIMessageChunk(content="Examining "), {})),
            ("messages", (AIMessageChunk(content="the notice."), {})),
            (
                "messages",
                (
                    AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            ToolCallChunk(
                                name="IntakeOutput",
                                args='{"jurisdiction": "sct"}',
                                id="c1",
                                index=0,
                            )
                        ],
                    ),
                    {},
                ),
            ),
            ("values", {"structured_response": {"jurisdiction": "sct"}, "messages": []}),
        ]

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    for item in chunks:
                        yield item
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                raise AssertionError("ainvoke must not be called when astream succeeds")

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        # Stub the prompt + tool resolvers so we don't hit LangSmith / DB.
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory.make_phase_node("intake")

        from types import SimpleNamespace

        state = {
            "case": SimpleNamespace(case_id="case-xyz"),
            "extra_instructions": {},
        }
        result = await node(state)

        # The structured response from the final "values" payload survives.
        assert result == {"intake_output": {"jurisdiction": "sct"}}

        # Three chunks → three llm_chunk SSE events with the right deltas.
        chunk_events = [e for e in published if e.get("event") == "llm_chunk"]
        assert [e["delta"] for e in chunk_events] == [
            "Examining ",
            "the notice.",
            '{"jurisdiction": "sct"}',
        ]
        # Every event is tagged with the LangGraph node id + the case id
        # so the SSE bridge routes them to the right agent card.
        assert all(e["agent"] == "intake" for e in chunk_events)
        assert all(e["case_id"] == "case-xyz" for e in chunk_events)


# ── Q1.2: streaming_started flag — Risk #1 (no double-call after first chunk).
# ---------------------------------------------------------------------------


class TestStreamingStartedFallbackPolicy:
    """Q1.2 contract: once any observable side-effect has happened
    (first message chunk OR first values payload OR first tool call),
    the broad `except Exception → ainvoke` fallback is unsafe — it
    would re-execute tools and double-charge OpenAI. Replaced with a
    `streaming_started` flag: pre-chunk failures still fall back to
    ainvoke (back-compat); post-chunk failures emit `agent_failed`
    SSE and re-raise."""

    @pytest.mark.asyncio
    async def test_post_chunk_failure_emits_agent_failed_and_raises(self, monkeypatch):
        """Risk #1 regression: mock astream to raise after one chunk.
        The factory MUST emit `agent_failed` and propagate the
        exception — NOT silently retry via ainvoke."""
        from langchain_core.messages import AIMessageChunk

        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        ainvoke_called = {"count": 0}

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("messages", (AIMessageChunk(content="streaming…"), {}))
                    raise RuntimeError("upstream blew up after first chunk")
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                ainvoke_called["count"] += 1
                return {"structured_response": {"jurisdiction": "sct"}}

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory.make_phase_node("intake")

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}

        with pytest.raises(RuntimeError, match="upstream blew up"):
            await node(state)

        # No silent retry — ainvoke was NOT called as a fallback.
        assert ainvoke_called["count"] == 0

        # `agent_failed` SSE was emitted with the contract fields.
        failed_events = [e for e in published if e.get("event") == "agent_failed"]
        assert len(failed_events) == 1
        ev = failed_events[0]
        assert ev["agent"] == "intake"
        assert ev["case_id"] == "case-xyz"
        assert ev["error_class"] == "RuntimeError"
        # No PII: the original error MESSAGE must NOT be in the event.
        assert "upstream blew up" not in str(ev)

    @pytest.mark.asyncio
    async def test_pre_chunk_failure_falls_back_to_ainvoke(self, monkeypatch):
        """Back-compat: when astream raises BEFORE any observable
        side-effect (no message chunk, no values payload), the
        existing ainvoke fallback is still safe — no tools were
        invoked, so retrying once doesn't double-execute anything."""
        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        ainvoke_called = {"count": 0}

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    if False:
                        yield  # pragma: no cover (make this an async generator)
                    raise RuntimeError("astream init failed")
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                ainvoke_called["count"] += 1
                return {"structured_response": {"jurisdiction": "sct"}}

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory.make_phase_node("intake")

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}
        result = await node(state)

        # ainvoke fallback ran exactly once and produced the structured response.
        assert ainvoke_called["count"] == 1
        assert result == {"intake_output": {"jurisdiction": "sct"}}

        # No agent_failed SSE — pre-chunk failures don't surface as
        # terminal events, the fallback masks them per the existing contract.
        failed_events = [e for e in published if e.get("event") == "agent_failed"]
        assert failed_events == []

    @pytest.mark.asyncio
    async def test_post_values_failure_also_propagates(self, monkeypatch):
        """A failure AFTER a `values` payload (graph state checkpoint
        visible to consumers) is also post-chunk — same policy."""
        from src.pipeline.graph.agents import factory

        published: list[dict] = []

        async def _fake_publish(case_id, event):
            published.append({"case_id": case_id, **event})

        monkeypatch.setattr(factory, "publish_agent_event", _fake_publish)

        ainvoke_called = {"count": 0}

        class _FakeAgent:
            def astream(self, *_args, **_kwargs):
                async def _gen():
                    yield ("values", {"messages": [{"role": "tool"}], "structured_response": None})
                    raise RuntimeError("post-tool failure")
                return _gen()

            async def ainvoke(self, *_args, **_kwargs):
                ainvoke_called["count"] += 1
                return {}

        monkeypatch.setattr(factory, "create_agent", lambda **_kw: _FakeAgent())
        monkeypatch.setattr(factory, "_resolve_prompt", lambda *_a, **_k: "stub")
        monkeypatch.setattr(factory, "_filter_tools", lambda *_a, **_k: [])

        node = factory.make_phase_node("intake")

        from types import SimpleNamespace

        state = {"case": SimpleNamespace(case_id="case-xyz"), "extra_instructions": {}}

        with pytest.raises(RuntimeError, match="post-tool failure"):
            await node(state)

        assert ainvoke_called["count"] == 0
        failed_events = [e for e in published if e.get("event") == "agent_failed"]
        assert len(failed_events) == 1
