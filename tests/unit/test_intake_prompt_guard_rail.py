"""Q2.4 — intake prompt has explicit guard rails against silent
intake-stage halts.

The failure mode this prompt change targets: agent saw two `file_id`s
in `raw_documents`, never called `parse_document`, and gave up by
setting `status='failed'` because `parties` was empty. The prompt
must now explicitly forbid that path.

These are prompt-shape assertions — not behavioural tests. They
guarantee the rules are present so a future prompt rewrite can't
silently strip them. The behavioural verification (real agent on a
failing payload) lives in Q2.6's e2e suite.
"""

from __future__ import annotations

from src.pipeline.graph.prompts import AGENT_PROMPTS

INTAKE_PROMPT = AGENT_PROMPTS["case-processing"]


def test_intake_prompt_forbids_halting_with_unprocessed_documents():
    """If `raw_documents` has any entries, the agent must process them
    (cached `parsed_text` first, fall back to `parse_document`) before
    deciding the case can't proceed."""
    # Phrasing isn't fixed — assert the load-bearing concepts appear.
    assert "raw_documents" in INTAKE_PROMPT
    assert "parsed_text" in INTAKE_PROMPT
    # The "must call parse_document" rule must be present somewhere
    # AND tied to the empty-parties / non-empty-raw_documents trigger.
    assert "parse_document" in INTAKE_PROMPT
    # The guard rail wording is anchored on a stable header so a
    # rewrite that drops the rule will fail this test even if other
    # parse_document mentions remain.
    assert "INTAKE GUARD RAIL" in INTAKE_PROMPT


def test_intake_prompt_prefers_processing_over_halt_on_ambiguous_extraction():
    """When extraction is ambiguous after parsing, the agent should
    leave the case in `status='processing'` so downstream agents
    (complexity-routing) can request clarification — not set
    `status='failed'` and burn the run."""
    assert "INTAKE GUARD RAIL" in INTAKE_PROMPT
    # Concrete instruction must appear: prefer processing over failed
    # on ambiguous extraction.
    assert "ambiguous" in INTAKE_PROMPT.lower()
    # The rule must reference status='processing' as the right
    # response (not status='failed').
    guard_rail_section = INTAKE_PROMPT.split("INTAKE GUARD RAIL", 1)[1]
    # Within the guard-rail section, both rules must be present.
    assert "parse_document" in guard_rail_section
    assert "status='processing'" in guard_rail_section


def test_intake_prompt_mentions_intake_extraction_as_authoritative():
    """Q2.3b: the runner now bridges Case.intake_extraction onto
    CaseState. The prompt must tell the agent how to treat it —
    authoritative pre-parse data the runner has already merged
    where empty Case columns existed."""
    assert "intake_extraction" in INTAKE_PROMPT
    # The "use it to ground your extraction" intent must be present
    # so a future rewrite can't strip the cross-reference.
    assert "PRE-PARSE EXTRACTION" in INTAKE_PROMPT


def test_intake_prompt_has_conversational_mode_section():
    """Q1.6a: intake gains a "WHEN IN CONVERSATIONAL MODE" section
    that tells the agent to emit prose first when the runner invokes
    it via the conversational path. JSON-mode path still uses the
    same schema and MISSING FIELD PROTOCOL — the new section is
    additive."""
    assert "WHEN IN CONVERSATIONAL MODE" in INTAKE_PROMPT
    # Anchor on the load-bearing instructions: prose, tool calls, summary.
    section = INTAKE_PROMPT.split("WHEN IN CONVERSATIONAL MODE", 1)[1]
    assert "prose" in section.lower()
    assert "summary" in section.lower()


def test_intake_prompt_passes_existing_structural_invariants():
    """Q2.4 is additive — the existing length / non-empty invariants
    locked in `test_graph_state.py` still hold."""
    assert isinstance(INTAKE_PROMPT, str)
    assert len(INTAKE_PROMPT) > 100
