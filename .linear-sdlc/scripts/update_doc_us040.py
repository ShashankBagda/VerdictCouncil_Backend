#!/usr/bin/env python3
"""Append a §1.10 section + US-040 row to the Linear 'User Stories' document."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LINEAR_URL = "https://api.linear.app/graphql"
DOC_ID = "b820ae2a-5734-4aae-b2f2-7e1781e429f1"


def load_token():
    tok = os.environ.get("LINEAR_API_KEY")
    if tok:
        return tok
    env_file = Path.home() / ".linear-sdlc" / "env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("export LINEAR_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
            if line.startswith("LINEAR_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("LINEAR_API_KEY not found")


TOKEN = load_token()


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}})
    for attempt in range(3):
        result = subprocess.run(
            ["curl", "-sS", "-X", "POST", LINEAR_URL,
             "-H", f"Authorization: {TOKEN}",
             "-H", "Content-Type: application/json",
             "--data-binary", body],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"curl failed: {result.stderr}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"non-JSON response: {result.stdout[:200]}") from e
        if "errors" in data:
            raise RuntimeError(f"GraphQL: {data['errors']}")
        return data["data"]


GET = """
query Doc($id: String!) {
  document(id: $id) { id title content }
}
"""

UPDATE = """
mutation Update($id: String!, $input: DocumentUpdateInput!) {
  documentUpdate(id: $id, input: $input) {
    success
    document { id title }
  }
}
"""


APPEND_SECTION = """

## §1.10 Senior Judge Operations

The Senior Judge persona owns a unified inbox for everything routed to them
for senior-judge action. US-038 and US-039 are intentionally reserved for
follow-up senior-judge stories (bulk-action, analytics dashboard).

| Story | Title | Persona | Linear |
|---|---|---|---|
| US-040 | Senior Judge — Review Referred Cases | Senior Judge | [VER-133](https://linear.app/verdictcouncil/issue/VER-133) (FE [VER-135](https://linear.app/verdictcouncil/issue/VER-135) · BE [VER-134](https://linear.app/verdictcouncil/issue/VER-134)) |

**US-040 — Senior Judge — Review Referred Cases**

Aggregates three referral sources into one inbox: escalations from US-024,
reopen requests from US-037, and amendments-of-record from US-036.

Per-entry actions: **Approve**, **Reject** (with reason), **Reassign** to
another senior judge, **Request more info** (returns to originating judge).

A **two-person rule** prevents a senior judge from approving their own
referral. Gated by the `senior_judge` role assigned via US-033. All actions
recorded in the audit trail (US-026). Notifications are **in-app only** for
the MVP.
"""


def main():
    doc = gql(GET, {"id": DOC_ID})["document"]
    if "§1.10" in doc.get("content", ""):
        print("§1.10 already present in document — nothing to do")
        return
    new_content = doc["content"].rstrip() + "\n" + APPEND_SECTION
    res = gql(UPDATE, {"id": DOC_ID, "input": {"content": new_content}})
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
