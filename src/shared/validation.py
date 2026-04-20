from typing import Any

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
    "deliberation": {"deliberation"},
    "governance-verdict": {"fairness_check", "verdict_recommendation", "status"},
}

# Fields that all agents can append to
APPEND_ONLY_FIELDS = {"audit_log"}


class FieldOwnershipError(Exception):
    pass


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
                f"Agent '{agent_name}' is not allowed to write to field '{key}'. "
                f"Allowed fields: {allowed}"
            )
