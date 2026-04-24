"""Unit tests for src.tools.exceptions — exception hierarchy."""

from src.tools.exceptions import (
    CriticalToolFailure,
    DegradableToolError,
    DomainGuidanceUnavailable,
    RetiredDomainError,
)


def test_domain_guidance_unavailable_is_critical_tool_failure():
    """DomainGuidanceUnavailable must be a CriticalToolFailure (halts the gate)."""
    assert issubclass(DomainGuidanceUnavailable, CriticalToolFailure)


def test_retired_domain_error_is_critical_tool_failure():
    """RetiredDomainError must be a CriticalToolFailure (halts the gate)."""
    assert issubclass(RetiredDomainError, CriticalToolFailure)


def test_domain_guidance_unavailable_is_exception():
    """DomainGuidanceUnavailable inherits from Exception via CriticalToolFailure."""
    assert issubclass(DomainGuidanceUnavailable, Exception)


def test_retired_domain_error_is_exception():
    """RetiredDomainError inherits from Exception via CriticalToolFailure."""
    assert issubclass(RetiredDomainError, Exception)


def test_critical_tool_failure_is_not_degradable():
    """CriticalToolFailure and DegradableToolError are parallel hierarchies."""
    assert not issubclass(CriticalToolFailure, DegradableToolError)
    assert not issubclass(DegradableToolError, CriticalToolFailure)


def test_domain_guidance_unavailable_can_be_raised_with_message():
    """DomainGuidanceUnavailable can be instantiated with a message."""
    exc = DomainGuidanceUnavailable("domain 'traffic' not provisioned")
    assert "traffic" in str(exc)


def test_retired_domain_error_can_be_raised_with_message():
    """RetiredDomainError can be instantiated with a message."""
    exc = RetiredDomainError("domain retired after case was filed")
    assert "retired" in str(exc)


def test_precedent_search_error_is_degradable_tool_error():
    """PrecedentSearchError must be a DegradableToolError (safe to surface to LLM)."""
    from src.tools.search_precedents import PrecedentSearchError

    assert issubclass(PrecedentSearchError, DegradableToolError)


def test_vector_store_error_is_degradable_tool_error():
    """VectorStoreError must be a DegradableToolError (safe to surface to LLM)."""
    from src.tools.vector_store_fallback import VectorStoreError

    assert issubclass(VectorStoreError, DegradableToolError)


def test_degradable_errors_are_not_critical():
    """Neither PrecedentSearchError nor VectorStoreError should halt the gate."""
    from src.tools.search_precedents import PrecedentSearchError
    from src.tools.vector_store_fallback import VectorStoreError

    assert not issubclass(PrecedentSearchError, CriticalToolFailure)
    assert not issubclass(VectorStoreError, CriticalToolFailure)
