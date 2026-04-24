"""Unit tests for src.pipeline.graph.state and src.pipeline.graph.prompts."""

from __future__ import annotations

from src.pipeline.graph.prompts import (
    AGENT_MODEL_TIER,
    AGENT_ORDER,
    AGENT_PROMPTS,
    AGENT_TOOLS,
    GATE2_PARALLEL_AGENTS,
    GATE_AGENTS,
    MODEL_TIER_MAP,
)
from src.pipeline.graph.state import _merge_case
from src.shared.case_state import (
    AuditEntry,
    CaseState,
    EvidenceAnalysis,
    ExtractedFacts,
    Witnesses,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base() -> CaseState:
    return CaseState()


def _with_domain(domain: str) -> CaseState:
    return CaseState(domain=domain)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _merge_case — sequential (non-parallel) scenarios
# ---------------------------------------------------------------------------


class TestMergeCaseSequential:
    def test_new_value_overwrites_none_base(self):
        base = _base()  # domain is None
        update = CaseState(domain="traffic_violation")  # type: ignore[arg-type]
        merged = _merge_case(base, update)
        assert merged.domain == "traffic_violation"

    def test_new_value_overwrites_existing_base(self):
        base = CaseState(domain="traffic_violation")  # type: ignore[arg-type]
        update = CaseState(domain="small_claims")  # type: ignore[arg-type]
        merged = _merge_case(base, update)
        assert merged.domain == "small_claims"

    def test_none_update_does_not_clear_base(self):
        base = CaseState(domain="traffic_violation")  # type: ignore[arg-type]
        update = _base()  # domain is None
        merged = _merge_case(base, update)
        # Base already has a value; update is None → parallel branch didn't own it
        assert merged.domain == "traffic_violation"

    def test_identical_values_are_noop(self):
        base = CaseState(domain="traffic_violation")  # type: ignore[arg-type]
        update = CaseState(domain="traffic_violation")  # type: ignore[arg-type]
        merged = _merge_case(base, update)
        assert merged.domain == "traffic_violation"

    def test_empty_list_update_preserves_base(self):
        base = CaseState(legal_rules=[{"rule": "A"}])
        update = _base()  # legal_rules is []
        merged = _merge_case(base, update)
        assert merged.legal_rules == [{"rule": "A"}]

    def test_non_empty_list_update_overwrites_base(self):
        base = CaseState(legal_rules=[{"rule": "A"}])
        update = CaseState(legal_rules=[{"rule": "B"}, {"rule": "C"}])
        merged = _merge_case(base, update)
        assert merged.legal_rules == [{"rule": "B"}, {"rule": "C"}]

    def test_status_updated_sequentially(self):
        base = CaseState(status="pending")  # type: ignore[arg-type]
        update = CaseState(status="processing")  # type: ignore[arg-type]
        merged = _merge_case(base, update)
        assert merged.status == "processing"

    def test_non_owned_submodel_preserved(self):
        base = CaseState(evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]))
        update = _base()  # evidence_analysis is None
        merged = _merge_case(base, update)
        assert merged.evidence_analysis is not None
        assert merged.evidence_analysis.evidence_items == [{"id": "e1"}]


# ---------------------------------------------------------------------------
# _merge_case — Gate-2 parallel fan-out scenarios
# ---------------------------------------------------------------------------


class TestMergeCaseParallel:
    """Simulate 4 parallel L2 agent outputs being merged into shared state."""

    def _gate2_base(self) -> CaseState:
        """Minimal state that Gate-2 nodes start with (evidence etc. all None)."""
        return CaseState(domain="traffic_violation")  # type: ignore[arg-type]

    def test_evidence_update_populates_none_base(self):
        base = self._gate2_base()
        ev = EvidenceAnalysis(evidence_items=[{"id": "e1", "type": "photo"}])
        update = CaseState(evidence_analysis=ev)
        merged = _merge_case(base, update)
        assert merged.evidence_analysis is not None
        assert len(merged.evidence_analysis.evidence_items) == 1

    def test_fact_update_populates_none_base(self):
        base = self._gate2_base()
        facts = ExtractedFacts(facts=[{"fact": "Defendant was speeding"}])
        update = CaseState(extracted_facts=facts)
        merged = _merge_case(base, update)
        assert merged.extracted_facts is not None
        assert len(merged.extracted_facts.facts) == 1

    def test_parallel_merge_does_not_cross_contaminate(self):
        """Evidence agent output must not zero out facts from fact agent."""
        after_evidence = CaseState(
            domain="traffic_violation",  # type: ignore[arg-type]
            evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]),
        )
        # Fact agent returns its own update; evidence_analysis is None in its output
        fact_update = CaseState(extracted_facts=ExtractedFacts(facts=[{"fact": "fact-A"}]))
        merged = _merge_case(after_evidence, fact_update)
        # Both owned fields must survive
        assert merged.evidence_analysis is not None
        assert merged.extracted_facts is not None

    def test_witness_does_not_clear_evidence(self):
        base = CaseState(
            domain="traffic_violation",  # type: ignore[arg-type]
            evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]),
            extracted_facts=ExtractedFacts(facts=[{"fact": "A"}]),
        )
        witness_update = CaseState(witnesses=Witnesses(witnesses=[{"name": "Alice"}]))
        merged = _merge_case(base, witness_update)
        assert merged.evidence_analysis is not None
        assert merged.extracted_facts is not None
        assert merged.witnesses is not None

    def test_legal_knowledge_does_not_clear_parallel_fields(self):
        base = CaseState(
            domain="traffic_violation",  # type: ignore[arg-type]
            evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}]),
            extracted_facts=ExtractedFacts(facts=[{"fact": "A"}]),
            witnesses=Witnesses(witnesses=[{"name": "Alice"}]),
        )
        legal_update = CaseState(
            legal_rules=[{"rule": "Speed limit 60km/h"}],
            precedents=[{"case": "Smith v Jones"}],
        )
        merged = _merge_case(base, legal_update)
        assert merged.evidence_analysis is not None
        assert merged.extracted_facts is not None
        assert merged.witnesses is not None
        assert merged.legal_rules == [{"rule": "Speed limit 60km/h"}]
        assert merged.precedents == [{"case": "Smith v Jones"}]

    def test_all_four_l2_agents_accumulated(self):
        """Simulate sequential accumulation across all 4 parallel branch outputs."""
        state = _base()

        # evidence-analysis fires first
        state = _merge_case(
            state,
            CaseState(evidence_analysis=EvidenceAnalysis(evidence_items=[{"id": "e1"}])),
        )
        # fact-reconstruction
        state = _merge_case(
            state,
            CaseState(extracted_facts=ExtractedFacts(facts=[{"fact": "A"}])),
        )
        # witness-analysis
        state = _merge_case(
            state,
            CaseState(witnesses=Witnesses(witnesses=[{"name": "Alice"}])),
        )
        # legal-knowledge
        state = _merge_case(
            state,
            CaseState(legal_rules=[{"rule": "R1"}]),
        )

        assert state.evidence_analysis is not None
        assert state.extracted_facts is not None
        assert state.witnesses is not None
        assert state.legal_rules == [{"rule": "R1"}]


# ---------------------------------------------------------------------------
# _merge_case — audit log
# ---------------------------------------------------------------------------


class TestMergeCaseAuditLog:
    def _entry(self, agent: str, action: str) -> AuditEntry:
        return AuditEntry(agent=agent, action=action)

    def test_audit_log_extended_with_new_entries(self):
        e1 = self._entry("case-processing", "completed")
        e2 = self._entry("complexity-routing", "completed")
        base = CaseState(audit_log=[e1])
        update = CaseState(audit_log=[e1, e2])
        merged = _merge_case(base, update)
        assert len(merged.audit_log) == 2

    def test_audit_log_deduplicates_identical_entries(self):
        e1 = self._entry("case-processing", "completed")
        base = CaseState(audit_log=[e1])
        update = CaseState(audit_log=[e1])  # same entry again
        merged = _merge_case(base, update)
        assert len(merged.audit_log) == 1

    def test_audit_log_from_empty_base(self):
        e1 = self._entry("case-processing", "completed")
        base = _base()
        update = CaseState(audit_log=[e1])
        merged = _merge_case(base, update)
        assert len(merged.audit_log) == 1

    def test_audit_log_parallel_accumulation(self):
        """Each L2 agent appends its own entry; all four must survive."""
        state = _base()
        for agent in ["evidence-analysis", "fact-reconstruction", "witness-analysis", "legal-knowledge"]:
            entry = self._entry(agent, "completed")
            update = CaseState(audit_log=[entry])
            state = _merge_case(state, update)
        assert len(state.audit_log) == 4

    def test_audit_log_order_preserved(self):
        e1 = self._entry("evidence-analysis", "started")
        e2 = self._entry("evidence-analysis", "completed")
        base = CaseState(audit_log=[e1])
        update = CaseState(audit_log=[e1, e2])
        merged = _merge_case(base, update)
        assert merged.audit_log[0].action == "started"
        assert merged.audit_log[1].action == "completed"


# ---------------------------------------------------------------------------
# prompts.py — constant integrity
# ---------------------------------------------------------------------------


class TestPromptConstants:
    def test_agent_order_has_nine_entries(self):
        assert len(AGENT_ORDER) == 9

    def test_agent_prompts_keys_match_agent_order(self):
        assert set(AGENT_PROMPTS.keys()) == set(AGENT_ORDER)

    def test_agent_prompts_are_non_empty_strings(self):
        for agent, prompt in AGENT_PROMPTS.items():
            assert isinstance(prompt, str), f"{agent} prompt is not a string"
            assert len(prompt) > 100, f"{agent} prompt is suspiciously short"

    def test_agent_model_tier_keys_match_agent_order(self):
        assert set(AGENT_MODEL_TIER.keys()) == set(AGENT_ORDER)

    def test_agent_model_tier_values_are_valid_tiers(self):
        valid_tiers = set(MODEL_TIER_MAP.keys())
        for agent, tier in AGENT_MODEL_TIER.items():
            assert tier in valid_tiers, f"{agent} has invalid tier: {tier}"

    def test_agent_tools_keys_match_agent_order(self):
        assert set(AGENT_TOOLS.keys()) == set(AGENT_ORDER)

    def test_agent_tools_values_are_lists(self):
        for agent, tools in AGENT_TOOLS.items():
            assert isinstance(tools, list), f"{agent}.tools is not a list"

    def test_gate_agents_covers_all_agents(self):
        all_gated = [a for agents in GATE_AGENTS.values() for a in agents]
        assert set(all_gated) == set(AGENT_ORDER)

    def test_gate2_parallel_agents_in_gate2(self):
        gate2 = set(GATE_AGENTS["gate2"])
        assert set(GATE2_PARALLEL_AGENTS) == gate2

    def test_model_tier_map_has_four_tiers(self):
        assert set(MODEL_TIER_MAP.keys()) == {"lightweight", "efficient", "strong", "frontier"}

    def test_legal_knowledge_has_search_tools(self):
        """Legal knowledge must have both search tools — critical for precedent retrieval."""
        tools = AGENT_TOOLS["legal-knowledge"]
        assert "search_precedents" in tools
        assert "search_domain_guidance" in tools

    def test_complexity_routing_has_no_tools(self):
        assert AGENT_TOOLS["complexity-routing"] == []
