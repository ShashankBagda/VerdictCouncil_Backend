"""Unit tests for src.pipeline.runner.PipelineRunner."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipeline.runner import PipelineRunner
from src.shared.case_state import CaseDomainEnum, CaseState, CaseStatusEnum


def _make_chat_response(payload: dict, finish_reason: str = "stop"):
    """Build a mock ChatCompletion response."""
    message = SimpleNamespace(
        content=json.dumps(payload),
        tool_calls=None,
    )
    message_dict = MagicMock()
    message_dict.model_dump.return_value = {
        "role": "assistant",
        "content": json.dumps(payload),
    }
    choice = SimpleNamespace(
        message=message,
        finish_reason=finish_reason,
    )
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_tool_call_response(tool_calls: list[dict]):
    """Build a mock response that requests tool calls."""
    tc_objects = []
    for tc in tool_calls:
        obj = SimpleNamespace(
            id=tc["id"],
            function=SimpleNamespace(
                name=tc["name"],
                arguments=json.dumps(tc["arguments"]),
            ),
        )
        tc_objects.append(obj)

    message = SimpleNamespace(content=None, tool_calls=tc_objects)
    message.model_dump = MagicMock(
        return_value={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
                }
                for tc in tool_calls
            ],
        }
    )
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    return SimpleNamespace(choices=[choice], usage=usage)


def _minimal_state():
    return CaseState(
        domain=CaseDomainEnum.small_claims,
        parties=[{"name": "Plaintiff", "role": "claimant"}],
        case_metadata={"filed_date": "2026-03-01"},
    )


def _agent_config(model_tier: str = "lightweight"):
    return {
        "instruction": "You are a legal analysis agent.",
        "model_tier": model_tier,
    }


# ------------------------------------------------------------------ #
# run() executes gate 1 agents only (2-agent intake gate)
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_run_executes_gate1_agents():
    """run() only executes gate 1 (case-processing + complexity-routing).

    Subsequent gates are advanced by the judge via the gate advance endpoint
    and run through run_gate_job. The final state is awaiting_review_gate1.
    """
    client = AsyncMock()
    # Each agent returns an empty JSON (no fields modified)
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response({}))

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        result = await runner.run(_minimal_state())

    assert isinstance(result, CaseState)
    # Gate 1 has 2 agents: case-processing + complexity-routing
    assert client.chat.completions.create.call_count == 2
    assert result.status.value == "awaiting_review_gate1"


# ------------------------------------------------------------------ #
# Complexity-routing escalation is intercepted by the hook
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_complexity_routing_escalation_forced_to_processing():
    """When complexity-routing returns status=escalated, ComplexityEscalationHook
    forces it back to processing — the pipeline does NOT halt.  Gate 1 completes
    and returns awaiting_review_gate1.
    """
    client = AsyncMock()

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _make_chat_response({})
        # Agent 2 (complexity-routing) tries to set escalated
        if call_count == 2:
            return _make_chat_response({"status": "escalated"})
        return _make_chat_response({})

    client.chat.completions.create = AsyncMock(side_effect=mock_create)

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        result = await runner.run(_minimal_state())

    # Hook intercepted escalated → processing; gate 1 completed normally.
    assert result.status != CaseStatusEnum.escalated
    assert result.status.value == "awaiting_review_gate1"
    # Both gate-1 agents ran
    assert call_count == 2


# ------------------------------------------------------------------ #
# Governance fairness issues are surfaced to judge, not a halt
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_governance_fairness_issues_captured_without_halt():
    """GovernanceHaltHook no longer halts. Critical fairness issues from
    hearing-governance are surfaced to the judge at gate 4 review; the
    pipeline completes normally and status is NOT set to escalated.
    """
    client = AsyncMock()

    call_count = [0]

    async def mock_create(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # hearing-governance (the only gate-4 agent) returns fairness issue
            return _make_chat_response(
                {
                    "fairness_check": {
                        "critical_issues_found": True,
                        "audit_passed": False,
                        "issues": ["bias detected"],
                        "recommendations": [],
                    },
                }
            )
        return _make_chat_response({})

    client.chat.completions.create = AsyncMock(side_effect=mock_create)

    runner = PipelineRunner(client=client)

    state = _minimal_state()
    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        result = await runner.run_gate(state, "gate4")

    # GovernanceHaltHook logs but does not halt; status is gate-4 pause.
    assert result.status != CaseStatusEnum.escalated
    assert result.status.value == "awaiting_review_gate4"
    assert result.fairness_check is not None
    assert result.fairness_check.critical_issues_found is True


# ------------------------------------------------------------------ #
# Tool call loop: agent requests tool, receives result, then responds
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_tool_call_loop():
    """When an agent requests a tool call, runner executes it and loops."""
    client = AsyncMock()

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: agent requests timeline_construct tool
            return _make_tool_call_response(
                [
                    {
                        "id": "call_1",
                        "name": "timeline_construct",
                        "arguments": {"events": []},
                    }
                ]
            )
        # Second call: agent returns final response after tool result
        return _make_chat_response({"extracted_facts": {"timeline": []}})

    client.chat.completions.create = AsyncMock(side_effect=mock_create)

    runner = PipelineRunner(client=client)

    # Only run the fact-reconstruction agent (which has timeline_construct)
    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        await runner._run_agent("fact-reconstruction", _minimal_state())

    # Two LLM calls: first returned tool_call, second returned final response
    assert call_count == 2


# ------------------------------------------------------------------ #
# Agent config loading and model resolution
# ------------------------------------------------------------------ #
def test_build_tools_returns_schemas_for_agent():
    runner = PipelineRunner(client=AsyncMock())

    tools = runner._build_tools("evidence-analysis")
    tool_names = [t["function"]["name"] for t in tools]

    assert "parse_document" in tool_names
    assert "cross_reference" in tool_names


def test_build_tools_returns_empty_for_no_tool_agent():
    runner = PipelineRunner(client=AsyncMock())

    tools = runner._build_tools("complexity-routing")
    assert tools == []


def test_build_tools_returns_empty_for_unknown_agent():
    runner = PipelineRunner(client=AsyncMock())

    tools = runner._build_tools("nonexistent-agent")
    assert tools == []


# ------------------------------------------------------------------ #
# Field ownership violation is handled gracefully
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_field_ownership_violation_strips_unauthorized_fields():
    """Agent trying to write unauthorized fields gets them stripped."""
    client = AsyncMock()
    # complexity-routing tries to write evidence_analysis (not allowed)
    client.chat.completions.create = AsyncMock(
        return_value=_make_chat_response(
            {
                "status": "processing",
                "evidence_analysis": {"should": "be stripped"},
            }
        )
    )

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        result = await runner._run_agent("complexity-routing", _minimal_state())

    # status should be updated (allowed for complexity-routing)
    assert result.status == CaseStatusEnum.processing
    # evidence_analysis should remain None (not allowed for complexity-routing)
    assert result.evidence_analysis is None


# ------------------------------------------------------------------ #
# Non-JSON response from agent is handled
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_non_json_response_handled_gracefully():
    """If agent returns non-JSON, it should not crash; state is unchanged."""
    client = AsyncMock()

    message = SimpleNamespace(content="This is not JSON", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    response = SimpleNamespace(choices=[choice], usage=usage)

    client.chat.completions.create = AsyncMock(return_value=response)

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        state = _minimal_state()
        result = await runner._run_agent("case-processing", state)

    # Should still return a valid CaseState (unchanged aside from audit)
    assert isinstance(result, CaseState)
