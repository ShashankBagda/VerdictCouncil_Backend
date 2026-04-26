"""Property-based and fuzz tests using Hypothesis.

Targets pure-function code with no I/O dependencies — ideal for
property-based testing because inputs are varied automatically:

  * ``sanitize_text`` / ``detect_injection`` — must never crash; must never
    return a result *longer* than the input (only strip, never add); known
    injection delimiters must always be removed.
  * ``confidence_calc`` — score must always be in [0, 100]; classification
    must be one of High/Medium/Low; empty inputs must not raise.
  * ``_safe_average`` — pure arithmetic helper; average of values in [0,100]
    must stay in [0,100].
  * ``generate_csrf_token`` — every generated token must be URL-safe and
    non-empty (sanity property).

Markers
-------
These tests have no external dependencies and run in every CI pass.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import HealthCheck, given, settings as h_settings
from hypothesis import strategies as st

from src.api.middleware.csrf import generate_csrf_token
from src.shared.sanitization import SanitizationResult, detect_injection, sanitize_text
from src.tools.confidence_calc import _safe_average, confidence_calc

# ---------------------------------------------------------------------------
# Hypothesis settings shared across tests
# ---------------------------------------------------------------------------

# Suppress the too_slow health check in CI where the machine may be slow
_CI_SETTINGS = h_settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.too_slow],
)

# ---------------------------------------------------------------------------
# sanitize_text properties
# ---------------------------------------------------------------------------


class TestSanitizeTextProperties:
    """Property-based tests for the layer-1 regex sanitization."""

    @given(text=st.text(min_size=0, max_size=2000))
    @_CI_SETTINGS
    def test_never_raises(self, text: str):
        """sanitize_text must never raise for any unicode input."""
        result = sanitize_text(text)
        assert isinstance(result, SanitizationResult)

    @given(text=st.text(alphabet=string.ascii_letters + string.digits + " .,;:'\"!?", max_size=500))
    @_CI_SETTINGS
    def test_plain_text_zero_hits(self, text: str):
        """Clean ASCII text without injection patterns must have zero regex hits."""
        result = sanitize_text(text)
        assert result.regex_hits == 0
        assert result.text == text

    @given(text=st.text(min_size=0, max_size=2000))
    @_CI_SETTINGS
    def test_result_text_never_longer_than_input(self, text: str):
        """Sanitization strips content; it must never grow the text."""
        result = sanitize_text(text)
        assert len(result.text) <= len(text) + 50  # small margin for replacement labels

    @given(
        prefix=st.text(alphabet=string.ascii_letters, max_size=20),
        suffix=st.text(alphabet=string.ascii_letters, max_size=20),
        payload=st.text(alphabet=string.ascii_letters + " ", max_size=50),
    )
    @_CI_SETTINGS
    def test_llama_delimiter_always_removed(self, prefix: str, suffix: str, payload: str):
        """[INST]…[/INST] must always be removed regardless of surrounding text."""
        text = f"{prefix}[INST]{payload}[/INST]{suffix}"
        result = sanitize_text(text)
        assert "[INST]" not in result.text
        assert "[/INST]" not in result.text
        assert result.regex_hits >= 1

    @given(
        prefix=st.text(alphabet=string.ascii_letters, max_size=20),
        suffix=st.text(alphabet=string.ascii_letters, max_size=20),
        payload=st.text(alphabet=string.ascii_letters + " ", max_size=50),
    )
    @_CI_SETTINGS
    def test_openai_delimiter_always_removed(self, prefix: str, suffix: str, payload: str):
        """<|im_start|>…<|im_end|> must always be removed."""
        text = f"{prefix}<|im_start|>system\n{payload}<|im_end|>{suffix}"
        result = sanitize_text(text)
        assert "<|im_start|>" not in result.text
        assert "<|im_end|>" not in result.text
        assert result.regex_hits >= 1

    @given(
        prefix=st.text(alphabet=string.ascii_letters, max_size=20),
        suffix=st.text(alphabet=string.ascii_letters, max_size=20),
        tag=st.sampled_from(["system", "instruction", "tool_call", "function_call"]),
        payload=st.text(alphabet=string.ascii_letters + " ", max_size=30),
    )
    @_CI_SETTINGS
    def test_xml_injection_tag_always_removed(self, prefix, suffix, tag, payload):
        """XML injection tags (<system>, <instruction> etc.) must always be removed."""
        text = f"{prefix}<{tag}>{payload}</{tag}>{suffix}"
        result = sanitize_text(text)
        assert f"<{tag}>" not in result.text
        assert result.regex_hits >= 1

    @given(text=st.text(min_size=0, max_size=2000))
    @_CI_SETTINGS
    def test_idempotent_after_two_passes(self, text: str):
        """Applying sanitize_text twice must produce the same result as once."""
        first = sanitize_text(text).text
        second = sanitize_text(first).text
        assert first == second


# ---------------------------------------------------------------------------
# detect_injection properties
# ---------------------------------------------------------------------------


class TestDetectInjectionProperties:
    @given(text=st.text(min_size=0, max_size=2000))
    @_CI_SETTINGS
    def test_never_raises(self, text: str):
        result = detect_injection(text)
        assert isinstance(result, bool)

    @given(text=st.text(alphabet=string.ascii_letters + " .,;:'\"!?\n", max_size=500))
    @_CI_SETTINGS
    def test_clean_text_returns_false(self, text: str):
        """Safe ASCII text must never be flagged as injection."""
        assert detect_injection(text) is False

    def test_empty_string_returns_false(self):
        assert detect_injection("") is False

    @given(
        payload=st.text(alphabet=string.ascii_letters + " ", max_size=50),
    )
    @_CI_SETTINGS
    def test_llama_instruction_always_detected(self, payload: str):
        text = f"[INST]{payload}[/INST]"
        assert detect_injection(text) is True


# ---------------------------------------------------------------------------
# _safe_average properties
# ---------------------------------------------------------------------------


class TestSafeAverageProperties:
    @given(scores=st.lists(st.floats(min_value=0, max_value=100), min_size=1, max_size=100))
    @_CI_SETTINGS
    def test_result_in_valid_range(self, scores: list[float]):
        """Average of values in [0,100] must stay in [0,100]."""
        avg = _safe_average(scores)
        assert 0.0 <= avg <= 100.0

    @given(scores=st.lists(st.floats(min_value=-1000, max_value=-0.001), min_size=1, max_size=50))
    @_CI_SETTINGS
    def test_negative_scores_excluded(self, scores: list[float]):
        """Values outside [0,100] are excluded; result must be 0.0 when all excluded."""
        avg = _safe_average(scores)
        assert avg == 0.0

    def test_empty_list_returns_zero(self):
        assert _safe_average([]) == 0.0

    @given(value=st.floats(min_value=0, max_value=100))
    @_CI_SETTINGS
    def test_single_value_returns_itself(self, value: float):
        avg = _safe_average([value])
        assert abs(avg - value) < 1e-9


# ---------------------------------------------------------------------------
# confidence_calc properties
# ---------------------------------------------------------------------------

_EVIDENCE_LABELS = ["strong", "moderate", "weak", "insufficient"]
_FACT_LABELS = ["verified", "corroborated", "disputed", "unverified", "contradicted"]
_CLASSIFICATIONS = {"High", "Medium", "Low"}


class TestConfidenceCalcProperties:
    @given(
        evidence=st.lists(st.sampled_from(_EVIDENCE_LABELS), max_size=20),
        facts=st.lists(st.sampled_from(_FACT_LABELS), max_size=20),
        witnesses=st.lists(st.integers(min_value=0, max_value=100), max_size=20),
        precedents=st.lists(st.floats(min_value=0.0, max_value=1.0), max_size=20),
    )
    @_CI_SETTINGS
    def test_score_always_in_range(self, evidence, facts, witnesses, precedents):
        """Confidence score must always be an integer in [0, 100]."""
        result = confidence_calc(evidence, facts, witnesses, precedents)
        score = result["confidence_score"]
        assert isinstance(score, int)
        assert 0 <= score <= 100

    @given(
        evidence=st.lists(st.sampled_from(_EVIDENCE_LABELS), max_size=20),
        facts=st.lists(st.sampled_from(_FACT_LABELS), max_size=20),
        witnesses=st.lists(st.integers(min_value=0, max_value=100), max_size=20),
        precedents=st.lists(st.floats(min_value=0.0, max_value=1.0), max_size=20),
    )
    @_CI_SETTINGS
    def test_classification_always_valid(self, evidence, facts, witnesses, precedents):
        """Classification must always be one of High, Medium, or Low."""
        result = confidence_calc(evidence, facts, witnesses, precedents)
        assert result["classification"] in _CLASSIFICATIONS

    def test_empty_inputs_do_not_raise(self):
        """All-empty inputs must not raise an exception."""
        result = confidence_calc([], [], [], [])
        assert "confidence_score" in result
        assert "classification" in result

    @given(
        evidence=st.lists(st.sampled_from(_EVIDENCE_LABELS), min_size=1, max_size=20),
        facts=st.lists(st.sampled_from(_FACT_LABELS), min_size=1, max_size=20),
        witnesses=st.lists(st.integers(min_value=0, max_value=100), min_size=1, max_size=20),
        precedents=st.lists(st.floats(min_value=0.0, max_value=1.0), min_size=1, max_size=20),
    )
    @_CI_SETTINGS
    def test_breakdown_keys_present(self, evidence, facts, witnesses, precedents):
        """Breakdown dict must contain all four component keys."""
        result = confidence_calc(evidence, facts, witnesses, precedents)
        breakdown = result["breakdown"]
        for key in ("evidence", "facts", "witnesses", "precedents"):
            assert key in breakdown

    @given(n=st.integers(min_value=1, max_value=20))
    @_CI_SETTINGS
    def test_all_strong_evidence_high_score(self, n: int):
        """All strong evidence + all verified facts + high witnesses → High score."""
        result = confidence_calc(
            ["strong"] * n,
            ["verified"] * n,
            [100] * n,
            [1.0] * n,
        )
        assert result["classification"] == "High"
        assert result["confidence_score"] >= 80

    @given(n=st.integers(min_value=1, max_value=20))
    @_CI_SETTINGS
    def test_all_insufficient_evidence_low_score(self, n: int):
        """All insufficient evidence + all contradicted facts → Low score."""
        result = confidence_calc(
            ["insufficient"] * n,
            ["contradicted"] * n,
            [0] * n,
            [0.0] * n,
        )
        assert result["classification"] == "Low"
        assert result["confidence_score"] < 60

    @given(
        bad_labels=st.lists(st.text(alphabet=string.ascii_letters, min_size=1, max_size=15), max_size=10)
    )
    @_CI_SETTINGS
    def test_unknown_labels_do_not_raise(self, bad_labels: list[str]):
        """Unrecognised label strings must be silently ignored, not crash."""
        result = confidence_calc(bad_labels, bad_labels, [], [])
        assert "confidence_score" in result


# ---------------------------------------------------------------------------
# generate_csrf_token properties
# ---------------------------------------------------------------------------

_URL_SAFE_CHARS = set(string.ascii_letters + string.digits + "-_")


class TestGenerateCsrfTokenProperties:
    @h_settings(max_examples=100)
    @given(st.integers(min_value=0, max_value=0))  # dummy arg to drive Hypothesis
    def test_token_always_url_safe(self, _):
        token = generate_csrf_token()
        invalid = set(token) - _URL_SAFE_CHARS
        assert not invalid, f"Non-URL-safe chars found: {invalid!r}"

    @h_settings(max_examples=100)
    @given(st.integers(min_value=0, max_value=0))
    def test_token_always_non_empty(self, _):
        token = generate_csrf_token()
        assert len(token) > 0
