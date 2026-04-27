"""Pull the 7 phase-aligned prompts from LangSmith into `prompts/*.md`.

Mirror of `scripts/migrate_prompts_to_langsmith.py` in the opposite direction:
fetches the latest LangSmith commit for each `vc-<phase>` prompt and writes
the system-message content to the matching `prompts/<file>.md` so the local
fallback used by `prompt_registry.py` stays in sync with the canonical
LangSmith source.

Run:

    LANGSMITH_API_KEY=ls_pt_... python scripts/pull_prompts_from_langsmith.py

A `--dry-run` flag previews diffs without touching the filesystem.
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"

load_dotenv(REPO_ROOT / ".env", override=False)

PROMPT_FILES: dict[str, str] = {
    "intake.md": "vc-intake",
    "research-evidence.md": "vc-research-evidence",
    "research-facts.md": "vc-research-facts",
    "research-witnesses.md": "vc-research-witnesses",
    "research-law.md": "vc-research-law",
    "synthesis.md": "vc-synthesis",
    "audit.md": "vc-audit",
}


def _remote_content(client: Client, identifier: str) -> str | None:
    try:
        template: ChatPromptTemplate = client.pull_prompt(identifier)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            return None
        raise

    if hasattr(template, "messages") and template.messages:
        first = template.messages[0]
        if hasattr(first, "prompt") and hasattr(first.prompt, "template"):
            return first.prompt.template
    return None


def _pull_one(client: Client, identifier: str, filename: str, *, dry_run: bool) -> str:
    remote = _remote_content(client, identifier)
    if remote is None:
        return "missing"

    path = PROMPTS_DIR / filename
    local = path.read_text() if path.exists() else ""

    if local.strip() == remote.strip():
        return "unchanged"

    if dry_run:
        diff = difflib.unified_diff(
            local.splitlines(keepends=True),
            remote.splitlines(keepends=True),
            fromfile=f"local/{filename}",
            tofile=f"langsmith/{identifier}",
            n=2,
        )
        sys.stdout.writelines(diff)
        return "would-update"

    path.write_text(remote)
    return "updated" if local else "created"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("LANGSMITH_API_KEY")
    if not api_key:
        print("ERROR: LANGSMITH_API_KEY is not set.", file=sys.stderr)
        return 1

    client = Client(api_key=api_key)

    for filename, identifier in PROMPT_FILES.items():
        action = _pull_one(client, identifier, filename, dry_run=args.dry_run)
        prefix = "[dry-run]" if args.dry_run else "         "
        print(f"{prefix} {action:<13} {identifier}  ({filename})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
