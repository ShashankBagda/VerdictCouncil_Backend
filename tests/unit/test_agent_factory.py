"""Sprint 1 1.A1.4 — phase + research subagent factory unit tests.

Asserts each phase / scope's tool list and schema explicitly. Does NOT
invoke real models — that's covered by integration tests in 1.A1.5+.
"""

from __future__ import annotations

import pytest


def test_phase_tool_names_are_explicit_and_least_privilege():
    from src.pipeline.graph.agents import factory

    assert factory.PHASE_TOOL_NAMES == {
        "intake": ["parse_document"],
        "synthesis": ["search_precedents"],
        "audit": [],
    }


def test_research_tool_names_are_explicit_and_least_privilege():
    from src.pipeline.graph.agents import factory

    assert factory.RESEARCH_TOOL_NAMES == {
        "evidence": ["parse_document"],
        "facts": ["parse_document"],
        "witnesses": ["parse_document"],
        "law": ["search_legal_rules", "search_precedents"],
    }


def test_audit_phase_has_zero_tools():
    """Auditor independence guarantee — Sprint 0.4 §2 / 0.5 §5 D-1.

    No retrieval, no document parse: the auditor inspects completed
    state and may not introduce new evidence.
    """
    from src.pipeline.graph.agents import factory

    assert factory.PHASE_TOOL_NAMES["audit"] == []


def test_intake_phase_has_only_parse_document():
    from src.pipeline.graph.agents import factory

    assert factory.PHASE_TOOL_NAMES["intake"] == ["parse_document"]


def test_law_is_only_research_subagent_with_search_tools():
    from src.pipeline.graph.agents import factory

    search_tools = {"search_legal_rules", "search_precedents"}
    for scope, names in factory.RESEARCH_TOOL_NAMES.items():
        intersection = set(names) & search_tools
        if scope == "law":
            assert intersection == search_tools, (
                f"law subagent must hold both search tools, got {names!r}"
            )
        else:
            assert intersection == set(), (
                f"non-law subagent {scope!r} cannot hold search tools, got {names!r}"
            )


def test_phase_schemas_match_expected_classes():
    from src.pipeline.graph.agents import factory
    from src.pipeline.graph.schemas import AuditOutput, IntakeOutput, SynthesisOutput

    assert {
        "intake": IntakeOutput,
        "synthesis": SynthesisOutput,
        "audit": AuditOutput,
    } == factory.PHASE_SCHEMAS


def test_research_schemas_match_expected_classes():
    from src.pipeline.graph.agents import factory
    from src.pipeline.graph.schemas import (
        EvidenceResearch,
        FactsResearch,
        LawResearch,
        WitnessesResearch,
    )

    assert {
        "evidence": EvidenceResearch,
        "facts": FactsResearch,
        "witnesses": WitnessesResearch,
        "law": LawResearch,
    } == factory.RESEARCH_SCHEMAS


def test_audit_phase_uses_strict_response_format():
    """`AuditOutput` is the one phase using OpenAI strict JSON schema
    (Sprint 0.5 §5 D-4)."""
    from src.pipeline.graph.schemas import AuditOutput

    config = AuditOutput.model_config
    assert config.get("extra") == "forbid"
    assert config.get("strict") is True


def test_other_phases_extra_forbid_but_not_strict():
    from src.pipeline.graph.schemas import IntakeOutput, SynthesisOutput

    for cls in (IntakeOutput, SynthesisOutput):
        config = cls.model_config
        assert config.get("extra") == "forbid", f"{cls.__name__} must use extra=forbid"
        assert config.get("strict") is not True, (
            f"{cls.__name__} must NOT use strict=True (only AuditOutput is strict)"
        )


def test_make_phase_node_returns_callable_for_each_phase():
    from src.pipeline.graph.agents import factory

    for phase in ("intake", "synthesis", "audit"):
        node = factory.make_phase_node(phase)
        assert callable(node)
        assert node.__name__ == f"phase_node_{phase}"


def test_make_research_subagent_returns_callable_for_each_scope():
    from src.pipeline.graph.agents import factory

    for scope in ("evidence", "facts", "witnesses", "law"):
        node = factory.make_research_subagent(scope)
        assert callable(node)
        assert node.__name__ == f"phase_node_research-{scope}"


def test_unknown_phase_raises_value_error():
    from src.pipeline.graph.agents import factory

    with pytest.raises(ValueError, match="Unknown phase"):
        factory.make_phase_node("intakeology")


def test_unknown_research_scope_raises_value_error():
    from src.pipeline.graph.agents import factory

    with pytest.raises(ValueError, match="Unknown research scope"):
        factory.make_research_subagent("paranormal")


def test_phase_middleware_stack_includes_all_four_hooks():
    from src.pipeline.graph.agents import factory
    from src.pipeline.graph.middleware import (
        audit_tool_call,
        cancel_check,
        sse_tool_emitter,
        token_usage_emitter,
    )

    assert set(factory.PHASE_MIDDLEWARE) == {
        cancel_check,
        sse_tool_emitter,
        audit_tool_call,
        token_usage_emitter,
    }
