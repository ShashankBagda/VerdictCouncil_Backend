"""Canonical confidence enum used across phase output schemas.

Per Sprint 0.5 §5 D-3: replaces `confidence: str` (legacy fact-reconstruction
schema) and `confidence_score: int | None` (legacy hearing-analysis schema)
uniformly. Lives outside `pipeline/graph/schemas.py` so utility modules
(e.g. `src/utils/confidence_calc.py` per Sprint 0.5 §5 D-7) can import it
without creating an import cycle.
"""

from __future__ import annotations

from enum import Enum


class ConfidenceLevel(str, Enum):
    """Canonical low/med/high confidence scale."""

    LOW = "low"
    MED = "med"
    HIGH = "high"
