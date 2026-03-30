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
    "deliberation",
    "governance-verdict",
]

# Maps each agent to the tool function names it can invoke
AGENT_TOOLS: dict[str, list[str]] = {
    "case-processing": ["parse_document"],
    "complexity-routing": [],
    "evidence-analysis": ["parse_document", "cross_reference"],
    "fact-reconstruction": ["timeline_construct"],
    "witness-analysis": ["generate_questions"],
    "legal-knowledge": ["search_precedents"],
    "argument-construction": ["confidence_calc"],
    "deliberation": [],
    "governance-verdict": ["confidence_calc"],
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
                            "Types of questions: 'clarification' | 'challenge' | "
                            "'exploration' | 'credibility'"
                        ),
                        "default": ["clarification", "challenge"],
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
    "confidence_calc": {
        "type": "function",
        "function": {
            "name": "confidence_calc",
            "description": (
                "Calculate weighted confidence score for verdict recommendation "
                "based on evidence strength, rule relevance, precedent similarity, "
                "and witness credibility."
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
    "governance-verdict": {
        "fairness_check": ["critical_issues_found", "audit_passed"],
        "verdict_recommendation": ["confidence_score"],
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
                model_name = getattr(settings, "openai_model_lightweight")
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
        try:
            if tool_name == "parse_document":
                from src.tools import parse_document

                result = await parse_document(**arguments)
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
                from src.tools import search_precedents

                result = await search_precedents(**arguments)
            elif tool_name == "confidence_calc":
                from src.tools import confidence_calc

                result = confidence_calc(**arguments)
            else:
                result = {"error": f"Unknown tool: {tool_name}"}
        except Exception as exc:
            logger.exception("Tool call failed: %s", tool_name)
            result = {"error": str(exc)}

        return json.dumps(result, default=str)

    async def _run_agent(self, agent_name: str, state: CaseState) -> CaseState:
        """Run a single agent step: call LLM, parse response, update state."""
        config = self._load_agent_config(agent_name)
        model = self._resolve_model(config)
        system_prompt = config.get("instruction", "")
        tools = self._build_tools(agent_name)

        state_json = state.model_dump_json()

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state_json},
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

    async def run(self, case_state: CaseState) -> CaseState:
        """Run the full 9-agent pipeline sequentially.

        Accepts a CaseState with raw_documents populated and returns
        the final CaseState with all agent outputs merged in.

        Halt conditions:
        - After Agent 2 (complexity-routing): if status == "escalated"
        - After Agent 9 (governance-verdict) phase 1: if fairness_check
          has critical_issues_found == True
        """
        state = case_state

        for agent_name in AGENT_ORDER:
            logger.info("Pipeline step: %s", agent_name)
            state = await self._run_agent(agent_name, state)

            # Halt after Agent 2 if case is escalated
            if agent_name == "complexity-routing" and state.status == CaseStatusEnum.escalated:
                logger.warning(
                    "Pipeline halted at complexity-routing: "
                    "case escalated to human review (case_id=%s)",
                    state.case_id,
                )
                return state

            # Halt after Agent 9 if fairness check found critical issues
            if (
                agent_name == "governance-verdict"
                and state.fairness_check
                and state.fairness_check.get("critical_issues_found")
            ):
                logger.warning(
                    "Pipeline halted at governance-verdict: "
                    "critical fairness issues detected (case_id=%s)",
                    state.case_id,
                )
                state = state.model_copy(update={"status": CaseStatusEnum.escalated})
                return state

        logger.info(
            "Pipeline completed for case_id=%s, status=%s",
            state.case_id,
            state.status,
        )
        return state
