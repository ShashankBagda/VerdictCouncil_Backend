"""Phase + research subagent factories (Sprint 1 1.A1.4).

Two factories for the new 6-phase topology:

- `make_phase_node(phase)` builds a LangGraph node for one of the three
  single-agent phases (`intake`, `synthesis`, `audit`).
- `make_research_subagent(scope)` builds one of the four research
  subagents (`evidence`, `facts`, `witnesses`, `law`) that fan out from
  `research_dispatch` (Sprint 1 1.A1.5 wires the topology).

Tool scoping is least-privilege by design (codex P2 finding 7):

- `audit` gets ZERO tools — the auditor independence guarantee.
- `intake` gets only `parse_document`.
- The two search tools are restricted to the `law` research subagent
  and (with `search_precedents` only) the `synthesis` phase.

Sprint 1 placeholder: `_resolve_prompt(name)` returns a static stub so
the factory works before 1.C3a.3 wires the LangSmith prompt registry.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from src.pipeline.graph.agents.stream_coalescer import StreamCoalescer
from src.pipeline.graph.middleware import (
    CaseAwareState,
    audit_tool_call,
    cancel_check,
    sse_tool_emitter,
    token_usage_emitter,
)
from src.services.pipeline_events import publish_agent_event

logger = logging.getLogger(__name__)
from src.pipeline.graph.prompt_registry import get_prompt
from src.pipeline.graph.prompts import AGENT_TOOLS
from src.pipeline.graph.schemas import (
    AuditOutput,
    EvidenceResearch,
    FactsResearch,
    IntakeOutput,
    LawResearch,
    SynthesisOutput,
    WitnessesResearch,
)
from src.pipeline.graph.tools import make_tools

# ---------------------------------------------------------------------------
# Tool scoping policy (codex P2-7) — explicit dicts, not `tools=ALL_TOOLS`.
# ---------------------------------------------------------------------------

PHASE_TOOL_NAMES: dict[str, list[str]] = {
    "intake": ["parse_document"],
    "synthesis": ["search_precedents"],
    "audit": [],
}

RESEARCH_TOOL_NAMES: dict[str, list[str]] = {
    "evidence": ["parse_document"],
    "facts": ["parse_document"],
    "witnesses": ["parse_document"],
    "law": ["search_legal_rules", "search_precedents"],
}

# ---------------------------------------------------------------------------
# Tool name aliases — Sprint 0.5 §5 D-7 / 0.3 finalized roster.
# `search_legal_rules` is the new canonical name; the underlying registered
# tool today is `search_domain_guidance`. The full rename happens when
# tools.py is rewritten in a later sprint; until then the factory resolves
# the new name to the existing tool.
# ---------------------------------------------------------------------------

_TOOL_ALIASES: dict[str, str] = {
    "search_legal_rules": "search_domain_guidance",
}

# ---------------------------------------------------------------------------
# Phase output schemas (Pydantic, all `extra="forbid"`; AuditOutput
# additionally `strict=True` per Sprint 0.5 §5 D-4).
# ---------------------------------------------------------------------------

PHASE_SCHEMAS: dict[str, type[Any]] = {
    "intake": IntakeOutput,
    "synthesis": SynthesisOutput,
    "audit": AuditOutput,
}

RESEARCH_SCHEMAS: dict[str, type[Any]] = {
    "evidence": EvidenceResearch,
    "facts": FactsResearch,
    "witnesses": WitnessesResearch,
    "law": LawResearch,
}

# ---------------------------------------------------------------------------
# Middleware stack — every phase agent gets the same four hooks
# (Sprint 1 1.A1.2). Order: cancel first (short-circuit before tool work),
# then sse_emitter / audit / token_usage around tool + model calls.
# ---------------------------------------------------------------------------

PHASE_MIDDLEWARE: list[Any] = [
    cancel_check,
    sse_tool_emitter,
    audit_tool_call,
    token_usage_emitter,
]


def _resolve_model(phase_or_scope: str) -> str:
    """Pick model tier per Sprint 0.5 §5 D-10 (gpt-5-mini / gpt-5)."""
    if phase_or_scope == "intake":
        return "gpt-5-mini"
    return "gpt-5"


def _chunk_text(chunk: AIMessageChunk) -> str:
    """Extract a text delta from a streaming AIMessageChunk.

    Handles three cases that all show up depending on `response_format`:

    - **Plain text** — `chunk.content` is a string. Native streaming for
      strict-JSON / unstructured responses (e.g. the audit phase).
    - **Multi-modal content parts** — `chunk.content` is a list of dicts
      where text parts have `{"type": "text", "text": ...}`. Used by
      models that surface "thinking" alongside the final answer.
    - **Tool-call args** — when `response_format=ToolStrategy(schema)`,
      the structured response lands as a tool call to the schema-binding
      tool; per-token deltas appear in `chunk.tool_call_chunks[*].args`
      as a stream of partial JSON. Surfacing those as text gives the UI
      something to render while the structured response forms.
    """
    content = chunk.content
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", "") or "")
            elif isinstance(part, str):
                parts.append(part)
        joined = "".join(parts)
        if joined:
            return joined
    tool_chunks = getattr(chunk, "tool_call_chunks", None) or []
    if tool_chunks:
        return "".join(tc.get("args", "") or "" for tc in tool_chunks)
    return ""


def _resolve_prompt(phase: str, corrections: str | None = None) -> str:
    """Resolve the system prompt for the active phase.

    Sprint 1 1.C3a.3: delegates to `prompt_registry.get_prompt(phase)`,
    which pulls from LangSmith with a local-file fallback. The static
    stub the factory used in 1.A1.4 is gone.
    """
    return get_prompt(phase, corrections=corrections)


def _build_all_tools(state: dict[str, Any]) -> dict[str, Any]:
    """Return every legacy tool keyed by name, ignoring `AGENT_TOOLS` scoping.

    `make_tools(state, agent_name)` filters by the legacy `AGENT_TOOLS`
    map, so no single legacy agent name yields the full new-topology
    tool surface (parse_document + search_precedents +
    search_domain_guidance). We aggregate by walking every legacy agent
    and merging — each tool factory closure is identical regardless of
    which legacy name we asked for.
    """
    by_name: dict[str, Any] = {}
    for legacy_agent in AGENT_TOOLS:
        raw_tools, _meta = make_tools(state, agent_name=legacy_agent)
        for tool in raw_tools:
            by_name.setdefault(tool.name, tool)
    return by_name


def _filter_tools(state: dict[str, Any], phase_or_scope: str, allowed: list[str]) -> list[Any]:
    """Build the tool subset the agent is allowed to call.

    Resolves new-topology aliases (e.g. `search_legal_rules` →
    `search_domain_guidance`) so the factory's policy dicts can use the
    canonical names from Sprint 0.5 even before the tool registration is
    renamed.
    """
    if not allowed:
        return []
    by_name = _build_all_tools(state)
    selected: list[Any] = []
    for name in allowed:
        actual = _TOOL_ALIASES.get(name, name)
        tool = by_name.get(actual)
        if tool is not None:
            selected.append(tool)
    return selected


# Re-export the shared extractor so the existing import path keeps working.
# Sprint 3 3.B.5 — research_join consumes the accumulated set to validate
# self-reported `supporting_sources`. The single canonical implementation
# lives in `src.pipeline.graph.citation_provenance`.
from src.pipeline.graph.citation_provenance import (  # noqa: E402
    source_ids_from_messages as _extract_source_ids_from_messages,
)


def _make_node(
    *,
    phase_or_scope: str,
    allowed_tool_names: list[str],
    schema: type[Any],
    use_strict_response_format: bool,
    conversational: bool = False,
) -> Callable:
    """Common factory body shared by `make_phase_node` + `make_research_subagent`.

    `conversational=True` (Q1.4) builds the agent WITHOUT
    `response_format` so the model emits prose. The factory then
    swaps the wire format: prose deltas go through the Q1.1 coalescer
    → `llm_token` SSE events; tool-call chunks emit as
    `tool_call_delta` events. Used by Q1.6 to wire intake's
    conversational mode behind the
    `PIPELINE_CONVERSATIONAL_STREAMING_PHASES` flag. The audit phase
    is NEVER conversational (architecture decision A3).
    """

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        tools = _filter_tools(state, phase_or_scope, allowed_tool_names)
        # Conversational mode: NO response_format binding — the model
        # emits prose, not bound JSON. Q1.5 will run a structuring
        # pass after the conversational stream completes to produce
        # the schema-bound artifact. JSON mode (default) keeps the
        # existing ToolStrategy / strict-response wiring.
        if conversational:
            response_format = None
        elif use_strict_response_format:
            response_format = schema
        else:
            response_format = ToolStrategy(schema)
        # Pull per-phase corrective instructions (judge rerun) if any. The
        # gate-apply node writes these into `state["extra_instructions"]`
        # keyed by phase name when the judge selects "rerun" with notes.
        extra = (state.get("extra_instructions") or {}).get(phase_or_scope)
        agent = create_agent(
            model=_resolve_model(phase_or_scope),
            tools=tools,
            system_prompt=_resolve_prompt(phase_or_scope, corrections=extra),
            response_format=response_format,
            middleware=PHASE_MIDDLEWARE,
            state_schema=CaseAwareState,
        )

        case = state["case"]
        case_id = str(case.case_id)
        agent_state: dict[str, Any] = {
            "messages": [],
            "case_id": case_id,
            "agent_name": phase_or_scope,
        }

        # Multi-mode astream:
        #   - "messages" yields (AIMessageChunk, metadata) tuples per token
        #     of the model's response — published as `llm_chunk` SSE events
        #     so the UI can render the agent's reasoning live.
        #   - "values"   yields the full graph state after each step; the
        #     last yield carries the agent's final `structured_response`
        #     and accumulated messages.
        # Tool calls are still emitted by the wrap_tool_call middleware
        # (`sse_tool_emitter`), so the per-tool wire format is unchanged.
        #
        # Q1.2 / Risk #1: once any observable side-effect has happened
        # (first message chunk OR first values payload), the broad
        # `except Exception → ainvoke` fallback is unsafe — it would
        # re-execute tools and double-charge OpenAI. `streaming_started`
        # gates the fallback: pre-chunk failures still get the safe
        # ainvoke retry; post-chunk failures emit `agent_failed` SSE
        # and propagate so the orchestrator's existing failure handling
        # takes over.
        result: dict[str, Any] = {}
        streaming_started = False

        # Conversational mode bookkeeping: a fresh message_id per
        # assistant turn (reset whenever a tool message lands so a
        # new assistant turn gets a new bubble in the UI), and a
        # coalescer to batch prose deltas into `llm_token` events.
        message_id: str = uuid.uuid4().hex if conversational else ""

        async def _emit_token(text: str) -> None:
            await publish_agent_event(
                case_id,
                {
                    "case_id": case_id,
                    "agent": phase_or_scope,
                    "phase": phase_or_scope,
                    "event": "llm_token",
                    "message_id": message_id,
                    "delta": text,
                    "ts": datetime.now(UTC).isoformat(),
                },
            )

        coalescer = StreamCoalescer(on_emit=_emit_token) if conversational else None

        try:
            async for mode, payload in agent.astream(
                agent_state,
                stream_mode=["values", "messages"],
            ):
                streaming_started = True
                if mode == "messages":
                    msg = payload[0] if isinstance(payload, tuple) else payload
                    if conversational:
                        # New assistant turn after a tool result → flush
                        # pending prose, mint a fresh message_id so the
                        # frontend renders distinct bubbles.
                        if isinstance(msg, ToolMessage | AIMessage):
                            if coalescer is not None:
                                await coalescer.flush()
                            if isinstance(msg, ToolMessage):
                                message_id = uuid.uuid4().hex
                        if isinstance(msg, AIMessageChunk):
                            # Tool-call chunks → `tool_call_delta` events.
                            for tc in getattr(msg, "tool_call_chunks", None) or []:
                                args_delta = tc.get("args") or ""
                                if args_delta or tc.get("name"):
                                    await publish_agent_event(
                                        case_id,
                                        {
                                            "case_id": case_id,
                                            "agent": phase_or_scope,
                                            "phase": phase_or_scope,
                                            "event": "tool_call_delta",
                                            "tool_call_id": tc.get("id") or "",
                                            "name": tc.get("name") or "",
                                            "args_delta": args_delta,
                                            "ts": datetime.now(UTC).isoformat(),
                                        },
                                    )
                            # Prose content (string or multi-modal text parts) → coalescer.
                            content = msg.content
                            if isinstance(content, str) and content:
                                if coalescer is not None:
                                    await coalescer.feed(content)
                            elif isinstance(content, list):
                                for part in content:
                                    if (
                                        coalescer is not None
                                        and isinstance(part, dict)
                                        and part.get("type") == "text"
                                    ):
                                        await coalescer.feed(part.get("text") or "")
                    elif isinstance(msg, AIMessageChunk):
                        text = _chunk_text(msg)
                        if text:
                            await publish_agent_event(
                                case_id,
                                {
                                    "case_id": case_id,
                                    "agent": phase_or_scope,
                                    "event": "llm_chunk",
                                    "delta": text,
                                    "ts": datetime.now(UTC).isoformat(),
                                },
                            )
                elif mode == "values":
                    result = payload
            # Drain any pending prose at end-of-stream.
            if coalescer is not None:
                await coalescer.close()
        except Exception as exc:
            if not streaming_started:
                logger.exception(
                    "astream failed before any chunk for phase=%s case=%s; "
                    "falling back to ainvoke (streaming_started=False, safe)",
                    phase_or_scope,
                    case_id,
                )
                result = await agent.ainvoke(agent_state)
            else:
                logger.exception(
                    "astream failed AFTER first chunk for phase=%s case=%s "
                    "(streaming_started=True); emitting agent_failed and re-raising "
                    "— ainvoke retry would double-execute tools",
                    phase_or_scope,
                    case_id,
                )
                # Error CLASS only — never the message (may carry PII from prompts).
                await publish_agent_event(
                    case_id,
                    {
                        "case_id": case_id,
                        "agent": phase_or_scope,
                        "event": "agent_failed",
                        "error_class": type(exc).__name__,
                        "ts": datetime.now(UTC).isoformat(),
                    },
                )
                raise

        structured = result.get("structured_response")
        update: dict[str, Any] = {f"{phase_or_scope}_output": structured}
        # Sprint 3 3.B.5 — surface citation source_ids from this agent's
        # tool-message chain so the research_join validator can verify
        # self-reported supporting_sources without re-querying the audit log.
        # Dict-keyed by phase/scope so a judge-driven /rerun of this slot
        # alone overwrites cleanly without leaking stale source_ids.
        source_ids = _extract_source_ids_from_messages(result.get("messages") or [])
        if source_ids:
            update["retrieved_source_ids"] = {phase_or_scope: source_ids}
        return update

    _node.__name__ = f"phase_node_{phase_or_scope}"
    return _node


def make_phase_node(phase: str) -> Callable:
    """Return an async LangGraph node for one of the three phase agents."""
    if phase not in PHASE_TOOL_NAMES:
        raise ValueError(f"Unknown phase: {phase!r}; expected one of {sorted(PHASE_TOOL_NAMES)}")
    schema = PHASE_SCHEMAS[phase]
    allowed = PHASE_TOOL_NAMES[phase]
    return _make_node(
        phase_or_scope=phase,
        allowed_tool_names=allowed,
        schema=schema,
        # `audit` is the only strict-mode phase; the others get ToolStrategy.
        use_strict_response_format=(phase == "audit"),
    )


def make_research_subagent(scope: str) -> Callable:
    """Return an async LangGraph node for one of the four research subagents."""
    if scope not in RESEARCH_TOOL_NAMES:
        raise ValueError(
            f"Unknown research scope: {scope!r}; expected one of {sorted(RESEARCH_TOOL_NAMES)}"
        )
    schema = RESEARCH_SCHEMAS[scope]
    allowed = RESEARCH_TOOL_NAMES[scope]
    return _make_node(
        phase_or_scope=f"research-{scope}",
        allowed_tool_names=allowed,
        schema=schema,
        use_strict_response_format=False,
    )
