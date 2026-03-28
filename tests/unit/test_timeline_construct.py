"""Unit tests for src.tools.timeline_construct (pure logic, no mocking)."""

from src.tools.timeline_construct import timeline_construct


# ------------------------------------------------------------------ #
# Facts with dates sorted chronologically
# ------------------------------------------------------------------ #
def test_dated_facts_sorted_chronologically():
    facts = [
        {"fact_id": "f3", "date": "2026-03-10", "event": "Hearing scheduled"},
        {"fact_id": "f1", "date": "2026-01-05", "event": "Incident occurred"},
        {"fact_id": "f2", "date": "2026-02-20", "event": "Claim filed"},
    ]

    timeline = timeline_construct(facts)

    assert len(timeline) == 3
    assert timeline[0]["fact_id"] == "f1"
    assert timeline[1]["fact_id"] == "f2"
    assert timeline[2]["fact_id"] == "f3"
    # All timestamps should be ISO-format strings
    for entry in timeline:
        assert entry["timestamp"] is not None


def test_multiple_date_formats():
    facts = [
        {"fact_id": "a", "date": "15/06/2026", "event": "DD/MM/YYYY format"},
        {"fact_id": "b", "date": "2026-01-01", "event": "ISO format"},
        {"fact_id": "c", "date": "March 10, 2026", "event": "Month DD, YYYY"},
    ]

    timeline = timeline_construct(facts)

    assert timeline[0]["fact_id"] == "b"  # Jan 1
    assert timeline[1]["fact_id"] == "c"  # Mar 10
    assert timeline[2]["fact_id"] == "a"  # Jun 15


# ------------------------------------------------------------------ #
# Facts with missing dates placed at end
# ------------------------------------------------------------------ #
def test_missing_dates_placed_at_end():
    facts = [
        {"fact_id": "f1", "date": "", "event": "Undated event"},
        {"fact_id": "f2", "date": "2026-05-01", "event": "Dated event"},
        {"fact_id": "f3", "event": "No date key at all"},
    ]

    timeline = timeline_construct(facts)

    assert len(timeline) == 3
    # Dated entry first
    assert timeline[0]["fact_id"] == "f2"
    assert timeline[0]["timestamp"] is not None
    # Undated entries at end
    assert timeline[1]["fact_id"] == "f1"
    assert timeline[1]["timestamp"] is None
    assert "_note" in timeline[1]
    assert timeline[2]["fact_id"] == "f3"
    assert timeline[2]["timestamp"] is None


# ------------------------------------------------------------------ #
# Empty facts list returns empty timeline
# ------------------------------------------------------------------ #
def test_empty_facts_returns_empty_timeline():
    assert timeline_construct([]) == []


# ------------------------------------------------------------------ #
# Alternate key names (description / timestamp)
# ------------------------------------------------------------------ #
def test_alternate_keys():
    """timeline_construct should accept 'description' and 'timestamp' keys."""
    facts = [
        {
            "fact_id": "x",
            "timestamp": "2026-04-01",
            "description": "Something happened",
            "source_refs": ["doc-1"],
        }
    ]

    timeline = timeline_construct(facts)

    assert len(timeline) == 1
    assert timeline[0]["event"] == "Something happened"
    assert timeline[0]["source_refs"] == ["doc-1"]
    assert timeline[0]["timestamp"] is not None
