from typing import Any

# Fields the complexity-routing LLM should nest inside case_metadata but
# sometimes emits at the top level of its JSON response.
_COMPLEXITY_ROUTING_METADATA_FIELDS = frozenset(
    {
        "complexity",
        "complexity_score",
        "route",
        "routing_factors",
        "vulnerability_assessment",
        "escalation_reason",
        "pipeline_halt",
    }
)

# Maps each agent to the CaseState fields it is allowed to write
FIELD_OWNERSHIP: dict[str, set[str]] = {
    "case-processing": {
        "case_id",
        "run_id",
        "domain",
        "status",
        "parties",
        "case_metadata",
        "raw_documents",
    },
    "complexity-routing": {"status", "case_metadata"},
    "evidence-analysis": {"evidence_analysis"},
    "fact-reconstruction": {"extracted_facts"},
    "witness-analysis": {"witnesses"},
    "legal-knowledge": {"legal_rules", "precedents", "precedent_source_metadata"},
    "argument-construction": {"arguments"},
    "hearing-analysis": {"hearing_analysis"},
    "hearing-governance": {"fairness_check", "status"},
}

# Fields that all agents can append to
APPEND_ONLY_FIELDS = {"audit_log"}


class FieldOwnershipError(Exception):
    pass


def normalize_agent_output(agent_name: str, agent_output: dict[str, Any]) -> dict[str, Any]:
    """Coerce LLM output into the expected CaseState shape before merging.

    complexity-routing is instructed to nest its fields inside case_metadata,
    but occasionally emits them at the top level.  Move any such stragglers
    into case_metadata so they are not silently stripped by the ownership
    check.
    """
    if agent_name != "complexity-routing":
        return agent_output

    stray = {k: v for k, v in agent_output.items() if k in _COMPLEXITY_ROUTING_METADATA_FIELDS}
    if not stray:
        return agent_output

    output = dict(agent_output)
    meta = dict(output.get("case_metadata") or {})
    for k, v in stray.items():
        if k not in meta:
            meta[k] = v
        del output[k]
    output["case_metadata"] = meta
    return output


def validate_field_ownership(
    agent_name: str,
    original: dict[str, Any],
    updated: dict[str, Any],
) -> None:
    """Validate that an agent only writes to its designated fields.

    Raises FieldOwnershipError if the agent attempts to write to
    unauthorized fields.
    """
    allowed = FIELD_OWNERSHIP.get(agent_name, set())

    for key in updated:
        if key in APPEND_ONLY_FIELDS:
            continue
        if updated[key] != original.get(key) and key not in allowed:
            raise FieldOwnershipError(
                f"Agent '{agent_name}' is not allowed to write to field '{key}'. Allowed fields: {allowed}"  # noqa: E501
            )
