"""Sprint 4 4.C4.3 — per-model cost calculation.

``calc_cost(model_id, usage)`` reads ``src/config/model_prices.yaml`` once
at import time and returns the dollar cost of a single LLM call as
``Decimal`` (the exact type expected by ``audit_logs.cost_usd``).

Unknown models return ``None`` rather than raising — this keeps the
audit-write path tolerant of new tiers landing before the price table
catches up. Callers must treat ``None`` as "cost unknown" and not as
"$0.00" when rolling up.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

#: Price table location relative to repo root.
_PRICES_PATH = Path(__file__).resolve().parent.parent / "config" / "model_prices.yaml"


def _load_prices() -> dict[str, dict[str, Decimal]]:
    if not _PRICES_PATH.exists():
        logger.warning("model_prices.yaml not found at %s", _PRICES_PATH)
        return {}
    raw = yaml.safe_load(_PRICES_PATH.read_text()) or {}
    out: dict[str, dict[str, Decimal]] = {}
    for model, rates in raw.items():
        if not isinstance(rates, dict):
            continue
        coerced: dict[str, Decimal] = {}
        for kind in ("input", "output"):
            value = rates.get(kind)
            if value is None:
                continue
            coerced[kind] = Decimal(str(value))
        if coerced:
            out[str(model)] = coerced
    return out


_PRICES: dict[str, dict[str, Decimal]] = _load_prices()


def get_prices() -> dict[str, dict[str, Decimal]]:
    """Return a defensive copy of the loaded price table (for tests / reload)."""
    return {model: dict(rates) for model, rates in _PRICES.items()}


def calc_cost(model_id: str | None, usage: dict[str, Any] | None) -> Decimal | None:
    """Return total dollar cost of one LLM call, or ``None`` if unknown.

    ``usage`` follows the OpenAI-compatible shape:
        {"prompt_tokens": int, "completion_tokens": int, ...}

    Unknown model_id, missing token counts, and total-zero usage all
    return ``None`` — there is no meaningful "$0.00 cost we know about".
    """
    if not model_id:
        return None
    rates = _PRICES.get(model_id)
    if rates is None:
        return None
    if usage is None:
        return None

    prompt = _coerce_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _coerce_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    if prompt == 0 and completion == 0:
        return None

    cost = Decimal("0")
    input_rate = rates.get("input")
    output_rate = rates.get("output")

    if input_rate is not None and prompt:
        cost += (Decimal(prompt) / Decimal(1000)) * input_rate
    if output_rate is not None and completion:
        cost += (Decimal(completion) / Decimal(1000)) * output_rate

    # Quantize to the audit_logs.cost_usd precision (NUMERIC(10, 6)).
    return cost.quantize(Decimal("0.000001"))


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
