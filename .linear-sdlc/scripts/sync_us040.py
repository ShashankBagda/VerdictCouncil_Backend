#!/usr/bin/env python3
"""
One-off: create the Linear epic + FE/BE sub-issues for US-040
(Senior Judge — Review Referred Cases). Reuses constants from
sync_user_stories.py and updates sync_state.json on success.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = Path(__file__).parent / "sync_state.json"
LINEAR_URL = "https://api.linear.app/graphql"

TEAM_ID = "07877f05-4f32-42b4-a2df-9e1764316652"
PROJECT_ID = "a929c4da-1c7c-46a4-a9ac-af4e8d7e202a"
STATE_BACKLOG = "8371331d-8f0e-4005-9566-f404fb445b61"
LBL_EPIC = "c93eda00-b8b2-4898-85d9-1b88dcaabdb5"
LBL_SUB = "01d81090-818f-4470-9122-ec0ec0573971"
LBL_FE = "82b30d78-8701-41cd-ae31-f55b9411a956"
LBL_BE = "6413d2d4-3aee-48e6-817a-bf23e48f7e87"


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
            [
                "curl", "-sS", "-X", "POST", LINEAR_URL,
                "-H", f"Authorization: {TOKEN}",
                "-H", "Content-Type: application/json",
                "--data-binary", body,
            ],
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


CREATE = """
mutation Create($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier title url }
  }
}
"""


def create_issue(title, description, label_ids, parent_id=None):
    inp = {
        "teamId": TEAM_ID,
        "projectId": PROJECT_ID,
        "stateId": STATE_BACKLOG,
        "title": title,
        "description": description,
        "labelIds": label_ids,
    }
    if parent_id:
        inp["parentId"] = parent_id
    data = gql(CREATE, {"input": inp})
    res = data["issueCreate"]
    if not res["success"]:
        raise RuntimeError(f"create failed for {title}")
    return res["issue"]


EPIC_DESC = """**Epic — US-040: Senior Judge — Review Referred Cases**

> As a senior judicial officer, I want a single inbox that surfaces every
> item routed to me for senior-judge action — escalation referrals,
> decision amendments by other judges, and reopen requests — so that I can
> review, approve, reject, or reassign each one without hunting through
> individual cases.

**Persona:** Senior Judge (gated by `senior_judge` role from US-033)

**Aggregates:**
- Escalations referred from US-024 ("Refer to Senior Judge")
- Reopen requests from US-037 awaiting senior approval
- Amendments-of-record from US-036 by a non-original-recording judge

**Per-entry actions:** Approve · Reject (with reason) · Reassign · Request more info

**Constraints:**
- Two-person rule: a senior judge cannot approve their own referral
- All actions logged to audit trail (US-026)
- In-app notifications only for MVP (email/SMS deferred)
- US-038, US-039 reserved for follow-up senior-judge stories

**Source of truth:** see canonical text in
`docs/architecture/01-user-stories.md` §1.10 and the wiki page
`.linear-sdlc/wiki/concepts/user-stories-10-senior-judge.md`.

**Sub-issues:** see linked FE and BE sub-issues for scope split.
"""

BE_DESC = """**Backend — US-040: Senior Judge inbox API**

Implements the backend surface for the Senior Judge unified inbox.

**Scope:**
- New endpoints under `/api/senior-judge/inbox`:
  - `GET /` — paged inbox listing with filters (type, judge, domain)
  - `GET /{entry_id}` — entry detail with case context
  - `POST /{entry_id}/approve` — approve referral, trigger downstream action
  - `POST /{entry_id}/reject` — reject with reason
  - `POST /{entry_id}/reassign` — route to another senior judge
  - `POST /{entry_id}/request-info` — return to originating judge with question
- Aggregator service that merges three referral sources:
  - Escalations (US-024 referral table — also a backend gap, see VER for US-024 BE)
  - Reopen requests (US-037)
  - Amendments-of-record (US-036) when amending judge ≠ original recorder
- Role gate: requires `senior_judge` role (from US-033)
- Two-person-rule guard: reject approve actions where the senior judge is also the originator
- Audit-trail emission for every state change (Solace event → US-026)
- Counter-badge endpoint for global nav: `GET /api/senior-judge/inbox/count`
- All endpoints documented in OpenAPI spec

**Out of scope:**
- Frontend UI (separate sub-issue)
- Email/SMS notifications (MVP is in-app only)
- US-038/US-039 features (bulk-action, analytics)

**Acceptance:**
- Happy-path tests for each action
- Two-person-rule rejection test
- Role-gate test (regular judge → 403)
- Audit-trail event verified for each action
"""

FE_DESC = """**Frontend — US-040: Senior Judge inbox UI**

Implements the Senior Judge inbox view, gated to the `senior_judge` role.

**Scope:**
- New route `/senior-inbox` (visible only when role check passes)
- Inbox list view:
  - Sorted oldest-first within priority tier
  - Filter chips: referral type (escalation / reopen / amendment), originating judge, domain (SCT / Traffic)
  - Each entry shows: case ID, originating judge, type, reason, age, one-line preview
- Entry detail pane (right rail):
  - Loads case context (decision, original audit trail, attached new evidence for reopens)
- Per-entry action buttons: Approve · Reject (with reason modal) · Reassign (senior-judge picker) · Request more info (question modal)
- Confirmation modal for approvals on SCT cases above the $20k threshold (domain note)
- Counter badge in global navigation, polled or SSE-driven
- In-app notification surface for outcomes returned to originating judges
- Empty-state messaging when inbox is clear

**Out of scope:**
- Backend API (separate sub-issue)
- Email/SMS notification UI (deferred)
- US-038/US-039 features

**Acceptance:**
- Role-gate redirect for non-senior-judge users
- Component tests for each per-entry action
- E2E test: senior judge approves a reopen → confirms case status change in UI
"""


def main():
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    state.setdefault("epics", {})
    state.setdefault("us040_subissues", {})

    # Idempotency: skip if already created
    if "US-040" in state["epics"] and state.get("us040_subissues", {}).get("be") and state.get("us040_subissues", {}).get("fe"):
        print("US-040 already fully created; nothing to do")
        print(json.dumps({
            "epic": state["epics"]["US-040"],
            "subissues": state["us040_subissues"],
        }, indent=2))
        return

    # 1. Epic
    if "US-040" not in state["epics"]:
        epic = create_issue(
            title="US-040 — Senior Judge — Review Referred Cases",
            description=EPIC_DESC,
            label_ids=[LBL_EPIC, LBL_FE, LBL_BE],
        )
        state["epics"]["US-040"] = {"id": epic["id"], "identifier": epic["identifier"]}
        STATE_PATH.write_text(json.dumps(state, indent=2))
        print(f"  epic: {epic['identifier']} {epic['url']}")
    else:
        print(f"  epic exists: {state['epics']['US-040']['identifier']}")

    epic_id = state["epics"]["US-040"]["id"]

    # 2. Backend sub-issue
    if not state["us040_subissues"].get("be"):
        be = create_issue(
            title="US-040 — Senior Judge inbox API (BE)",
            description=BE_DESC,
            label_ids=[LBL_SUB, LBL_BE],
            parent_id=epic_id,
        )
        state["us040_subissues"]["be"] = {"id": be["id"], "identifier": be["identifier"]}
        STATE_PATH.write_text(json.dumps(state, indent=2))
        print(f"  be:   {be['identifier']} {be['url']}")

    # 3. Frontend sub-issue
    if not state["us040_subissues"].get("fe"):
        fe = create_issue(
            title="US-040 — Senior Judge inbox UI (FE)",
            description=FE_DESC,
            label_ids=[LBL_SUB, LBL_FE],
            parent_id=epic_id,
        )
        state["us040_subissues"]["fe"] = {"id": fe["id"], "identifier": fe["identifier"]}
        STATE_PATH.write_text(json.dumps(state, indent=2))
        print(f"  fe:   {fe['identifier']} {fe['url']}")

    print("\nDONE")
    print(json.dumps({
        "epic": state["epics"]["US-040"],
        "subissues": state["us040_subissues"],
    }, indent=2))


if __name__ == "__main__":
    main()
