"""Sprint 1 1.A1.4 — tool scoping behavioural tests (codex P2-7).

The breakdown's acceptance: "auditor invocation cannot reach search_*;
intake invocation cannot reach search_*". Translates to: the constructed
agents have explicit, restricted tool sets, and there is no path by which
those constructions silently widen.

These tests spy on `langchain.agents.create_agent` so we capture the
exact `tools=` kwarg the factory passes — the runtime guarantee is then
that the model literally cannot bind a tool that's not in the list.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _force_json_mode(monkeypatch):
    """These tests assert factory tool-scoping policy via a stub agent
    that only implements `ainvoke`. With Q1.6 default-on, intake hits
    the conversational `astream` path which the stub doesn't speak.
    Force JSON mode so the stub remains valid — the tool-scoping
    assertions are independent of streaming wire shape."""
    monkeypatch.setenv("PIPELINE_CONVERSATIONAL_STREAMING_PHASES", "")


class _StubCase:
    case_id = "11111111-1111-1111-1111-111111111111"
    domain_vector_store_id = "vs-stub"


def _stub_state() -> dict:
    return {"case": _StubCase()}


async def _capture_create_agent_kwargs(monkeypatch, node):
    """Drive the factory-built node once and return the kwargs that
    `create_agent` was invoked with."""
    from src.pipeline.graph.agents import factory

    captured: dict = {}

    class _StubAgent:
        async def ainvoke(self, _state):
            return {"structured_response": None}

    def _spy(**kwargs):
        captured.update(kwargs)
        return _StubAgent()

    monkeypatch.setattr(factory, "create_agent", _spy)
    await node(_stub_state())
    return captured


async def test_audit_phase_passes_zero_tools_to_create_agent(monkeypatch):
    from src.pipeline.graph.agents import factory

    node = factory.make_phase_node("audit")
    kwargs = await _capture_create_agent_kwargs(monkeypatch, node)

    assert kwargs["tools"] == [], (
        f"auditor must be constructed with tools=[]; got {kwargs['tools']!r}"
    )


async def test_intake_phase_only_gets_parse_document(monkeypatch):
    from src.pipeline.graph.agents import factory

    node = factory.make_phase_node("intake")
    kwargs = await _capture_create_agent_kwargs(monkeypatch, node)

    tool_names = [t.name for t in kwargs["tools"]]
    assert tool_names == ["parse_document"], (
        f"intake must hold only parse_document; got {tool_names!r}"
    )


async def test_intake_invocation_cannot_reach_search_tools(monkeypatch):
    from src.pipeline.graph.agents import factory

    node = factory.make_phase_node("intake")
    kwargs = await _capture_create_agent_kwargs(monkeypatch, node)

    bound = {t.name for t in kwargs["tools"]}
    assert "search_precedents" not in bound
    assert "search_legal_rules" not in bound
    assert "search_domain_guidance" not in bound


async def test_audit_invocation_cannot_reach_any_tool(monkeypatch):
    from src.pipeline.graph.agents import factory

    node = factory.make_phase_node("audit")
    kwargs = await _capture_create_agent_kwargs(monkeypatch, node)

    assert kwargs["tools"] == []


async def test_law_research_subagent_holds_both_search_tools(monkeypatch):
    from src.pipeline.graph.agents import factory

    node = factory.make_research_subagent("law")
    kwargs = await _capture_create_agent_kwargs(monkeypatch, node)

    bound = {t.name for t in kwargs["tools"]}
    # The new canonical name `search_legal_rules` aliases to the existing
    # `search_domain_guidance` registration; both are acceptable here.
    assert "search_precedents" in bound
    assert "search_legal_rules" in bound or "search_domain_guidance" in bound, (
        f"law subagent missing legal-rules tool; got {bound!r}"
    )


async def test_non_law_research_subagents_cannot_reach_search_tools(monkeypatch):
    from src.pipeline.graph.agents import factory

    for scope in ("evidence", "facts", "witnesses"):
        node = factory.make_research_subagent(scope)
        kwargs = await _capture_create_agent_kwargs(monkeypatch, node)
        bound = {t.name for t in kwargs["tools"]}
        assert "search_precedents" not in bound, f"{scope} must not hold search_precedents"
        assert "search_legal_rules" not in bound, f"{scope} must not hold search_legal_rules"
        assert "search_domain_guidance" not in bound, (
            f"{scope} must not hold search_domain_guidance"
        )


async def test_create_agent_kwargs_use_state_schema_and_middleware(monkeypatch):
    """Make sure every phase agent constructs with `state_schema=CaseAwareState`
    and the full middleware stack — otherwise telemetry breaks silently."""
    from src.pipeline.graph.agents import factory
    from src.pipeline.graph.middleware import CaseAwareState

    for phase in ("intake", "synthesis", "audit"):
        node = factory.make_phase_node(phase)
        kwargs = await _capture_create_agent_kwargs(monkeypatch, node)
        assert kwargs["state_schema"] is CaseAwareState
        # Order matters: cancel must run first to short-circuit before tools.
        assert kwargs["middleware"][0].__class__.__name__ in {
            "cancel_check",
            "before_modelMiddleware",
        } or "cancel_check" in str(kwargs["middleware"][0]), (
            f"first middleware should be cancel_check, got {kwargs['middleware'][0]!r}"
        )
