"""What-If fork primitives (Sprint 4 4.A5).

LangGraph-native fork: the judge's modification is applied to the
original thread's terminal CaseState and the result seeded into a
fresh thread (keyed by case_id + judge_id + a fork uuid) via
``graph.aupdate_state(..., {"case": Overwrite(modified)}, as_node="research_join")``.
Stability scoring (4.A5.2) fans out N forks in parallel and aggregates
their hold-rate; see :mod:`src.services.whatif.stability`.
"""

from src.services.whatif.fork import (
    WhatIfModification,
    apply_modifications,
    create_whatif_fork,
    drive_whatif_to_terminal,
)
from src.services.whatif.stability import (
    classify,
    compute_stability_score,
    identify_perturbations,
)

__all__ = [
    "WhatIfModification",
    "apply_modifications",
    "classify",
    "compute_stability_score",
    "create_whatif_fork",
    "drive_whatif_to_terminal",
    "identify_perturbations",
]
