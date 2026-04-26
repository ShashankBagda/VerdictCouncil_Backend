"""Canonical runtime manifest for the LangGraph pipeline.

Single source of truth for the agent topology that the SSE bridge,
audit middleware, status-polling endpoint, and rerun endpoint all
agree on. Mirrors the nodes registered in
`src/pipeline/graph/builder.py` (intake → 4 parallel research
subagents → synthesis → audit, gated at gate1/2/3/4).

`prompts.py` keeps its own legacy 9-name `AGENT_ORDER` because the
prompt registry, model-tier table, and tool assignments are still
keyed by those display names. This module is the *runtime* view —
what the LangGraph nodes are actually called when they emit events
and write audit rows. The legacy → LangGraph map below lets
external callers send either form to the rerun endpoint without
breaking.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------

# Order matches the LangGraph topology: intake first, then the 4 parallel
# research subagents (evidence/facts/witnesses/law), then synthesis, then
# the auditor. Each ID is the exact string the SSE bridge emits and the
# audit middleware writes to `audit_logs.agent_name`.
PIPELINE_AGENT_ORDER: list[str] = [
    "intake",
    "research-evidence",
    "research-facts",
    "research-witnesses",
    "research-law",
    "synthesis",
    "audit",
]

PIPELINE_AGENT_LABELS: dict[str, str] = {
    "intake": "Intake",
    "research-evidence": "Evidence Research",
    "research-facts": "Fact Reconstruction",
    "research-witnesses": "Witness Analysis",
    "research-law": "Legal Research",
    "synthesis": "Synthesis",
    "audit": "Auditor",
}

# ---------------------------------------------------------------------------
# Gate (layer) grouping
# ---------------------------------------------------------------------------

# Mirrors builder.py edges: each gate pause/apply pair sits between two
# layers. Gate1 follows intake; gate2 follows the research fan-in; gate3
# follows synthesis; gate4 follows audit.
GATE_AGENTS: dict[str, list[str]] = {
    "gate1": ["intake"],
    "gate2": [
        "research-evidence",
        "research-facts",
        "research-witnesses",
        "research-law",
    ],
    "gate3": ["synthesis"],
    "gate4": ["audit"],
}

GATE_LABELS: dict[str, str] = {
    "gate1": "Intake",
    "gate2": "Research",
    "gate3": "Synthesis",
    "gate4": "Audit",
}

# Reverse index: agent_id → gate.
AGENT_GATE: dict[str, str] = {
    agent: gate for gate, agents in GATE_AGENTS.items() for agent in agents
}

# ---------------------------------------------------------------------------
# Graph topology (for the frontend Graph Mesh visualization)
# ---------------------------------------------------------------------------

PIPELINE_EDGES: list[tuple[str, str]] = [
    ("intake", "research-evidence"),
    ("intake", "research-facts"),
    ("intake", "research-witnesses"),
    ("intake", "research-law"),
    ("research-evidence", "synthesis"),
    ("research-facts", "synthesis"),
    ("research-witnesses", "synthesis"),
    ("research-law", "synthesis"),
    ("synthesis", "audit"),
]

# ---------------------------------------------------------------------------
# Legacy compatibility
# ---------------------------------------------------------------------------

# Pre-LangGraph clients (and existing audit-log rows from the SAM era)
# refer to agents by the 9-name display list in `prompts.AGENT_ORDER`.
# This map collapses those onto the 7 LangGraph nodes so the rerun
# endpoint and any historical-data reader can accept either form.
LEGACY_AGENT_ID_TO_LANGGRAPH: dict[str, str] = {
    "case-processing": "intake",
    "complexity-routing": "intake",
    "evidence-analysis": "research-evidence",
    "fact-reconstruction": "research-facts",
    "witness-analysis": "research-witnesses",
    "legal-knowledge": "research-law",
    "argument-construction": "synthesis",
    "hearing-analysis": "synthesis",
    "hearing-governance": "audit",
}


def normalize_agent_id(agent_id: str | None) -> str | None:
    """Translate a legacy display name to its LangGraph node ID.

    Returns the input unchanged if it is already a LangGraph ID or if it
    is not recognised (the caller is responsible for validating the
    result against `PIPELINE_AGENT_ORDER`).
    """
    if not agent_id:
        return agent_id
    return LEGACY_AGENT_ID_TO_LANGGRAPH.get(agent_id, agent_id)


def manifest_dict() -> dict:
    """Serialize the manifest as a JSON-friendly dict for the API."""
    return {
        "agents": [
            {
                "id": agent_id,
                "label": PIPELINE_AGENT_LABELS[agent_id],
                "gate": AGENT_GATE[agent_id],
                "layer": GATE_LABELS[AGENT_GATE[agent_id]],
            }
            for agent_id in PIPELINE_AGENT_ORDER
        ],
        "gates": [
            {
                "id": gate_id,
                "label": GATE_LABELS[gate_id],
                "agents": agents,
            }
            for gate_id, agents in GATE_AGENTS.items()
        ],
        "edges": [{"source": s, "target": t} for s, t in PIPELINE_EDGES],
        "legacy_alias_map": dict(LEGACY_AGENT_ID_TO_LANGGRAPH),
    }
