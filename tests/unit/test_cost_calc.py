"""Sprint 4 4.C4.3 — calc_cost contract."""

from __future__ import annotations

from decimal import Decimal

from src.shared.cost import calc_cost, get_prices


def test_known_model_with_full_usage_returns_decimal() -> None:
    cost = calc_cost("gpt-5", {"prompt_tokens": 1000, "completion_tokens": 500})
    assert isinstance(cost, Decimal)
    # 1000/1000 * 0.030 + 500/1000 * 0.120 = 0.030 + 0.060 = 0.090
    assert cost == Decimal("0.090000")


def test_unknown_model_returns_none() -> None:
    assert calc_cost("gpt-99-vapor", {"prompt_tokens": 100}) is None


def test_missing_usage_returns_none() -> None:
    assert calc_cost("gpt-5", None) is None


def test_zero_usage_returns_none() -> None:
    """Zero tokens means we have no real call to cost — not a $0.00 row."""
    assert calc_cost("gpt-5", {"prompt_tokens": 0, "completion_tokens": 0}) is None


def test_alternate_token_keys_supported() -> None:
    """OpenAI v1 + Anthropic-style {input_tokens, output_tokens} both work."""
    cost = calc_cost("gpt-5-mini", {"input_tokens": 1000, "output_tokens": 1000})
    assert cost == Decimal("0.015000")


def test_no_model_id_returns_none() -> None:
    assert calc_cost(None, {"prompt_tokens": 100}) is None
    assert calc_cost("", {"prompt_tokens": 100}) is None


def test_embedding_model_uses_input_rate_only() -> None:
    cost = calc_cost("text-embedding-3-large", {"prompt_tokens": 10000})
    assert cost == Decimal("0.001300")


def test_cost_quantized_to_six_decimals() -> None:
    """Match audit_logs.cost_usd NUMERIC(10, 6)."""
    cost = calc_cost("gpt-5", {"prompt_tokens": 1, "completion_tokens": 1})
    assert cost is not None
    # Total < $0.000001 → either rounds to zero or stays non-None;
    # the contract is: 6 decimals, never more.
    sign, digits, exp = cost.as_tuple()
    assert exp == -6, f"cost_usd must quantize to NUMERIC(10, 6); got exp={exp}"


def test_get_prices_returns_defensive_copy() -> None:
    """Mutating the returned dict must not leak into module-level state."""
    snapshot = get_prices()
    snapshot["fake-model"] = {"input": Decimal("1.0")}

    assert "fake-model" not in get_prices(), "get_prices() must return a defensive copy"


def test_known_models_match_yaml_table() -> None:
    """Sanity: prompt-table tier names appear in the price registry."""
    prices = get_prices()
    for name in ("gpt-5.4", "gpt-5", "gpt-5-mini", "gpt-5.4-nano"):
        assert name in prices, f"Expected price entry for {name!r}"
        assert "input" in prices[name]
        assert "output" in prices[name]
