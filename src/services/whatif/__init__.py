"""What-If fork primitives (Sprint 4 4.A5).

Replaces the legacy ``services/whatif_controller`` deep-clone pipeline
with a LangGraph-native fork: the judge's modification is applied to
the original thread's terminal CaseState and the result seeded into a
fresh thread (keyed by case_id + judge_id + a fork uuid) via
``graph.aupdate_state(..., {"case": Overwrite(modified)}, as_node="research_join")``.
"""

from src.services.whatif.fork import (
    WhatIfModification,
    apply_modifications,
    create_whatif_fork,
    drive_whatif_to_terminal,
)

__all__ = [
    "WhatIfModification",
    "apply_modifications",
    "create_whatif_fork",
    "drive_whatif_to_terminal",
]
