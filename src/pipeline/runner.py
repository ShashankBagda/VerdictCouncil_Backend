"""Single-process pipeline runner for VerdictCouncil.

Chains 9 agent calls sequentially via OpenAI API. No Solace, no Redis.
Used to validate pipeline logic against gold-set eval cases before
adding distributed infrastructure.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from openai import AsyncOpenAI

from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState, CaseStatusEnum
from src.shared.config import settings
from src.shared.validation import FieldOwnershipError, validate_field_ownership
from src.tools.exceptions import CriticalToolFailure, DegradableToolError
from src.tools.search_precedents import PrecedentSearchError  # noqa: F401 — register as degradable

logger = logging.getLogger(__name__)

# Pipeline order: all 9 agents in sequence
AGENT_ORDER: list[str] = [
    "case-processing",
    "complexity-routing",
    "evidence-analysis",
    "fact-reconstruction",
    "witness-analysis",
    "legal-knowledge",
    "argument-construction",
    "hearing-analysis",
    "hearing-governance",
]

# Gate groupings — agents are paused for judge review after each gate completes.
GATE_AGENTS: dict[str, list[str]] = {
    "gate1": ["case-processing", "complexity-routing"],
    "gate2": ["evidence-analysis", "fact-reconstruction", "witness-analysis", "legal-knowledge"],
    "gate3": ["argument-construction", "hearing-analysis"],
    "gate4": ["hearing-governance"],
}

# Maps each agent to the tool function names it can invoke
AGENT_TOOLS: dict[str, list[str]] = {
    "case-processing": ["parse_document"],
    "complexity-routing": [],
    "evidence-analysis": ["parse_document", "cross_reference"],
    "fact-reconstruction": ["timeline_construct"],
    "witness-analysis": ["generate_questions"],
    "legal-knowledge": ["search_precedents", "search_domain_guidance"],
    "argument-construction": ["confidence_calc"],
    "hearing-analysis": [],
    "hearing-governance": [],
}

# Maps model tier names to settings attribute names
MODEL_TIER_MAP: dict[str, str] = {
    "lightweight": "openai_model_lightweight",
    "efficient": "openai_model_efficient_reasoning",
    "strong": "openai_model_strong_reasoning",
    "frontier": "openai_model_frontier_reasoning",
}

# OpenAI tool schemas for each tool name
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "parse_document": {
        "type": "function",
        "function": {
            "name": "parse_document",
            "description": (
                "Parse uploaded documents via OpenAI Files API. "
                "Extracts text, tables, and metadata from legal filings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "OpenAI File ID of the uploaded document",
                    },
                    "extract_tables": {
                        "type": "boolean",
                        "description": "Whether to extract tabular data",
                        "default": True,
                    },
                    "ocr_enabled": {
                        "type": "boolean",
                        "description": "Whether to enable OCR for scanned documents",
                        "default": False,
                    },
                },
                "required": ["file_id"],
            },
        },
    },
    "cross_reference": {
        "type": "function",
        "function": {
            "name": "cross_reference",
            "description": (
                "Compare document segments to identify contradictions, "
                "corroborations, and inconsistencies across evidence items."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "segments": {
                        "type": "array",
                        "description": (
                            "List of document segments to compare. "
                            "Each segment: {doc_id, text, page, paragraph}"
                        ),
                    },
                    "check_type": {
                        "type": "string",
                        "description": (
                            "Type of cross-reference check: "
                            "'contradiction' | 'corroboration' | 'all'"
                        ),
                    },
                },
                "required": ["segments", "check_type"],
            },
        },
    },
    "timeline_construct": {
        "type": "function",
        "function": {
            "name": "timeline_construct",
            "description": (
                "Build a chronological timeline from extracted events. "
                "Handles date normalization, ordering, and conflict detection."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "events": {
                        "type": "array",
                        "description": (
                            "List of events to order. Each event: "
                            "{date, description, source_ref, parties, location}"
                        ),
                    },
                },
                "required": ["events"],
            },
        },
    },
    "generate_questions": {
        "type": "function",
        "function": {
            "name": "generate_questions",
            "description": (
                "Generate suggested judicial questions based on argument "
                "analysis and identified weaknesses."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "argument_summary": {
                        "type": "string",
                        "description": "Summary of the argument or testimony",
                    },
                    "weaknesses": {
                        "type": "array",
                        "description": "List of identified weaknesses or gaps to probe",
                    },
                    "question_types": {
                        "type": "array",
                        "description": (
                            "Types of questions: 'factual_clarification' | 'evidence_gap' | "
                            "'credibility_probe' | 'legal_interpretation'"
                        ),
                        "default": ["factual_clarification", "evidence_gap"],
                    },
                    "max_questions": {
                        "type": "integer",
                        "description": "Maximum number of questions to generate",
                        "default": 5,
                    },
                },
                "required": ["argument_summary", "weaknesses"],
            },
        },
    },
    "search_precedents": {
        "type": "function",
        "function": {
            "name": "search_precedents",
            "description": (
                "Query the PAIR Search API for binding higher court case law "
                "matching fact patterns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Targeted query for legal concepts or statutory provisions"
                        ),
                    },
                    "domain": {
                        "type": "string",
                        "description": "Legal domain: 'small_claims' | 'traffic'",
                    },
                    "vector_store_id": {
                        "type": "string",
                        "description": "Domain vector store ID (injected by runner; do not set)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of precedents to return",
                        "default": 10,
                    },
                },
                "required": ["query", "domain"],
            },
        },
    },
    "search_domain_guidance": {
        "type": "function",
        "function": {
            "name": "search_domain_guidance",
            "description": (
                "Query the domain's curated knowledge base for statutes, practice directions, "
                "bench books, and procedural rules. Domain-scoped — always hits the correct corpus."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Semantic query for guidance content",
                    },
                    "vector_store_id": {
                        "type": "string",
                        "description": "Domain vector store ID (injected by runner; do not set)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 5,
                    },
                },
                "required": ["query", "vector_store_id"],
            },
        },
    },
    "confidence_calc": {
        "type": "function",
        "function": {
            "name": "confidence_calc",
            "description": (
                "Calculate a weighted confidence score for case analysis components "
                "based on evidence strength, precedent alignment, and argument completeness."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "evidence_strengths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of evidence strength labels: strong, medium, weak",
                    },
                    "fact_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of fact statuses: agreed, disputed, verified",
                    },
                    "witness_scores": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "List of witness credibility scores (0-100)",
                    },
                    "precedent_similarities": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "List of precedent similarity scores (0.0-1.0)",
                    },
                },
                "required": [
                    "evidence_strengths",
                    "fact_statuses",
                    "witness_scores",
                    "precedent_similarities",
                ],
            },
        },
    },
}

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "agents"

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: str) -> str:
    """Replace ``${VAR}`` placeholders with environment variable values.

    Returns the original placeholder unchanged if the env var is not set.
    """

    def _replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return _ENV_VAR_PATTERN.sub(_replacer, value)


def _load_yaml_with_includes(config_path: Path) -> dict:
    """Load a SAM-style YAML config that may start with ``!include``.

    SAM natively supports ``!include`` in its evaluation config loader
    (``solace_agent_mesh.evaluation.summary_builder``).  This function
    mirrors that behaviour for the pipeline runner so configs work
    identically in both local and SAM-hosted execution.

    Because ``!include <path>`` followed by additional YAML keys is not
    valid in a single YAML document, this function:

    1. Reads the raw text.
    2. If the first non-comment, non-blank line is ``!include <path>``,
       loads the referenced file as shared anchors, then loads the
       remainder of the file using those anchors.
    3. Otherwise falls back to plain ``yaml.safe_load``.
    """
    text = config_path.read_text()
    lines = text.split("\n")

    # Find the first non-comment, non-blank line
    include_path = None
    include_line_idx = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("!include"):
            include_path = stripped.split(None, 1)[1].strip()
            include_line_idx = idx
        break

    if include_path is None:
        # No include directive — plain YAML
        return yaml.safe_load(text)

    # Load the shared config to populate anchors
    shared_path = (config_path.parent / include_path).resolve()
    shared_text = shared_path.read_text()

    # Combine: shared config providing anchors, then the rest of the
    # host file.  Anchors defined in the shared config are available.
    remaining_lines = lines[include_line_idx + 1 :]
    remaining_text = "\n".join(remaining_lines)

    combined = shared_text + "\n" + remaining_text
    return yaml.safe_load(combined)


# Required keys for critical agent output fields
_REQUIRED_KEYS: dict[str, dict[str, list[str]]] = {
    "hearing-governance": {
        "fairness_check": ["critical_issues_found", "audit_passed"],
    },
}


def _validate_agent_output_structure(agent_name: str, output: dict[str, Any]) -> None:
    """Validate that critical agent output fields have expected keys.

    Logs warnings for missing keys but does not block the pipeline,
    since LLM output is inherently variable.
    """
    checks = _REQUIRED_KEYS.get(agent_name, {})
    for field, keys in checks.items():
        value = output.get(field)
        if value is None or not isinstance(value, dict):
            continue
        missing = [k for k in keys if k not in value]
        if missing:
            logger.warning(
                "Agent '%s' output field '%s' missing keys: %s",
                agent_name,
                field,
                missing,
            )


class PipelineRunner:
    """Runs the 9-agent VerdictCouncil pipeline in a single process."""

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._client = client or AsyncOpenAI(api_key=settings.openai_api_key)
        self._config_cache: dict[str, dict[str, Any]] = {}
        self._pending_precedent_meta: dict[str, Any] | None = None
        self._document_pages_buffer: dict[str, list[str]] = {}  # openai_file_id → page texts
        self._state_pages_cache: dict[str, list] = {}  # openai_file_id → pages from DB (D13)

    def _get_cached_pages(self, file_id: str) -> list | None:
        """Return pages from DB-hydrated state if available. None means cache miss."""
        return self._state_pages_cache.get(file_id)

    @staticmethod
    def _parse_sam_yaml(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a YAML config dict into the internal format.

        Handles two layouts:
        - **New SAM format**: has an ``apps`` key with the agent config nested
          under ``apps[0]["app_config"]``.  The model is a dict like
          ``{"model": "gpt-5.4-nano", "api_key": "..."}``.
        - **Legacy format**: has ``instruction`` and ``model_tier`` at the
          top level.  Returned as-is for backward compatibility.

        Returns a flat dict with at least ``instruction`` and either
        ``model_tier`` (legacy) or ``model_name`` (SAM) so downstream
        callers can resolve the model string.
        """
        if "apps" not in raw:
            # Legacy format — pass through unchanged
            return raw

        app_config = raw["apps"][0]["app_config"]
        instruction = app_config.get("instruction", "")

        # Extract model name from the SAM model dict and resolve env vars
        model_value = app_config.get("model", {})
        if isinstance(model_value, dict):
            model_name = model_value.get("model", "")
        else:
            model_name = str(model_value)

        # Resolve ${ENV_VAR} placeholders from the environment
        model_name = _resolve_env_vars(model_name)

        return {
            "instruction": instruction,
            "model_name": model_name,
            "display_name": app_config.get("display_name", ""),
            "agent_name": app_config.get("agent_name", ""),
            "_raw_app_config": app_config,
        }

    def _load_agent_config(self, agent_name: str) -> dict[str, Any]:
        """Load and cache an agent's YAML configuration."""
        if agent_name in self._config_cache:
            return self._config_cache[agent_name]

        config_path = CONFIGS_DIR / f"{agent_name}.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Agent config not found: {config_path}")

        config = _load_yaml_with_includes(config_path)
        config = self._parse_sam_yaml(config)
        self._config_cache[agent_name] = config
        return config

    def _resolve_model(self, config: dict[str, Any]) -> str:
        """Resolve model name from config.

        Supports two formats:
        - SAM format: ``model_name`` key contains the resolved model string.
        - Legacy format: ``model_tier`` key maps to a settings attribute.
        """
        # SAM format: model_name already resolved
        model_name = config.get("model_name")
        if model_name:
            if "${" in model_name:
                # Env var not set — fall back to settings default
                model_name = settings.openai_model_lightweight
                logger.warning(
                    "Model env var not resolved, falling back to settings default: %s",
                    model_name,
                )
            return model_name

        # Legacy format: tier-based lookup
        tier = config.get("model_tier", "lightweight")
        attr_name = MODEL_TIER_MAP.get(tier)
        if not attr_name:
            raise ValueError(f"Unknown model tier: {tier}")
        return getattr(settings, attr_name)

    def _build_tools(self, agent_name: str) -> list[dict[str, Any]]:
        """Build OpenAI tool definitions for a given agent."""
        tool_names = AGENT_TOOLS.get(agent_name, [])
        tools = []
        for name in tool_names:
            schema = TOOL_SCHEMAS.get(name)
            if schema:
                tools.append(schema)
        return tools

    async def _execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool call and return the result as a JSON string.

        In the prototype, tools are imported from src.tools and called
        directly. For distributed mode, these would be Solace RPC calls.
        """
        from src.pipeline.observability import tool_span  # lazy: avoids linter removal

        try:
            if tool_name == "parse_document":
                from src.tools import parse_document

                file_id = arguments.get("file_id", "")
                # D13: return cached pages from state to skip redundant OpenAI call
                cached_pages = self._get_cached_pages(file_id)
                if cached_pages is not None:
                    result = {"file_id": file_id, "pages": cached_pages}
                else:
                    with tool_span("tool.parse_document", inputs={"args": list(arguments.keys())}):
                        result = await parse_document(**arguments)
                    # Capture page texts for later DB write (US-008 citation drill-down)
                    if file_id and isinstance(result, dict) and result.get("pages"):
                        self._document_pages_buffer[file_id] = [
                            p.get("text", "") for p in result["pages"]
                        ]
            elif tool_name == "cross_reference":
                from src.tools import cross_reference

                result = await cross_reference(**arguments)
            elif tool_name == "timeline_construct":
                from src.tools import timeline_construct

                result = timeline_construct(**arguments)
            elif tool_name == "generate_questions":
                from src.tools import generate_questions

                result = await generate_questions(**arguments)
            elif tool_name == "search_precedents":
                from src.tools.search_precedents import search_precedents_with_meta

                with tool_span("tool.search_precedents", inputs={"args": list(arguments.keys())}):
                    search_result = await search_precedents_with_meta(**arguments)
                result = search_result.precedents
                # Merge metadata across multiple calls: source_failed if ANY call failed
                if self._pending_precedent_meta is None:
                    self._pending_precedent_meta = search_result.metadata
                elif search_result.metadata.get("source_failed"):
                    self._pending_precedent_meta["source_failed"] = True
                    self._pending_precedent_meta["pair_status"] = search_result.metadata.get(
                        "pair_status", self._pending_precedent_meta.get("pair_status")
                    )
            elif tool_name == "search_domain_guidance":
                from src.tools.search_domain_guidance import search_domain_guidance

                with tool_span(
                    "tool.search_domain_guidance", inputs={"args": list(arguments.keys())}
                ):
                    result = await search_domain_guidance(**arguments)
            elif tool_name == "confidence_calc":
                from src.tools import confidence_calc

                result = confidence_calc(**arguments)
            else:
                result = {"error": f"Unknown tool: {tool_name}"}
        except CriticalToolFailure:
            raise  # Bubble to _run_agent; gate marks case failed_retryable
        except DegradableToolError as exc:
            logger.warning("Degradable tool failure in %s: %s", tool_name, exc)
            result = {"error": str(exc)}
        except Exception:
            logger.exception("Unhandled tool error in %s — failing gate", tool_name)
            raise  # Anything unexpected fails the gate, not the model's context

        return json.dumps(result, default=str)

    async def _run_agent(
        self,
        agent_name: str,
        state: CaseState,
        extra_instructions: str | None = None,
    ) -> CaseState:
        """Run a single agent step: call LLM, parse response, update state."""
        # D13: Refresh pages cache from current state's raw_documents
        self._state_pages_cache = {
            d["openai_file_id"]: d["pages"]
            for d in state.raw_documents
            if d.get("openai_file_id") and d.get("pages")
        }
        config = self._load_agent_config(agent_name)
        model = self._resolve_model(config)
        system_prompt = config.get("instruction", "")
        if extra_instructions:
            system_prompt = (
                f"{system_prompt}\n\nAdditional instructions from judge:\n{extra_instructions}"
            )
        tools = self._build_tools(agent_name)

        state_json = state.model_dump_json()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Current case state JSON:\n{state_json}"},
        ]

        logger.info("Running agent '%s' with model '%s'", agent_name, model)

        # LLM call with optional tool use loop
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        token_usage = None
        if response.usage:
            token_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        # Handle tool calls in a loop until the model returns a final response
        tool_calls_log: list[dict[str, Any]] = []
        while choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            messages.append(choice.message.model_dump())

            for tool_call in choice.message.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)
                # Inject domain vector store id for retrieval tools (§5e)
                if fn_name in ("search_precedents", "search_domain_guidance"):
                    if state.domain_vector_store_id and "vector_store_id" not in fn_args:
                        fn_args = {**fn_args, "vector_store_id": state.domain_vector_store_id}
                tool_result = await self._execute_tool_call(fn_name, fn_args)

                tool_calls_log.append(
                    {
                        "tool": fn_name,
                        "arguments": fn_args,
                        "result": json.loads(tool_result),
                    }
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result,
                    }
                )

            response = await self._client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            if response.usage:
                token_usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

        # Parse the final JSON response
        raw_content = choice.message.content or "{}"
        try:
            agent_output = json.loads(raw_content)
        except json.JSONDecodeError:
            logger.error(
                "Agent '%s' returned non-JSON response: %s",
                agent_name,
                raw_content[:500],
            )
            agent_output = {}

        # Validate critical output fields have expected structure
        _validate_agent_output_structure(agent_name, agent_output)

        # Merge agent output into CaseState (respecting field ownership)
        original_dict = state.model_dump()
        merged_dict = {**original_dict, **agent_output}

        try:
            validate_field_ownership(agent_name, original_dict, merged_dict)
        except FieldOwnershipError as exc:
            logger.warning(
                "Field ownership violation by '%s': %s. Stripping unauthorized fields.",
                agent_name,
                exc,
            )
            # Strip unauthorized fields, keep only allowed ones
            from src.shared.validation import FIELD_OWNERSHIP

            allowed = FIELD_OWNERSHIP.get(agent_name, set())
            merged_dict = {**original_dict}
            for key in allowed:
                if key in agent_output:
                    merged_dict[key] = agent_output[key]

        # Coerce any status value the LLM produced that isn't a valid enum member.
        # Agents occasionally output strings like 'REJECTED' that are not in
        # CaseStatusEnum; letting them through crashes CaseState construction.
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

        # Inject precedent source metadata from tool execution (overrides any LLM output)
        if agent_name == "legal-knowledge" and self._pending_precedent_meta is not None:
            merged_dict["precedent_source_metadata"] = self._pending_precedent_meta
        self._pending_precedent_meta = None

        updated_state = CaseState(**merged_dict)

        # Append audit entry
        updated_state = append_audit_entry(
            updated_state,
            agent=agent_name,
            action="agent_response",
            input_payload={"state_keys": list(original_dict.keys())},
            output_payload=agent_output,
            system_prompt=(
                system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt
            ),
            llm_response={"content": raw_content[:1000]},
            tool_calls=tool_calls_log if tool_calls_log else None,
            model=model,
            token_usage=token_usage,
        )

        return updated_state

    async def run_gate(
        self,
        case_state: CaseState,
        gate_name: str,
        start_agent: str | None = None,
        extra_instructions: str | None = None,
    ) -> CaseState:
        """Run one gate's agents and pause for judge review.

        When start_agent is provided, the gate resumes from that agent (used
        for per-agent reruns). extra_instructions are appended to that agent's
        system prompt only. After all agents complete, status is set to
        awaiting_review_<gate_name> regardless of LLM output.
        """
        agents = GATE_AGENTS[gate_name]
        if start_agent is not None:
            try:
                agents = agents[agents.index(start_agent) :]
            except ValueError:
                logger.warning(
                    "start_agent %r not in gate %r; running full gate", start_agent, gate_name
                )

        state = case_state
        for agent_name in agents:
            logger.info("Gate %s: running agent '%s'", gate_name, agent_name)
            agent_extra = extra_instructions if agent_name == start_agent else None
            state = await self._run_agent(agent_name, state, extra_instructions=agent_extra)
            # Guardrail: LLM output for complexity-routing can set status=escalated.
            # With the single-judge model there is no escalation target, so force
            # status back to processing after every agent step.
            if state.status == CaseStatusEnum.escalated:
                state = state.model_copy(update={"status": CaseStatusEnum.processing})

        gate_pause_status = CaseStatusEnum[f"awaiting_review_{gate_name}"]
        state = state.model_copy(update={"status": gate_pause_status})
        logger.info(
            "Gate %s completed for case_id=%s, pausing for judge review", gate_name, state.case_id
        )
        return state

    async def run(self, case_state: CaseState) -> CaseState:
        """Run gate 1 of the gated pipeline for a new case submission.

        Only gate 1 (case-processing + complexity-routing) is executed.
        The judge reviews gate 1 output and uses the gate advance endpoint
        to trigger subsequent gates.
        """
        from src.pipeline.observability import pipeline_run  # lazy: avoids linter removal

        state = case_state

        with pipeline_run(
            case_id=str(state.case_id or "unknown"),
            run_id=state.run_id or "unknown",
            mode="in_process",
        ):
            state = await self.run_gate(state, "gate1")

        return state
