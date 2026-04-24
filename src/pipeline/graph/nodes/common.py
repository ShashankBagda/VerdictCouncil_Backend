"""Shared LLM+tool execution loop for all agent nodes.

`_run_agent_node(agent_name, state)` is the single shared implementation
for every one of the 9 agent nodes. Node wrappers in the sibling modules
call this with their own name and forward the return value to the graph.

Design: manual tool-call loop (not create_react_agent) so we can emit
per-tool SSE events, capture token usage, and control MLflow span nesting
independently of LangGraph's ToolNode.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from src.api.schemas.pipeline_events import PipelineProgressEvent
from src.db.pipeline_state import persist_case_state
from src.pipeline.graph.prompts import (
    AGENT_MODEL_TIER,
    AGENT_ORDER,
    AGENT_PROMPTS,
    MODEL_TIER_MAP,
)
from src.pipeline.graph.state import GraphState
from src.pipeline.graph.tools import make_tools
from src.pipeline.observability import agent_run
from src.services.database import async_session
from src.services.pipeline_events import (
    check_cancel_flag,
    publish_agent_event,
    publish_progress,
)
from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState, CaseStatusEnum
from src.shared.config import settings
from src.shared.validation import (
    FIELD_OWNERSHIP,
    FieldOwnershipError,
    normalize_agent_output,
    validate_field_ownership,
)

logger = logging.getLogger(__name__)

_MAX_TOOL_ITERATIONS = 10


def _resolve_model(agent_name: str) -> str:
    tier = AGENT_MODEL_TIER[agent_name]
    attr = MODEL_TIER_MAP[tier]
    return getattr(settings, attr)


def _find_tool(tools: list, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise KeyError(f"Tool '{name}' not found in agent's tool set")


def _token_usage(ai_msg: AIMessage) -> dict[str, int] | None:
    meta = getattr(ai_msg, "usage_metadata", None)
    if meta is None:
        return None
    return {
        "prompt_tokens": meta.get("input_tokens", 0),
        "completion_tokens": meta.get("output_tokens", 0),
        "total_tokens": meta.get("total_tokens", 0),
    }


async def _cancelled_halt(case_id: str, agent_name: str) -> dict[str, Any]:
    """Publish the SSE cancel frame and return a halt dict for the graph."""
    await publish_progress(
        PipelineProgressEvent(
            case_id=case_id,  # type: ignore[arg-type]
            agent="pipeline",
            phase="cancelled",
            step=None,
            ts=datetime.now(UTC),
            detail={"reason": "cancelled_by_user", "stopped_at": agent_name},
        )
    )
    return {"halt": {"reason": "cancelled_by_user", "stopped_at": agent_name}}


async def _sse_thinking(case_id: str, agent_name: str, model_name: str, n_tools: int) -> None:
    await publish_agent_event(
        case_id,
        {
            "case_id": case_id,
            "agent": agent_name,
            "event": "thinking",
            "content": f"→ {model_name} · tools={n_tools}",
            "ts": datetime.now(UTC).isoformat(),
        },
    )


async def _run_agent_node(agent_name: str, state: GraphState) -> dict[str, Any]:
    """Execute one agent turn and return the state delta."""
    case: CaseState = state["case"]
    case_id = case.case_id
    run_id = state["run_id"]
    extra = state.get("extra_instructions", {}).get(agent_name)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------
    system_prompt = AGENT_PROMPTS[agent_name]
    if extra:
        system_prompt = f"{system_prompt}\n\nAdditional instructions from judge:\n{extra}"
    system_prompt = (
        f"{system_prompt}\n\n"
        "OUTPUT FORMAT: Respond with ONLY a single valid JSON object — no prose, "
        "no markdown fences, no commentary. Your entire response must parse as JSON."
    )

    # ------------------------------------------------------------------
    # Model + tools
    # ------------------------------------------------------------------
    model_name = _resolve_model(agent_name)
    tools, precedent_meta = make_tools(state, agent_name)

    llm_base = ChatOpenAI(model=model_name)
    llm = llm_base.bind_tools(tools) if tools else llm_base

    # ------------------------------------------------------------------
    # SSE: agent started
    # ------------------------------------------------------------------
    step = AGENT_ORDER.index(agent_name) + 1
    await publish_progress(
        PipelineProgressEvent(
            case_id=case_id,  # type: ignore[arg-type]
            agent=agent_name,
            phase="started",
            step=step,
            ts=datetime.now(UTC),
        )
    )

    # ------------------------------------------------------------------
    # Pre-turn cancel check
    # ------------------------------------------------------------------
    if await check_cancel_flag(case_id):
        return await _cancelled_halt(case_id, agent_name)

    # ------------------------------------------------------------------
    # MLflow span + LLM loop
    # ------------------------------------------------------------------
    mlflow_ids: tuple[str, str] | None = None
    token_usage: dict[str, int] | None = None
    tool_calls_log: list[dict[str, Any]] = []

    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Current case state JSON:\n{case.model_dump_json()}"),
    ]

    with agent_run(agent_name=agent_name, case_id=case_id, run_id=run_id) as mlflow_ctx:
        if mlflow_ctx:
            mlflow_ids = mlflow_ctx

        await _sse_thinking(case_id, agent_name, model_name, len(tools))
        ai_msg: AIMessage = await llm.ainvoke(messages)
        messages.append(ai_msg)
        token_usage = _token_usage(ai_msg)

        # Tool-call loop — manual so we control SSE granularity
        _tool_iteration = 0
        while ai_msg.tool_calls:
            if _tool_iteration >= _MAX_TOOL_ITERATIONS:
                logger.warning(
                    "Agent '%s' reached MAX_TOOL_ITERATIONS=%d; exiting loop",
                    agent_name,
                    _MAX_TOOL_ITERATIONS,
                )
                break
            _tool_iteration += 1

            tool_result_msgs: list[ToolMessage] = []

            for tc in ai_msg.tool_calls:
                fn_name: str = tc["name"]
                fn_args: dict[str, Any] = tc["args"]
                tc_id: str = tc["id"]

                await publish_agent_event(
                    case_id,
                    {
                        "case_id": case_id,
                        "agent": agent_name,
                        "event": "tool_call",
                        "tool_name": fn_name,
                        "args": fn_args,
                        "ts": datetime.now(UTC).isoformat(),
                    },
                )

                try:
                    tool = _find_tool(tools, fn_name)
                    result_raw = await tool.ainvoke(fn_args)
                except Exception as exc:
                    logger.warning("Tool '%s' raised in agent '%s': %s", fn_name, agent_name, exc)
                    result_raw = {"error": str(exc)}

                result_str = json.dumps(result_raw, default=str)

                await publish_agent_event(
                    case_id,
                    {
                        "case_id": case_id,
                        "agent": agent_name,
                        "event": "tool_result",
                        "tool_name": fn_name,
                        "result": result_str[:2000],
                        "ts": datetime.now(UTC).isoformat(),
                    },
                )

                tool_calls_log.append({"tool": fn_name, "arguments": fn_args, "result": result_raw})
                tool_result_msgs.append(ToolMessage(content=result_str, tool_call_id=tc_id))

            messages.extend(tool_result_msgs)

            await publish_agent_event(
                case_id,
                {
                    "case_id": case_id,
                    "agent": agent_name,
                    "event": "thinking",
                    "content": (
                        f"→ {model_name} · continuing after {len(tool_calls_log)} tool call(s)"
                    ),
                    "ts": datetime.now(UTC).isoformat(),
                },
            )

            if await check_cancel_flag(case_id):
                return await _cancelled_halt(case_id, agent_name)

            ai_msg = await llm.ainvoke(messages)
            messages.append(ai_msg)
            if _token_usage(ai_msg):
                token_usage = _token_usage(ai_msg)

    # ------------------------------------------------------------------
    # Parse final JSON
    # ------------------------------------------------------------------
    raw_content = ai_msg.content if isinstance(ai_msg.content, str) else ""
    if not raw_content:
        raw_content = "{}"

    await publish_agent_event(
        case_id,
        {
            "case_id": case_id,
            "agent": agent_name,
            "event": "llm_response",
            "content": raw_content[:2000],
            "ts": datetime.now(UTC).isoformat(),
        },
    )

    try:
        agent_output = json.loads(raw_content)
    except json.JSONDecodeError:
        logger.error("Agent '%s' returned non-JSON: %s", agent_name, raw_content[:500])
        agent_output = {}

    agent_output = normalize_agent_output(agent_name, agent_output)

    # ------------------------------------------------------------------
    # Field ownership validation + merge
    # ------------------------------------------------------------------
    original_dict = case.model_dump()
    merged_dict = {**original_dict, **agent_output}

    try:
        validate_field_ownership(agent_name, original_dict, merged_dict)
    except FieldOwnershipError as exc:
        logger.warning(
            "Field ownership violation by '%s': %s. Stripping unauthorized fields.",
            agent_name,
            exc,
        )
        allowed = FIELD_OWNERSHIP.get(agent_name, set())
        merged_dict = {**original_dict}
        for key in allowed:
            if key in agent_output:
                merged_dict[key] = agent_output[key]

    # Coerce invalid status values
    if "status" in merged_dict:
        try:
            CaseStatusEnum(merged_dict["status"])
        except ValueError:
            logger.warning(
                "Agent '%s' output invalid status '%s'; coercing to 'failed'.",
                agent_name,
                merged_dict["status"],
            )
            merged_dict["status"] = CaseStatusEnum.failed

    # Fold precedent metadata side-channel
    if agent_name == "legal-knowledge" and precedent_meta.metadata is not None:
        merged_dict["precedent_source_metadata"] = precedent_meta.metadata

    updated_case = CaseState(**merged_dict)

    # ------------------------------------------------------------------
    # Audit entry
    # ------------------------------------------------------------------
    updated_case = append_audit_entry(
        updated_case,
        agent=agent_name,
        action="agent_response",
        input_payload={"state_keys": list(original_dict.keys())},
        output_payload=agent_output,
        system_prompt=(system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt),
        llm_response={"content": raw_content[:1000]},
        tool_calls=tool_calls_log or None,
        model=model_name,
        token_usage=token_usage,
    )

    # ------------------------------------------------------------------
    # Checkpoint persistence
    # ------------------------------------------------------------------
    try:
        async with async_session() as db:
            await persist_case_state(
                db,
                case_id=case_id,
                run_id=run_id,
                agent_name=agent_name,
                state=updated_case,
            )
    except Exception:
        logger.exception(
            "persist_case_state failed for agent '%s' case '%s' — continuing",
            agent_name,
            case_id,
        )

    # ------------------------------------------------------------------
    # SSE: agent completed
    # ------------------------------------------------------------------
    mlflow_run_id: str | None = mlflow_ids[0] if mlflow_ids else None
    mlflow_experiment_id: str | None = mlflow_ids[1] if mlflow_ids else None

    await publish_agent_event(
        case_id,
        {
            "case_id": case_id,
            "agent": agent_name,
            "event": "agent_completed",
            "ts": datetime.now(UTC).isoformat(),
        },
    )

    await publish_progress(
        PipelineProgressEvent(
            case_id=case_id,  # type: ignore[arg-type]
            agent=agent_name,
            phase="completed",
            step=step,
            ts=datetime.now(UTC),
            mlflow_run_id=mlflow_run_id,
            mlflow_experiment_id=mlflow_experiment_id,
        )
    )

    # ------------------------------------------------------------------
    # State delta returned to the graph
    # ------------------------------------------------------------------
    result: dict[str, Any] = {"case": updated_case}
    if mlflow_ids:
        result["mlflow_run_ids"] = {**state.get("mlflow_run_ids", {}), agent_name: mlflow_ids}

    return result
