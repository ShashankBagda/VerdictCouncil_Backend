"""Sprint 1 1.C3a.2 — push the 7 phase-aligned prompts to LangSmith.

Reads every `prompts/<name>.md` file, wraps the content in a LangChain
`ChatPromptTemplate`, and pushes it to LangSmith under the identifier
`verdict-council/<name>`. The push is idempotent: if the prompt
already exists with the same content, no new commit is created.

Run:

    LANGSMITH_API_KEY=ls_pt_... python scripts/migrate_prompts_to_langsmith.py

A `--dry-run` flag previews what would change without making API calls.

Output identifier matches the convention `_resolve_prompt(name)` will
use once 1.C3a.3 rewires `prompts.py` as a registry lookup.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"

# Load credentials from the backend's .env so the script can be run
# straight from the venv without `export LANGSMITH_API_KEY=...`. Existing
# environment values win — this matches how `Settings(BaseSettings)`
# behaves elsewhere in the codebase.
load_dotenv(REPO_ROOT / ".env", override=False)

# Maps `prompts/<file>.md` → LangSmith prompt identifier.
#
# LangSmith treats the part before `/` as the OWNER/TENANT, not as a
# logical namespace. With a personal-scoped API key (Sprint 0 decision)
# there is no `verdict-council` tenant to push under, so prompts live
# in the personal namespace under their bare name. Logical grouping is
# preserved via the `verdictcouncil` tag below.
PROMPT_FILES: dict[str, str] = {
    "intake.md": "vc-intake",
    "research-evidence.md": "vc-research-evidence",
    "research-facts.md": "vc-research-facts",
    "research-witnesses.md": "vc-research-witnesses",
    "research-law.md": "vc-research-law",
    "synthesis.md": "vc-synthesis",
    "audit.md": "vc-audit",
}

# Tags applied to every commit so traces and registry browsers can
# filter to the new-topology Sprint 1 prompts.
COMMIT_TAGS = ["sprint-1", "topology-6phase", "verdictcouncil"]


def _load_local(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Local prompt missing: {path}")
    return path.read_text()


def _build_template(content: str) -> ChatPromptTemplate:
    """Wrap raw markdown as a single system-message ChatPromptTemplate."""
    return ChatPromptTemplate.from_messages([("system", content)])


def _remote_content(client: Client, identifier: str) -> str | None:
    """Return the latest LangSmith commit's system-message content, or None.

    A `LangSmithNotFoundError` (or any 404) means the prompt does not yet
    exist; treat that as "no remote content" rather than an error so the
    first-run path is the same as steady-state.
    """
    try:
        template: ChatPromptTemplate = client.pull_prompt(identifier)
    except Exception as exc:  # noqa: BLE001 — broad on purpose: SDK exceptions vary
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            return None
        raise

    # Extract the system-message content from the pulled template. The
    # template may wrap it in a list of messages or carry it directly.
    if hasattr(template, "messages") and template.messages:
        first = template.messages[0]
        if hasattr(first, "prompt") and hasattr(first.prompt, "template"):
            return first.prompt.template
    return None


def _push_one(
    client: Client,
    identifier: str,
    filename: str,
    *,
    dry_run: bool,
) -> str:
    """Push a single prompt; return one of 'created', 'updated', 'unchanged'."""
    local = _load_local(filename)
    template = _build_template(local)
    remote = _remote_content(client, identifier)

    if remote is None:
        action = "created"
    elif remote.strip() == local.strip():
        return "unchanged"
    else:
        action = "updated"

    if not dry_run:
        client.push_prompt(
            identifier,
            object=template,
            description=f"Sprint 1 1.C3a.2 — phase prompt for {identifier}",
            tags=COMMIT_TAGS,
            commit_tags=COMMIT_TAGS,
            commit_description=f"Push from {filename}",
        )
    return action


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without calling LangSmith.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("LANGSMITH_API_KEY")
    if not api_key:
        print(
            "ERROR: LANGSMITH_API_KEY is not set. Add it to .env or export it before running.",
            file=sys.stderr,
        )
        return 1

    client = Client(api_key=api_key)

    for filename, identifier in PROMPT_FILES.items():
        action = _push_one(client, identifier, filename, dry_run=args.dry_run)
        prefix = "[dry-run]" if args.dry_run else "         "
        print(f"{prefix} {action:<10} {identifier}  ({filename})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
