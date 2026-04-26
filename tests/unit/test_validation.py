"""Sprint 1 1.A1.SEC3 — Pydantic-based field ownership regression test.

Replaces the legacy `FIELD_OWNERSHIP` allowlist (deleted in this sprint).
The new contract: every phase output schema declares
`model_config = ConfigDict(extra="forbid")`, so any agent attempt to
emit an undeclared field raises `pydantic.ValidationError`. LangChain's
`ToolStrategy(handle_errors=True)` retries the model call once with the
validation error injected as corrective feedback (V-11), giving the
agent one chance to fix its output without burning a full pipeline
retry.

Sprint 0 §0.4 architecture proposal mandated this swap: a Pydantic
schema is the single source of truth for what fields a phase owns.
The runtime allowlist + manual strip-and-log code path is gone.
"""

from __future__ import annotations

import pytest
from pydantic import ConfigDict, ValidationError

from src.pipeline.graph.schemas import (
    AuditOutput,
    EvidenceResearch,
    FactsResearch,
    IntakeOutput,
    LawResearch,
    SynthesisOutput,
    WitnessesResearch,
)

# Every phase + research subagent output schema declared in 1.A1.4. Listing
# them by name (rather than iterating module attributes) keeps the test
# explicit about the contract surface.
ALL_PHASE_OUTPUT_SCHEMAS = [
    IntakeOutput,
    EvidenceResearch,
    FactsResearch,
    WitnessesResearch,
    LawResearch,
    SynthesisOutput,
    AuditOutput,
]


@pytest.mark.parametrize("schema_cls", ALL_PHASE_OUTPUT_SCHEMAS)
def test_phase_schema_declares_extra_forbid(schema_cls: type) -> None:
    """Every phase output schema must reject undeclared fields."""
    config = getattr(schema_cls, "model_config", None)
    assert config is not None, f"{schema_cls.__name__} is missing model_config"

    # Pydantic v2 stores it as a dict-like ConfigDict; accept either form.
    extra_setting = (
        config.get("extra")
        if isinstance(config, dict | ConfigDict)
        else getattr(config, "extra", None)
    )
    assert extra_setting == "forbid", (
        f"{schema_cls.__name__}.model_config must set extra='forbid' "
        f"(got {extra_setting!r}). The Pydantic schema is the single source "
        "of truth for field ownership; ToolStrategy retries on the resulting "
        "ValidationError. See Sprint 0 §0.4 + 1.A1.SEC3."
    )


def test_research_subagent_unknown_field_raises_validation_error() -> None:
    """An agent attempting to emit an undeclared field must fail validation."""
    with pytest.raises(ValidationError) as exc_info:
        EvidenceResearch(
            evidence_items=[],
            credibility_scores={},
            unauthorized_field="this should not be allowed",  # type: ignore[call-arg]
        )

    err = str(exc_info.value)
    assert "unauthorized_field" in err and "Extra inputs" in err, (
        f"ValidationError should call out the extra field; got: {err}"
    )


def test_intake_output_unknown_field_raises_validation_error() -> None:
    """Same contract on the intake (phase, not research-subagent) schema."""
    with pytest.raises(ValidationError):
        IntakeOutput(
            extracted_metadata={},
            parsed_documents=[],
            secret_field=42,  # type: ignore[call-arg]
        )


def test_audit_output_unknown_field_raises_validation_error() -> None:
    """AuditOutput uses strict mode + extra=forbid (Sprint 0.5 §5 D-4)."""
    with pytest.raises(ValidationError):
        AuditOutput(
            fairness_check={  # type: ignore[arg-type]
                "critical_issues_found": False,
                "issues": [],
                "mitigations": [],
            },
            status="passed",
            unauthorized="rogue",  # type: ignore[call-arg]
        )


def test_audit_output_accepts_recommend_send_back() -> None:
    """4.A3.14 — `recommend_send_back` is an optional structured recommendation.

    The auditor sets this when it spots an issue the judge should act on
    by rewinding to a past phase. The gate4 review panel surfaces it as
    a 'Send back to ▼' dropdown.
    """
    out = AuditOutput(
        fairness_check={  # type: ignore[arg-type]
            "critical_issues_found": True,
            "audit_passed": False,
            "issues": ["uncertainty flag on conclusion 2"],
            "recommendations": [],
        },
        status="ready_for_review",
        recommend_send_back={  # type: ignore[arg-type]
            "to_phase": "synthesis",
            "reason": "uncertainty flag on conclusion 2",
        },
    )
    assert out.recommend_send_back is not None
    assert out.recommend_send_back.to_phase == "synthesis"
    assert out.recommend_send_back.reason == "uncertainty flag on conclusion 2"


def test_audit_output_recommend_send_back_rejects_audit_phase() -> None:
    """`audit` is excluded from rewind targets — sending back to audit is
    a rerun-audit, not a rewind."""
    with pytest.raises(ValidationError):
        AuditOutput(
            fairness_check={  # type: ignore[arg-type]
                "critical_issues_found": False,
                "audit_passed": True,
                "issues": [],
                "recommendations": [],
            },
            status="ready_for_review",
            recommend_send_back={  # type: ignore[arg-type]
                "to_phase": "audit",
                "reason": "self-rewind",
            },
        )


def test_audit_output_recommend_send_back_rejects_extra_fields() -> None:
    """SendBackRecommendation enforces `extra='forbid'` (Sprint 0.5 §5 D-4)."""
    with pytest.raises(ValidationError):
        AuditOutput(
            fairness_check={  # type: ignore[arg-type]
                "critical_issues_found": False,
                "audit_passed": True,
                "issues": [],
                "recommendations": [],
            },
            status="ready_for_review",
            recommend_send_back={  # type: ignore[arg-type]
                "to_phase": "synthesis",
                "reason": "test",
                "stowaway": "rejected",
            },
        )
