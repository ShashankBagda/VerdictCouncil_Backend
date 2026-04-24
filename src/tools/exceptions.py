"""Tool failure exception hierarchy.

Separates failures that are safe to surface as LLM context (DegradableToolError)
from failures that must halt the gate entirely (CriticalToolFailure).
"""


class CriticalToolFailure(Exception):
    """Base for tool failures that MUST halt the gate, not degrade silently."""


class DomainGuidanceUnavailable(CriticalToolFailure):
    """Domain vector store is not provisioned or domain has been retired."""


class RetiredDomainError(CriticalToolFailure):
    """Case's linked domain was retired after the case was filed."""


class DegradableToolError(Exception):
    """Base for tool failures that are SAFE to surface as {"error": ...} to the LLM.

    Known-safe exceptions subclass this. Anything not in this hierarchy
    causes the gate to fail rather than degrade silently.
    """
