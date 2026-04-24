"""Unit tests for cache key isolation in src.tools.search_precedents.

Verifies that different vector_store_id values produce different cache keys,
preventing cross-domain cache collisions (H6 requirement).
"""

import inspect

from src.tools.search_precedents import (
    _cache_key,
    search_precedents,
    search_precedents_with_meta,
)

# ------------------------------------------------------------------ #
# Cache key isolation
# ------------------------------------------------------------------ #


def test_different_vector_store_ids_produce_different_keys():
    """Different vector_store_id values must produce different cache keys."""
    key_a = _cache_key("test query", "small_claims", "vs_a", 5)
    key_b = _cache_key("test query", "small_claims", "vs_b", 5)
    assert key_a != key_b


def test_none_vector_store_id_differs_from_explicit_id():
    """A None store id and an explicit store id must produce different cache keys."""
    key_none = _cache_key("test query", "small_claims", None, 5)
    key_vs = _cache_key("test query", "small_claims", "vs_a", 5)
    assert key_none != key_vs


def test_same_inputs_produce_same_key():
    """Cache key generation is deterministic — identical inputs yield identical keys."""
    key1 = _cache_key("breach of contract", "small_claims", "vs_xyz", 10)
    key2 = _cache_key("breach of contract", "small_claims", "vs_xyz", 10)
    assert key1 == key2


def test_different_queries_produce_different_keys():
    """Different queries with the same store id still produce different keys."""
    key_a = _cache_key("breach of contract", "small_claims", "vs_a", 5)
    key_b = _cache_key("negligence claim", "small_claims", "vs_a", 5)
    assert key_a != key_b


def test_different_domains_produce_different_keys():
    """Different domains with the same query and store id produce different keys."""
    key_a = _cache_key("test query", "small_claims", "vs_a", 5)
    key_b = _cache_key("test query", "traffic_violation", "vs_a", 5)
    assert key_a != key_b


def test_different_max_results_produce_different_keys():
    """Different max_results values produce different cache keys."""
    key_5 = _cache_key("test query", "small_claims", "vs_a", 5)
    key_10 = _cache_key("test query", "small_claims", "vs_a", 10)
    assert key_5 != key_10


def test_cache_key_returns_string_with_prefix():
    """Cache key has the expected vc:precedents: prefix."""
    key = _cache_key("test query", "small_claims", "vs_a", 5)
    assert isinstance(key, str)
    assert key.startswith("vc:precedents:")


# ------------------------------------------------------------------ #
# Public API accepts vector_store_id kwarg
# ------------------------------------------------------------------ #


def test_search_precedents_accepts_vector_store_id_kwarg():
    """search_precedents signature includes vector_store_id parameter."""
    sig = inspect.signature(search_precedents)
    assert "vector_store_id" in sig.parameters


def test_search_precedents_with_meta_accepts_vector_store_id_kwarg():
    """search_precedents_with_meta signature includes vector_store_id parameter."""
    sig = inspect.signature(search_precedents_with_meta)
    assert "vector_store_id" in sig.parameters


def test_search_precedents_vector_store_id_defaults_to_none():
    """search_precedents defaults vector_store_id to None (backward-compatible)."""
    sig = inspect.signature(search_precedents)
    param = sig.parameters["vector_store_id"]
    assert param.default is None


def test_search_precedents_with_meta_vector_store_id_defaults_to_none():
    """search_precedents_with_meta defaults vector_store_id to None (backward-compatible)."""
    sig = inspect.signature(search_precedents_with_meta)
    param = sig.parameters["vector_store_id"]
    assert param.default is None
