"""Timeline construction tool for VerdictCouncil fact reconstruction.

Pure-logic tool that sorts extracted facts into chronological order.
No LLM calls required.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from src.tools.types import TimelineFact

logger = logging.getLogger(__name__)

# Supported date formats for parsing, tried in order
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
]


def _parse_date(date_str: str) -> datetime | None:
    """Attempt to parse a date string using known formats.

    Returns None if no format matches.
    """
    if not date_str or not isinstance(date_str, str):
        return None

    cleaned = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def timeline_construct(
    events: Annotated[
        list[TimelineFact],
        "List of events to order. Each event: {date, description, source_ref, parties, location}",
    ],
) -> list[dict]:
    """Build a chronological timeline from extracted events.

    Takes events with date/time information, sorts them chronologically,
    and returns an ordered timeline. Events without parseable dates are
    placed at the end with a note.

    Args:
        events: List of event dictionaries. Each should contain:
            - fact_id (str): Unique identifier for the event.
            - date (str): Date/time string in any recognizable format.
            - event (str): Description of what happened.
            - source_refs (list[str]): References to source documents.

    Returns:
        List of timeline entries, each containing:
            - timestamp (str | None): ISO 8601 formatted date, or None if
              the date could not be parsed.
            - event (str): Description of the event.
            - fact_id (str): Original fact identifier.
            - source_refs (list[str]): Source document references.
    """
    if not events:
        return []

    dated_entries: list[tuple[datetime, dict]] = []
    undated_entries: list[dict] = []

    for fact in events:
        fact_id = fact.get("fact_id", "")
        event = fact.get("event") or fact.get("description", "")
        source_refs = fact.get("source_refs", [])
        date_str = fact.get("date") or fact.get("timestamp", "")

        parsed = _parse_date(date_str)

        entry = {
            "timestamp": parsed.isoformat() if parsed else None,
            "event": event,
            "fact_id": fact_id,
            "source_refs": source_refs,
        }

        if parsed is not None:
            dated_entries.append((parsed, entry))
        else:
            entry["_note"] = (
                f"Date could not be parsed from: '{date_str}'. Placed at end of timeline."
                if date_str
                else "No date provided. Placed at end of timeline."
            )
            undated_entries.append(entry)

    # Sort dated entries chronologically
    dated_entries.sort(key=lambda pair: pair[0])

    # Build final ordered list: dated first, then undated
    timeline = [entry for _, entry in dated_entries]
    timeline.extend(undated_entries)

    logger.info(
        "Timeline constructed: %d dated events, %d undated events",
        len(dated_entries),
        len(undated_entries),
    )

    return timeline
