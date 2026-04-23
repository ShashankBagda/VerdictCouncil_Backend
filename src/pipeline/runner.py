"""Single-process pipeline runner for VerdictCouncil.

Chains 9 agent calls sequentially via OpenAI API. No Solace, no Redis.
Used to validate pipeline logic against gold-set eval cases before
adding distributed infrastructure.

Exports two runner classes:
  - PipelineRunner: low-level gate-by-gate runner (used by API routes)
  - OrchestratorRunner: high-level runner driven by the Pipeline Orchestrator Agent,
    which manages the full lifecycle including retries, parallel dispatch, and escalation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from openai import AsyncOpenAI

from src.services.pipeline_events import publish_agent_event
from src.shared.audit import append_audit_entry
from src.shared.case_state import CaseState, CaseStatusEnum
from src.shared.config import settings
from src.shared.validation import FieldOwnershipError, normalize_agent_output, validate_field_ownership
from src.tools.exceptions import CriticalToolFailure, DegradableToolError
from src.tools.search_precedents import PrecedentSearchError  # noqa: F401 — register as degradable

logger = logging.getLogger(__name__)

# Pipeline orchestrator — meta-agent that manages the full pipeline lifecycle.
# It is NOT part of the sequential agent chain; it sits above it.
ORCHESTRATOR_AGENT: str = "pipeline-orchestrator"

# Pipeline order: all 9 agents in sequence (managed by PipelineRunner / OrchestratorRunner)
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

# Gate2 agents can be dispatched in parallel (order within barrier is irrelevant).
GATE2_PARALLEL_AGENTS: list[str] = [
    "evidence-analysis",
    "fact-reconstruction",
    "witness-analysis",
    "legal-knowledge",
]

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
    # Orchestrator management tools
    "pipeline-orchestrator": [
        "pipeline_status",
        "delegate_to_agent",
        "retry_failed_agent",
        "escalate_case",
        "parallel_dispatch",
        "advance_gate",
    ],
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
                        "items": {
                            "type": "object",
                            "properties": {
                                "doc_id": {"type": "string"},
                                "text": {"type": "string"},
                                "page": {"type": "integer"},
                                "paragraph": {"type": "integer"},
                            },
                        },
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
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string"},
                                "description": {"type": "string"},
                                "source_ref": {"type": "string"},
                                "parties": {"type": "string"},
                                "location": {"type": "string"},
                            },
                        },
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
                        "items": {"type": "string"},
                        "description": "List of identified weaknesses or gaps to probe",
                    },
                    "question_types": {
                        "type": "array",
                        "items": {"type": "string"},
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
    # ── Orchestrator management tools ─────────────────────────────────────
    "pipeline_status": {
        "type": "function",
        "function": {
            "name": "pipeline_status",
            "description": (
                "Record a pipeline lifecycle event and update orchestration metadata. "
                "Used to mark gate completions, halts, aborts, and what-if boundaries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "Lifecycle action: 'complete' | 'halt' | 'abort' | "
                            "'await_judge_input' | 'what_if_start' | 'what_if_complete'"
                        ),
                    },
                    "gate": {
                        "type": "string",
                        "description": "Gate name this event belongs to (e.g., 'gate1')",
                    },
                    "outcome": {
                        "type": "string",
                        "description": (
                            "Outcome for 'complete' actions: "
                            "'ready_for_review' | 'escalated' | 'failed'"
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Human-readable reason for halt or abort",
                    },
                },
                "required": ["action"],
            },
        },
    },
    "delegate_to_agent": {
        "type": "function",
        "function": {
            "name": "delegate_to_agent",
            "description": (
                "Dispatch a specific pipeline agent to run against the current CaseState. "
                "Returns the updated CaseState after the agent completes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": (
                            "Name of the agent to run. One of: case-processing, "
                            "complexity-routing, evidence-analysis, fact-reconstruction, "
                            "witness-analysis, legal-knowledge, argument-construction, "
                            "hearing-analysis, hearing-governance"
                        ),
                    },
                    "extra_instructions": {
                        "type": "string",
                        "description": (
                            "Optional additional instructions appended to the agent's "
                            "system prompt for this run only"
                        ),
                    },
                },
                "required": ["agent_name"],
            },
        },
    },
    "retry_failed_agent": {
        "type": "function",
        "function": {
            "name": "retry_failed_agent",
            "description": (
                "Retry a specific pipeline agent that previously failed or produced "
                "incomplete output. Accepts optional corrective instructions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Name of the agent to retry",
                    },
                    "failure_reason": {
                        "type": "string",
                        "description": "Description of why the previous run failed",
                    },
                    "extra_instructions": {
                        "type": "string",
                        "description": "Corrective instructions to guide the retry",
                    },
                    "attempt_number": {
                        "type": "integer",
                        "description": "Retry attempt count (1-based). Max auto-retries = 1.",
                        "default": 1,
                    },
                },
                "required": ["agent_name", "failure_reason"],
            },
        },
    },
    "escalate_case": {
        "type": "function",
        "function": {
            "name": "escalate_case",
            "description": (
                "Escalate the case to human review, halting automated processing. "
                "Must include a full escalation record. Sets status='escalated'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "escalation_source": {
                        "type": "string",
                        "description": (
                            "Source of escalation: 'orchestrator' | 'governance_audit' | "
                            "'judge' | 'routing_trigger'"
                        ),
                    },
                    "trigger_id": {
                        "type": "string",
                        "description": "Specific trigger or check ID that caused escalation",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Detailed explanation of why the case is being escalated",
                    },
                    "case_summary": {
                        "type": "string",
                        "description": "1-2 sentence case summary for the human reviewer",
                    },
                    "last_gate_completed": {
                        "type": "string",
                        "description": "Name of the last gate that completed, or null",
                    },
                    "recommended_human_action": {
                        "type": "string",
                        "description": "What the human reviewer should do with this case",
                    },
                },
                "required": [
                    "escalation_source",
                    "reason",
                    "case_summary",
                    "recommended_human_action",
                ],
            },
        },
    },
    "parallel_dispatch": {
        "type": "function",
        "function": {
            "name": "parallel_dispatch",
            "description": (
                "Dispatch multiple pipeline agents concurrently and await all completions "
                "(barrier sync). Used for gate2 parallel execution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of agent names to run in parallel. "
                            "All must complete before the barrier resolves."
                        ),
                    },
                    "gate": {
                        "type": "string",
                        "description": "Gate this parallel dispatch belongs to (e.g., 'gate2')",
                    },
                },
                "required": ["agent_names", "gate"],
            },
        },
    },
    "advance_gate": {
        "type": "function",
        "function": {
            "name": "advance_gate",
            "description": (
                "Signal that a gate review is complete and the pipeline should advance "
                "to the next gate. Records the Judge's decision and gate completion time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_gate": {
                        "type": "string",
                        "description": "Gate being completed (e.g., 'gate1')",
                    },
                    "to_gate": {
                        "type": "string",
                        "description": "Next gate to run (e.g., 'gate2')",
                    },
                    "judge_decision": {
                        "type": "string",
                        "description": "Judge's decision: 'accept' | 'amend' | 'reject'",
                    },
                    "judge_notes": {
                        "type": "string",
                        "description": "Optional notes from the Judge about this gate's output",
                    },
                },
                "required": ["from_gate", "to_gate", "judge_decision"],
            },
        },
    },
}

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "agents"

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _summarise_agent_output(agent_name: str, agent_output: dict[str, Any]) -> str:
    """Produce a short human-readable summary of an agent's output.

    Shown on the Building page's `agent_completed` card. The agents
    write to disjoint fields, so we tailor the summary by name rather
    than dumping the whole JSON blob.
    """
    if not agent_output:
        return "no output"
    if agent_name == "case-processing":
        parties = agent_output.get("parties") or []
        docs = agent_output.get("raw_documents") or []
        return f"{len(parties)} parties, {len(docs)} documents"
    if agent_name == "complexity-routing":
        meta = agent_output.get("case_metadata") or {}
        parts = []
        if meta.get("complexity"):
            parts.append(f"complexity={meta['complexity']}")
        if meta.get("route"):
            parts.append(f"route={meta['route']}")
        return " ".join(parts) or "routing decision recorded"
    if agent_name == "evidence-analysis":
        items = (agent_output.get("evidence_analysis") or {}).get("items") or []
        return f"{len(items)} evidence items analysed"
    if agent_name == "fact-reconstruction":
        facts = (agent_output.get("extracted_facts") or {}).get("facts") or []
        return f"{len(facts)} facts reconstructed"
    if agent_name == "witness-analysis":
        ws = (agent_output.get("witnesses") or {}).get("items") or []
        return f"{len(ws)} witness statements"
    if agent_name == "legal-knowledge":
        rules = agent_output.get("legal_rules") or []
        precs = agent_output.get("precedents") or []
        return f"{len(rules)} rules, {len(precs)} precedents"
    if agent_name == "argument-construction":
        args = agent_output.get("arguments") or {}
        return f"{len(args)} argument sides"
    if agent_name == "hearing-analysis":
        ha = agent_output.get("hearing_analysis") or {}
        if ha.get("recommendation"):
            return f"recommendation: {ha['recommendation']}"
        return "hearing analysis recorded"
    if agent_name == "hearing-governance":
        fc = agent_output.get("fairness_check") or {}
        if fc.get("passed") is not None:
            return f"fairness check {'passed' if fc['passed'] else 'failed'}"
        return "governance review recorded"
    return "output recorded"


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
        case_id = str(state.case_id) if state.case_id else "unknown"
        await publish_agent_event(
            case_id,
            {
                "case_id": case_id,
                "agent": agent_name,
                "event": "agent_started",
                "ts": datetime.now(UTC).isoformat(),
            },
        )
        try:
            return await self._run_agent_inner(agent_name, state, extra_instructions, case_id)
        except Exception as exc:
            await publish_agent_event(
                case_id,
                {
                    "case_id": case_id,
                    "agent": agent_name,
                    "event": "agent_failed",
                    "error": str(exc)[:500],
                    "ts": datetime.now(UTC).isoformat(),
                },
            )
            raise

    async def _run_agent_inner(
        self,
        agent_name: str,
        state: CaseState,
        extra_instructions: str | None,
        case_id: str,
    ) -> CaseState:
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

        await publish_agent_event(
            case_id,
            {
                "case_id": case_id,
                "agent": agent_name,
                "event": "thinking",
                "content": (
                    f"→ {model} · tools={len(tools) if tools else 0}"
                ),
                "ts": datetime.now(UTC).isoformat(),
            },
        )

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
                tool_result = await self._execute_tool_call(fn_name, fn_args)
                await publish_agent_event(
                    case_id,
                    {
                        "case_id": case_id,
                        "agent": agent_name,
                        "event": "tool_result",
                        "tool_name": fn_name,
                        # tool_result is JSON-encoded; keep it as a string
                        # for the UI but cap to avoid flooding the SSE stream.
                        "result": tool_result[:400],
                        "ts": datetime.now(UTC).isoformat(),
                    },
                )

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

            await publish_agent_event(
                case_id,
                {
                    "case_id": case_id,
                    "agent": agent_name,
                    "event": "thinking",
                    "content": f"→ {model} · continuing after {len(tool_calls_log)} tool call(s)",
                    "ts": datetime.now(UTC).isoformat(),
                },
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
        await publish_agent_event(
            case_id,
            {
                "case_id": case_id,
                "agent": agent_name,
                "event": "llm_response",
                "content": raw_content[:600],
                "ts": datetime.now(UTC).isoformat(),
            },
        )
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

        agent_output = normalize_agent_output(agent_name, agent_output)

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

        await publish_agent_event(
            case_id,
            {
                "case_id": case_id,
                "agent": agent_name,
                "event": "agent_completed",
                "output_summary": _summarise_agent_output(agent_name, agent_output),
                "ts": datetime.now(UTC).isoformat(),
            },
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


# ─────────────────────────────────────────────────────────────────────────────
# OrchestratorRunner
# High-level runner that mirrors the Pipeline Orchestrator Agent's logic in
# process.  Provides full lifecycle management: gate sequencing, parallel
# gate-2 dispatch, retry policy, escalation, and What-If forking.
# Used by API routes that want the full orchestrated flow rather than
# gate-by-gate control.
# ─────────────────────────────────────────────────────────────────────────────

# Maximum automatic retries per agent (beyond this, escalate or require judge)
_MAX_AUTO_RETRIES = 1

# Gate2 agents that can run concurrently
_GATE2_PARALLEL = [
    "evidence-analysis",
    "fact-reconstruction",
    "witness-analysis",
    "legal-knowledge",
]


class EscalationRequired(Exception):
    """Raised when the pipeline must halt and escalate to human review."""

    def __init__(self, reason: str, source: str, trigger_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.source = source
        self.trigger_id = trigger_id


class OrchestratorRunner:
    """Full-lifecycle pipeline runner managed by the orchestrator protocol.

    Wraps PipelineRunner and adds:
      - Parallel gate-2 dispatch with barrier synchronisation
      - Post-run guardrail checks for each agent
      - Automatic retry with corrective instructions (max 1 auto-retry)
      - Escalation on unrecoverable failures
      - What-If scenario forking
      - Orchestration metadata tracking in case_metadata.orchestration
    """

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._runner = PipelineRunner(client=client)
        self._retry_counts: dict[str, int] = {}

    # ── Orchestration metadata helpers ──────────────────────────────────

    @staticmethod
    def _init_orchestration(state: CaseState) -> CaseState:
        """Initialise the orchestration tracking block in case_metadata."""
        meta = dict(state.case_metadata)
        meta.setdefault(
            "orchestration",
            {
                "pipeline_version": "2.0",
                "gates_completed": [],
                "agents_run": [],
                "gate_statuses": {
                    "gate1": "pending",
                    "gate2": "pending",
                    "gate3": "pending",
                    "gate4": "pending",
                },
                "parallel_dispatch_results": {},
                "retry_log": [],
                "escalation_record": None,
                "what_if_runs": [],
                "pipeline_start_time": datetime.now(timezone.utc).isoformat(),
                "pipeline_end_time": None,
                "total_duration_seconds": None,
                "final_disposition": "in_progress",
            },
        )
        return state.model_copy(update={"case_metadata": meta})

    @staticmethod
    def _record_agent_run(
        state: CaseState,
        agent_name: str,
        status: str,
        retries: int = 0,
        start_time: str | None = None,
    ) -> CaseState:
        meta = dict(state.case_metadata)
        orch = dict(meta.get("orchestration", {}))
        agents_run: list = list(orch.get("agents_run", []))
        agents_run.append(
            {
                "agent_name": agent_name,
                "status": status,
                "retries": retries,
                "start_time": start_time or datetime.now(timezone.utc).isoformat(),
                "end_time": datetime.now(timezone.utc).isoformat(),
            }
        )
        orch["agents_run"] = agents_run
        meta["orchestration"] = orch
        return state.model_copy(update={"case_metadata": meta})

    @staticmethod
    def _set_gate_status(state: CaseState, gate: str, status: str) -> CaseState:
        meta = dict(state.case_metadata)
        orch = dict(meta.get("orchestration", {}))
        gate_statuses = dict(orch.get("gate_statuses", {}))
        gate_statuses[gate] = status
        if status == "complete":
            completed: list = list(orch.get("gates_completed", []))
            if gate not in completed:
                completed.append(gate)
            orch["gates_completed"] = completed
        orch["gate_statuses"] = gate_statuses
        meta["orchestration"] = orch
        return state.model_copy(update={"case_metadata": meta})

    @staticmethod
    def _record_escalation(
        state: CaseState, source: str, reason: str, trigger_id: str | None
    ) -> CaseState:
        meta = dict(state.case_metadata)
        orch = dict(meta.get("orchestration", {}))
        orch["escalation_record"] = {
            "source": source,
            "trigger_id": trigger_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        orch["final_disposition"] = "escalated"
        meta["orchestration"] = orch
        return state.model_copy(update={"case_metadata": meta})

    @staticmethod
    def _finalize_orchestration(state: CaseState, disposition: str) -> CaseState:
        meta = dict(state.case_metadata)
        orch = dict(meta.get("orchestration", {}))
        end = datetime.now(timezone.utc).isoformat()
        orch["pipeline_end_time"] = end
        orch["final_disposition"] = disposition
        start_str = orch.get("pipeline_start_time")
        if start_str:
            try:
                start = datetime.fromisoformat(start_str)
                end_dt = datetime.fromisoformat(end)
                orch["total_duration_seconds"] = (end_dt - start).total_seconds()
            except (ValueError, TypeError):
                pass
        meta["orchestration"] = orch
        return state.model_copy(update={"case_metadata": meta})

    # ── Guardrail checks ─────────────────────────────────────────────────

    @staticmethod
    def _check_gate1_post_run(state: CaseState, agent_name: str) -> tuple[bool, str]:
        """Return (ok, reason). ok=False means retry or escalate is needed."""
        if agent_name == "case-processing":
            if state.status == CaseStatusEnum.failed:
                return False, "jurisdiction_failed"
            meta = state.case_metadata
            if not meta.get("jurisdiction_valid", True) is False:
                pass  # valid
        if agent_name == "complexity-routing":
            route = state.case_metadata.get("route")
            if route not in ("proceed_automated", "proceed_with_review", "escalate_human", None):
                return False, f"invalid_route_value: {route}"
        return True, ""

    @staticmethod
    def _check_gate2_barrier(state: CaseState) -> list[str]:
        """Return list of issue descriptions after all gate2 agents complete."""
        issues = []
        ea = state.evidence_analysis
        if ea is None or (not ea.evidence_items and not ea.exhibits):
            issues.append("evidence_analysis produced no evidence items")
        ef = state.extracted_facts
        if ef is None or not ef.facts:
            issues.append("fact_reconstruction produced no facts")
        if not state.legal_rules:
            issues.append("legal_knowledge produced no legal rules")
        return issues

    @staticmethod
    def _check_gate3_post_run(state: CaseState, agent_name: str) -> tuple[bool, str]:
        if agent_name == "hearing-analysis":
            ha = state.hearing_analysis
            if ha and ha.preliminary_conclusion is not None:
                return False, "preliminary_conclusion must be null — retrying with correction"
        return True, ""

    @staticmethod
    def _check_gate4_post_run(state: CaseState) -> tuple[bool, str]:
        fc = state.fairness_check
        if fc is None:
            return False, "hearing_governance produced no fairness_check output"
        if fc.critical_issues_found:
            raise EscalationRequired(
                reason=f"Governance audit critical issues: {'; '.join(fc.issues)}",
                source="governance_audit",
                trigger_id="critical_issues_found",
            )
        return True, ""

    # ── Agent dispatch with retry ─────────────────────────────────────────

    async def _run_with_retry(
        self,
        state: CaseState,
        agent_name: str,
        extra_instructions: str | None = None,
    ) -> CaseState:
        """Run agent with up to _MAX_AUTO_RETRIES automatic retries."""
        retries = 0
        last_error: str = ""
        start_time = datetime.now(timezone.utc).isoformat()

        while retries <= _MAX_AUTO_RETRIES:
            try:
                state = await self._runner._run_agent(
                    agent_name, state, extra_instructions=extra_instructions
                )
                state = self._record_agent_run(state, agent_name, "success", retries, start_time)
                return state
            except CriticalToolFailure as exc:
                # Never retry critical failures — escalate immediately
                state = self._record_agent_run(
                    state, agent_name, "critical_failure", retries, start_time
                )
                raise EscalationRequired(
                    reason=f"CriticalToolFailure in {agent_name}: {exc}",
                    source="orchestrator",
                    trigger_id="critical_tool_failure",
                ) from exc
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Agent '%s' failed (attempt %d/%d): %s",
                    agent_name,
                    retries + 1,
                    _MAX_AUTO_RETRIES + 1,
                    last_error,
                )
                # Log retry
                meta = dict(state.case_metadata)
                orch = dict(meta.get("orchestration", {}))
                retry_log: list = list(orch.get("retry_log", []))
                retry_log.append(
                    {
                        "agent_name": agent_name,
                        "attempt": retries + 1,
                        "reason": last_error,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
                orch["retry_log"] = retry_log
                meta["orchestration"] = orch
                state = state.model_copy(update={"case_metadata": meta})

                if retries >= _MAX_AUTO_RETRIES:
                    state = self._record_agent_run(
                        state, agent_name, "failed_max_retries", retries, start_time
                    )
                    raise EscalationRequired(
                        reason=f"Agent '{agent_name}' failed after {retries + 1} attempts: {last_error}",
                        source="orchestrator",
                        trigger_id="max_retries_exceeded",
                    ) from exc

                retries += 1
                extra_instructions = (
                    f"RETRY {retries}/{_MAX_AUTO_RETRIES}: Previous run failed: {last_error}. "
                    "Please ensure all required output fields are populated correctly."
                )

        # Should be unreachable
        state = self._record_agent_run(state, agent_name, "failed", retries, start_time)
        raise EscalationRequired(
            reason=f"Agent '{agent_name}' exhausted retries",
            source="orchestrator",
            trigger_id="max_retries_exceeded",
        )

    # ── Gate runners ─────────────────────────────────────────────────────

    async def _run_gate1(self, state: CaseState) -> CaseState:
        state = self._set_gate_status(state, "gate1", "running")

        state = await self._run_with_retry(state, "case-processing")
        ok, reason = self._check_gate1_post_run(state, "case-processing")
        if not ok:
            if reason == "jurisdiction_failed":
                state = self._set_gate_status(state, "gate1", "complete")
                return self._finalize_orchestration(state, "failed")
            raise EscalationRequired(reason=reason, source="orchestrator", trigger_id=reason)

        state = await self._run_with_retry(state, "complexity-routing")
        route = state.case_metadata.get("route")
        if route == "escalate_human":
            trigger = (
                state.case_metadata.get("routing_factors", {}).get("unconditional_trigger")
                or "routing_escalation"
            )
            state = self._record_escalation(
                state,
                source="routing_trigger",
                reason=state.case_metadata.get("escalation_reason", "Complexity routing escalation"),
                trigger_id=trigger,
            )
            raise EscalationRequired(
                reason=f"Complexity routing triggered escalation: trigger={trigger}",
                source="routing_trigger",
                trigger_id=trigger,
            )

        state = self._set_gate_status(state, "gate1", "complete")
        return state

    async def _run_gate2_parallel(self, state: CaseState) -> CaseState:
        """Dispatch gate2 agents in parallel, then apply barrier checks."""
        state = self._set_gate_status(state, "gate2", "running")

        barrier_start = datetime.now(timezone.utc).isoformat()

        # Run all four gate2 agents concurrently from the same input state.
        # Each agent reads the shared CaseState and writes its own dedicated fields.
        results = await asyncio.gather(
            *[self._run_with_retry(state, agent) for agent in _GATE2_PARALLEL],
            return_exceptions=True,
        )

        # Merge gate2 outputs: apply each successful result's fields onto state
        failures: list[tuple[str, Exception]] = []
        for agent_name, result in zip(_GATE2_PARALLEL, results, strict=True):
            if isinstance(result, EscalationRequired):
                failures.append((agent_name, result))
                logger.error("Gate2 agent '%s' requires escalation: %s", agent_name, result.reason)
            elif isinstance(result, Exception):
                failures.append((agent_name, result))
                logger.error("Gate2 agent '%s' failed: %s", agent_name, result)
            else:
                # Merge fields written by this agent (those unique to its role)
                _GATE2_WRITE_FIELDS: dict[str, list[str]] = {
                    "evidence-analysis": ["evidence_analysis"],
                    "fact-reconstruction": ["extracted_facts"],
                    "witness-analysis": ["witnesses"],
                    "legal-knowledge": ["legal_rules", "precedents", "precedent_source_metadata"],
                }
                write_fields = _GATE2_WRITE_FIELDS.get(agent_name, [])
                merged = dict(state.model_dump())
                for field in write_fields:
                    agent_value = result.model_dump().get(field)
                    if agent_value is not None:
                        merged[field] = agent_value
                # Merge audit log from parallel result
                merged["audit_log"] = (
                    state.audit_log + [
                        e for e in result.audit_log if e not in state.audit_log
                    ]
                )
                state = CaseState(**merged)

        # Escalate if too many failures
        if len(failures) >= 2:
            failed_names = [name for name, _ in failures]
            raise EscalationRequired(
                reason=f"Gate2 parallel dispatch: {len(failures)} agents failed: {failed_names}",
                source="orchestrator",
                trigger_id="gate2_barrier_failure",
            )

        # Barrier checks
        issues = self._check_gate2_barrier(state)
        if issues:
            logger.warning("Gate2 barrier issues: %s", issues)
            # Attempt targeted retries for missing fields
            if "fact_reconstruction produced no facts" in issues:
                state = await self._run_with_retry(
                    state,
                    "fact-reconstruction",
                    extra_instructions="RETRY: Previous run produced no facts. Ensure facts[] is populated.",
                )
            if "legal_knowledge produced no legal rules" in issues:
                state = await self._run_with_retry(
                    state,
                    "legal-knowledge",
                    extra_instructions="RETRY: Previous run produced no legal rules. Ensure legal_rules[] is populated.",
                )
            if "evidence_analysis produced no evidence items" in issues:
                state = await self._run_with_retry(
                    state,
                    "evidence-analysis",
                    extra_instructions="RETRY: Previous run produced no evidence items. Ensure evidence_items[] is populated.",
                )

        # Impartiality check
        ea = state.evidence_analysis
        if ea is not None:
            impartiality = getattr(ea, "impartiality_check", None)
            if isinstance(impartiality, dict) and not impartiality.get("passed", True):
                raise EscalationRequired(
                    reason="Evidence Analysis impartiality check failed — bias detected in evidence weighting",
                    source="orchestrator",
                    trigger_id="impartiality_check_failed",
                )

        meta = dict(state.case_metadata)
        orch = dict(meta.get("orchestration", {}))
        parallel_results = dict(orch.get("parallel_dispatch_results", {}))
        parallel_results["gate2"] = {
            "status": "complete",
            "barrier_met_at": datetime.now(timezone.utc).isoformat(),
            "barrier_start": barrier_start,
            "agent_failures": len(failures),
        }
        orch["parallel_dispatch_results"] = parallel_results
        meta["orchestration"] = orch
        state = state.model_copy(update={"case_metadata": meta})

        state = self._set_gate_status(state, "gate2", "complete")
        return state

    async def _run_gate3(self, state: CaseState) -> CaseState:
        state = self._set_gate_status(state, "gate3", "running")

        state = await self._run_with_retry(state, "argument-construction")
        # Check both sides' weaknesses are present
        args = state.arguments or {}
        if isinstance(args, dict):
            for side_key in ("prosecution_argument", "claimant_position",
                             "defence_argument", "respondent_position"):
                if side_key in args and not args[side_key].get("weaknesses"):
                    state = await self._run_with_retry(
                        state,
                        "argument-construction",
                        extra_instructions=(
                            "RETRY: Weaknesses must be populated for BOTH sides. "
                            "Ensure prosecution/claimant AND defence/respondent weaknesses are present."
                        ),
                    )
                    break

        state = await self._run_with_retry(state, "hearing-analysis")
        ok, reason = self._check_gate3_post_run(state, "hearing-analysis")
        if not ok:
            state = await self._run_with_retry(
                state,
                "hearing-analysis",
                extra_instructions=(
                    "MANDATORY CORRECTION: You must set preliminary_conclusion=null "
                    "and confidence_score=null. Do NOT produce an outcome recommendation."
                ),
            )

        state = self._set_gate_status(state, "gate3", "complete")
        return state

    async def _run_gate4(self, state: CaseState) -> CaseState:
        state = self._set_gate_status(state, "gate4", "running")

        state = await self._run_with_retry(state, "hearing-governance")
        ok, reason = self._check_gate4_post_run(state)  # may raise EscalationRequired
        if not ok:
            # Missing fairness_check — retry once
            state = await self._run_with_retry(
                state,
                "hearing-governance",
                extra_instructions=(
                    "RETRY: fairness_check output was missing. "
                    "Ensure fairness_check field is fully populated with all required keys."
                ),
            )
            self._check_gate4_post_run(state)  # second failure escalates

        state = self._set_gate_status(state, "gate4", "complete")
        return state

    # ── Public API ────────────────────────────────────────────────────────

    async def run_full_pipeline(self, case_state: CaseState) -> CaseState:
        """Run the complete gated pipeline from intake to governance audit.

        Manages the full lifecycle:
          Gate1 (sequential) → Gate2 (parallel) → Gate3 (sequential) → Gate4 (governance)

        Returns the final CaseState with orchestration metadata populated.
        Raises EscalationRequired if the case must be reviewed by a human.
        """
        from src.pipeline.observability import pipeline_run  # lazy: avoids linter removal

        state = self._init_orchestration(case_state)

        try:
            with pipeline_run(
                case_id=str(state.case_id or "unknown"),
                run_id=state.run_id or "unknown",
                mode="orchestrated",
            ):
                state = await self._run_gate1(state)
                if state.status == CaseStatusEnum.failed:
                    return self._finalize_orchestration(state, "failed")

                state = await self._run_gate2_parallel(state)
                state = await self._run_gate3(state)
                state = await self._run_gate4(state)

        except EscalationRequired as exc:
            logger.warning("Pipeline escalation: %s (source=%s)", exc.reason, exc.source)
            state = self._record_escalation(
                state, source=exc.source, reason=exc.reason, trigger_id=exc.trigger_id
            )
            state = state.model_copy(update={"status": CaseStatusEnum.escalated})
            return self._finalize_orchestration(state, "escalated")

        # Pipeline completed successfully
        final_status = state.fairness_check
        if final_status and final_status.audit_passed and not final_status.critical_issues_found:
            state = state.model_copy(update={"status": CaseStatusEnum.ready_for_review})
            return self._finalize_orchestration(state, "ready_for_review")

        # Governance did not pass but no exception — escalate conservatively
        state = state.model_copy(update={"status": CaseStatusEnum.escalated})
        return self._finalize_orchestration(state, "escalated")

    async def run_what_if(
        self,
        base_state: CaseState,
        modifications: dict[str, Any],
        modification_type: str,
    ) -> CaseState:
        """Fork the pipeline for a What-If scenario.

        Args:
            base_state: The completed original pipeline state.
            modifications: Dict of CaseState fields to override for the scenario.
            modification_type: 'facts' | 'evidence' | 'credibility' | 'legal'

        Returns a new CaseState with a forked run_id, re-running only the
        minimum gates affected by the modification type.
        """
        import uuid

        # Fork: new run with parent reference
        forked = base_state.model_copy(
            update={
                "run_id": str(uuid.uuid4()),
                "parent_run_id": base_state.run_id,
                "status": CaseStatusEnum.processing,
                **modifications,
            }
        )
        forked = self._init_orchestration(forked)

        # Record what-if in parent state's orchestration metadata
        meta = dict(base_state.case_metadata)
        orch = dict(meta.get("orchestration", {}))
        what_if_runs: list = list(orch.get("what_if_runs", []))
        what_if_runs.append(
            {
                "scenario_id": forked.run_id,
                "parent_run_id": base_state.run_id,
                "modification_type": modification_type,
                "modifications": list(modifications.keys()),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        orch["what_if_runs"] = what_if_runs
        meta["orchestration"] = orch

        # Determine minimum re-run scope
        # Gate1 results (domain, jurisdiction, routing) carry over unchanged.
        # Gate2-4 always re-run because they depend on the modified fields.
        try:
            forked = await self._run_gate2_parallel(forked)
            forked = await self._run_gate3(forked)
            forked = await self._run_gate4(forked)
        except EscalationRequired as exc:
            forked = self._record_escalation(
                forked, source=exc.source, reason=exc.reason, trigger_id=exc.trigger_id
            )
            forked = forked.model_copy(update={"status": CaseStatusEnum.escalated})
            return self._finalize_orchestration(forked, "escalated")

        forked = forked.model_copy(update={"status": CaseStatusEnum.ready_for_review})
        return self._finalize_orchestration(forked, "ready_for_review")

