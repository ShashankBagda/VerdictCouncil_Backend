"""Sprint 3 3.D1.1 — sync golden cases to LangSmith.

Reads every JSON fixture under ``tests/eval/data/golden_cases/`` and
upserts it as an example in the LangSmith dataset
``verdict-council-golden``.

Idempotency: every example carries ``metadata.golden_id`` (mirrors the
fixture's ``metadata.id``). On re-run we list existing examples in the
dataset, build a set of seen ``golden_id``s, and skip those that are
already present. There's no destructive update path — bumping a
fixture's content with the same id is a no-op until the example is
manually deleted from the LangSmith UI (or ``--force`` is added to
this script). That's intentional: dataset history is the eval
baseline, and silent overwrites would corrupt experiment comparisons.

Run::

    LANGSMITH_API_KEY=ls_pt_... \\
        uv run python tests/eval/dataset_sync.py

A ``--dry-run`` flag previews what would change without API calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "eval" / "data" / "golden_cases"
DATASET_NAME = "verdict-council-golden"
DATASET_DESCRIPTION = (
    "Golden eval cases for VerdictCouncil — Sprint 0 0.11b. "
    "5 small_claims + 5 traffic_violation fixtures with expected intake "
    "+ research outputs. Synced via tests/eval/dataset_sync.py."
)

# Load .env so the script runs from the venv without an exported key.
load_dotenv(REPO_ROOT / ".env", override=False)


def load_fixtures(directory: Path = GOLDEN_DIR) -> list[dict[str, Any]]:
    """Read every ``*.json`` fixture under *directory*, sorted by filename."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Golden cases directory not found: {directory}")
    fixtures = []
    for path in sorted(directory.glob("*.json")):
        with path.open() as f:
            fixtures.append(json.load(f))
    return fixtures


def _placeholder_source_ids(fixture: dict[str, Any]) -> list[str]:
    """Return any ``placeholder-*`` source_ids in the fixture's expected outputs.

    Goldens are authored with placeholder file ids because real OpenAI
    vector-store ids are deployment-specific. This helper surfaces the
    gap so callers can warn loudly until the goldens are reconciled
    against a seeded vector store (Sprint 4 follow-up).
    """
    research = (fixture.get("expected") or {}).get("research") or {}
    return [
        sid
        for sid in (research.get("supporting_sources") or [])
        if isinstance(sid, str) and sid.startswith("placeholder-")
    ]


def fixture_to_example(fixture: dict[str, Any]) -> dict[str, Any]:
    """Project a fixture into a LangSmith ``ExampleCreate`` dict.

    LangSmith examples have free-form ``inputs`` / ``outputs`` / ``metadata``;
    we mirror the fixture's structure and stash the golden id in metadata
    so :func:`sync` can dedupe.
    """
    return {
        "inputs": fixture["inputs"],
        "outputs": fixture["expected"],
        "metadata": {
            "golden_id": fixture["metadata"]["id"],
            "domain": fixture["metadata"]["domain"],
            "author": fixture["metadata"]["author"],
            "date": fixture["metadata"]["date"],
        },
    }


def _ensure_dataset(client: Any, *, dry_run: bool) -> Any:
    """Return the dataset, creating it if it doesn't yet exist."""
    try:
        return client.read_dataset(dataset_name=DATASET_NAME)
    except Exception:  # noqa: BLE001 — LangSmith uses a custom exception hierarchy
        if dry_run:
            return None
        return client.create_dataset(dataset_name=DATASET_NAME, description=DATASET_DESCRIPTION)


def _existing_golden_ids(client: Any, dataset_id: Any) -> set[str]:
    """Build the set of already-synced golden_ids."""
    seen: set[str] = set()
    if dataset_id is None:
        return seen
    for example in client.list_examples(dataset_id=dataset_id):
        meta = getattr(example, "metadata", None) or {}
        gid = meta.get("golden_id")
        if gid:
            seen.add(gid)
    return seen


def sync(
    client: Any,
    fixtures: list[dict[str, Any]],
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Upsert *fixtures* into the LangSmith dataset.

    Returns a report dict with three keys: ``created``, ``skipped``,
    ``dataset_id``. ``skipped`` covers fixtures already present.
    """
    dataset = _ensure_dataset(client, dry_run=dry_run)
    dataset_id = getattr(dataset, "id", None) if dataset is not None else None
    existing = _existing_golden_ids(client, dataset_id)

    to_create: list[dict[str, Any]] = []
    skipped: list[str] = []
    created: list[str] = []

    for fixture in fixtures:
        gid = fixture["metadata"]["id"]
        if gid in existing:
            skipped.append(gid)
            continue
        to_create.append(fixture_to_example(fixture))
        created.append(gid)

    if to_create and not dry_run:
        if dataset_id is None:
            raise RuntimeError("dataset must exist before creating examples")
        client.create_examples(dataset_id=dataset_id, examples=to_create)

    return {
        "created": created,
        "skipped": skipped,
        "dataset_id": str(dataset_id) if dataset_id else "<dry-run>",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without calling LangSmith.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("LANGSMITH_API_KEY")
    if not api_key and not args.dry_run:
        print(
            "ERROR: LANGSMITH_API_KEY is not set. Add it to .env or export it before running.",
            file=sys.stderr,
        )
        return 2

    from langsmith import Client

    client = Client(api_key=api_key) if api_key else Client()

    fixtures = load_fixtures()
    if not fixtures:
        print(f"No fixtures found under {GOLDEN_DIR}", file=sys.stderr)
        return 1

    placeholder_count = sum(1 for fx in fixtures if _placeholder_source_ids(fx))
    if placeholder_count:
        print(
            f"WARN: {placeholder_count}/{len(fixtures)} fixtures still use "
            f"placeholder source_ids. citation_accuracy will floor at 0 in "
            f"--mode graph until they're reconciled against the live "
            f"OpenAI vector store.",
            file=sys.stderr,
        )

    report = sync(client, fixtures, dry_run=args.dry_run)
    print(f"Dataset: {DATASET_NAME} ({report['dataset_id']})")
    print(f"Created: {len(report['created'])} ({', '.join(report['created']) or '—'})")
    print(f"Skipped: {len(report['skipped'])} ({', '.join(report['skipped']) or '—'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
