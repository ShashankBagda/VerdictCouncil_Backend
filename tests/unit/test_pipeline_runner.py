"""Unit tests for src.pipeline.runner.PipelineRunner."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.pipeline.runner import AGENT_ORDER, PipelineRunner
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
# Full pipeline runs all 9 agents sequentially
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_full_pipeline_runs_all_agents():
    """Pipeline calls each agent in AGENT_ORDER and returns final state."""
    client = AsyncMock()
    # Each agent returns an empty JSON (no fields modified)
    client.chat.completions.create = AsyncMock(return_value=_make_chat_response({}))

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        result = await runner.run(_minimal_state())

    assert isinstance(result, CaseState)
    # Should have been called once per agent
    assert client.chat.completions.create.call_count == len(AGENT_ORDER)


# ------------------------------------------------------------------ #
# Pipeline halts when complexity-routing sets escalated status
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_pipeline_halts_on_escalation():
    """Pipeline stops after complexity-routing if status is escalated."""
    client = AsyncMock()

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        # Agent 1 (case-processing) returns normal
        if call_count == 1:
            return _make_chat_response({})
        # Agent 2 (complexity-routing) sets escalated status
        if call_count == 2:
            return _make_chat_response({"status": "escalated"})
        # Should not be reached
        return _make_chat_response({})

    client.chat.completions.create = AsyncMock(side_effect=mock_create)

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        result = await runner.run(_minimal_state())

    assert result.status == CaseStatusEnum.escalated
    # Only 2 agents should have been called
    assert call_count == 2


# ------------------------------------------------------------------ #
# Pipeline halts on critical fairness issues
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_pipeline_halts_on_fairness_issues():
    """Pipeline stops at governance-verdict if critical_issues_found is True."""
    client = AsyncMock()

    call_count = 0

    async def mock_create(**kwargs):
        nonlocal call_count
        call_count += 1
        # Last agent (governance-verdict) returns fairness issue
        if call_count == len(AGENT_ORDER):
            return _make_chat_response(
                {
                    "fairness_check": {"critical_issues_found": True, "issues": ["bias detected"]},
                }
            )
        return _make_chat_response({})

    client.chat.completions.create = AsyncMock(side_effect=mock_create)

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        result = await runner.run(_minimal_state())

    assert result.status == CaseStatusEnum.escalated
    assert result.fairness_check["critical_issues_found"] is True


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
async def test_non_json_response_raises_after_retry():
    """If an agent returns non-JSON twice, the runner halts the pipeline.

    Silently returning unchanged state (the prior behavior) was unsafe
    because downstream agents would consume an empty CaseState as if the
    upstream agent had nothing to say. The current contract is to raise
    `RuntimeError` after the retry also fails, so callers can decide
    whether to escalate the case or surface the failure to the judge.
    """
    client = AsyncMock()

    message = SimpleNamespace(content="This is not JSON", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    response = SimpleNamespace(choices=[choice], usage=usage)

    # Both the initial call and the retry return non-JSON
    client.chat.completions.create = AsyncMock(return_value=response)

    runner = PipelineRunner(client=client)

    with patch.object(runner, "_load_agent_config", return_value=_agent_config()):
        state = _minimal_state()
        with pytest.raises(RuntimeError, match="produced invalid output after retry"):
            await runner._run_agent("case-processing", state)
