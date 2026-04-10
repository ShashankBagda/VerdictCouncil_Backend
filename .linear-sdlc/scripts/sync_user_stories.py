#!/usr/bin/env python3
"""
One-shot orchestration script for the user-stories Linear sync.

Reads $LINEAR_API_KEY from env (or sources ~/.linear-sdlc/env).

Phase A: create epics for US-001..US-037 + Phase 5 SAM Mesh epic.
Phase B: reparent + relabel existing VER issues per the spec.
Phase C: split hybrid VER issues into FE+BE sub-issue pairs (closes original).
Phase D: create gap-filler sub-issues (US-005, US-024 BE, US-031..037).
Phase E: upsert the project document "User Stories" with the wiki index content.

Persists all created IDs to .linear-sdlc/scripts/sync_state.json so the script
can be re-entered after interruption (each phase checks state and skips
already-completed work).

Run: python3 .linear-sdlc/scripts/sync_user_stories.py [phase]
Phases: a, b, c, d, e, all (default all)
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

# Constants from discovery
TEAM_ID = "07877f05-4f32-42b4-a2df-9e1764316652"
PROJECT_ID = "a929c4da-1c7c-46a4-a9ac-af4e8d7e202a"
STATE_BACKLOG = "8371331d-8f0e-4005-9566-f404fb445b61"
STATE_CANCELED = "146fc666-60e7-4073-9e1a-0ecd4c512466"
LBL_EPIC = "c93eda00-b8b2-4898-85d9-1b88dcaabdb5"
LBL_SUB = "01d81090-818f-4470-9122-ec0ec0573971"
LBL_FE = "82b30d78-8701-41cd-ae31-f55b9411a956"
LBL_BE = "6413d2d4-3aee-48e6-817a-bf23e48f7e87"
LBL_BUG = "1dc20005-7993-455b-a5f9-c0ea78766439"


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


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {
        "epics": {},
        "relabeled": [],
        "split": {},
        "gap_filler": {},
        "doc_id": None,
    }


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# User story roster
# ---------------------------------------------------------------------------

# (key, title, persona)
STORIES = [
    ("US-001", "Upload New Case", "Judge"),
    ("US-002", "View Document Processing Status", "Judge"),
    ("US-003", "Receive Jurisdiction Validation Result", "Judge"),
    ("US-004", "Handle Rejected Cases", "Judge"),
    ("US-005", "Re-upload or Add Documents to Existing Case", "Judge"),
    ("US-006", "Review Evidence Analysis Dashboard", "Judge"),
    ("US-007", "View Fact Timeline", "Judge"),
    ("US-008", "Drill Down to Source Document", "Judge"),
    ("US-009", "Flag Disputed Facts", "Judge"),
    ("US-010", "Review Evidence Gaps", "Judge"),
    ("US-011", "Review Witness Profiles and Credibility Scores", "Judge"),
    ("US-012", "View Anticipated Testimony (Traffic Only)", "Judge"),
    ("US-013", "Review Suggested Judicial Questions", "Judge"),
    ("US-014", "Review Applicable Statutes", "Judge"),
    ("US-015", "Review Precedent Cases", "Judge"),
    ("US-016", "Search Live Precedent Database", "Judge"),
    ("US-017", "View Knowledge Base Status", "Judge"),
    ("US-018", "Review Both Sides' Arguments", "Judge"),
    ("US-019", "Review Deliberation Reasoning Chain", "Judge"),
    ("US-020", "Prepare Hearing Pack", "Judge"),
    ("US-021", "Compare Alternative Outcomes", "Judge"),
    ("US-022", "Review Verdict Recommendation", "Judge"),
    ("US-023", "Review Fairness and Bias Audit", "Judge"),
    ("US-024", "Handle Escalated Cases", "Judge"),
    ("US-025", "Record Judicial Decision", "Judge"),
    ("US-026", "View Full Audit Trail", "Judge"),
    ("US-027", "Export Case Report", "Judge"),
    ("US-028", "Search and Filter Cases", "Judge"),
    ("US-029", "View Dashboard Overview", "Judge"),
    ("US-030", "Manage Session and Authentication", "Judge"),
    ("US-031", "Refresh / Re-index Vector Stores", "Admin"),
    ("US-032", "Monitor Agent & Pipeline Health", "Ops"),
    ("US-033", "Manage User Accounts and Roles", "Admin"),
    ("US-034", "Configure Cost and Quota Alerts", "Admin"),
    ("US-035", "Take In-Hearing Notes", "Judge"),
    ("US-036", "Amend a Recorded Decision", "Judge"),
    ("US-037", "Reopen a Closed Case", "Judge + Senior Judge"),
]


def epic_description(us_id, title, persona):
    n = int(us_id.split("-")[1])
    if 1 <= n <= 5:
        section = "1.1 Case Intake & Setup"
    elif 6 <= n <= 10:
        section = "1.2 Evidence & Facts"
    elif 11 <= n <= 13:
        section = "1.3 Witness Analysis"
    elif 14 <= n <= 17:
        section = "1.4 Legal Research"
    elif 18 <= n <= 21:
        section = "1.5 Arguments & Deliberation"
    elif 22 <= n <= 25:
        section = "1.6 Verdict & Governance"
    elif 26 <= n <= 30:
        section = "1.7 Audit, Export & Session"
    elif 31 <= n <= 34:
        section = "1.8 Administration & Operations"
    else:
        section = "1.9 Hearing & Post-Decision"
    return (
        f"**{us_id} — {title}**\n\n"
        f"**Persona:** {persona}  \n"
        f"**Section:** {section}\n\n"
        f"This is the **epic** for {us_id}. Sub-issues represent the concrete "
        f"backend (Python/FastAPI/9-agent pipeline) and frontend (React/Vite) "
        f"work needed to deliver the story end-to-end.\n\n"
        f"Canonical text with full acceptance criteria and happy flow:\n"
        f"- [docs/architecture/01-user-stories.md](https://github.com/ShashankBagda/VerdictCouncil_Backend/blob/development/docs/architecture/01-user-stories.md) (search for `### {us_id}`)\n"
        f"- Wiki summary: [.linear-sdlc/wiki/concepts/user-stories-index.md](https://github.com/ShashankBagda/VerdictCouncil_Backend/blob/development/.linear-sdlc/wiki/concepts/user-stories-index.md)\n\n"
        f"**Definition of done:**\n"
        f"- All linked sub-issues closed\n"
        f"- Acceptance criteria from the canonical doc verified end-to-end\n"
        f"- FE and BE merged to `development`\n"
    )


# ---------------------------------------------------------------------------
# Phase A — create epics
# ---------------------------------------------------------------------------

def phase_a(state):
    print("=== Phase A: create 37 US epics + Phase 5 SAM Mesh epic ===")
    created = 0
    for us_id, title, persona in STORIES:
        if us_id in state["epics"]:
            continue
        desc = epic_description(us_id, title, persona)
        labels = [LBL_EPIC, LBL_FE, LBL_BE]
        result = gql(
            "mutation($input: IssueCreateInput!) { issueCreate(input:$input) { success issue { id identifier title } } }",
            {
                "input": {
                    "title": f"[{us_id}] {title}",
                    "description": desc,
                    "teamId": TEAM_ID,
                    "projectId": PROJECT_ID,
                    "stateId": STATE_BACKLOG,
                    "labelIds": labels,
                }
            },
        )
        issue = result["issueCreate"]["issue"]
        state["epics"][us_id] = {"id": issue["id"], "identifier": issue["identifier"]}
        save_state(state)
        created += 1
        print(f"  + {us_id} -> {issue['identifier']}")
        time.sleep(0.05)

    if "PHASE5-SAM" not in state["epics"]:
        result = gql(
            "mutation($input: IssueCreateInput!) { issueCreate(input:$input) { success issue { id identifier title } } }",
            {
                "input": {
                    "title": "[PHASE5] SAM Mesh + Solace Migration",
                    "description": (
                        "**Phase 5 — SAM agent mesh and Solace event broker migration.**\n\n"
                        "Orthogonal infrastructure track. Not user-story aligned. "
                        "Replaces the in-process pipeline runner with a SAM/Solace mesh. "
                        "Sub-issues: VER-45 SAM definitions, VER-46 mesh runner replacement, "
                        "VER-47 SSE pipeline status stream, VER-48 Solace HA, VER-49 total "
                        "precedent source failure flag.\n\n"
                        "See docs/architecture/ for design notes and the project memory "
                        "snapshot for context."
                    ),
                    "teamId": TEAM_ID,
                    "projectId": PROJECT_ID,
                    "stateId": STATE_BACKLOG,
                    "labelIds": [LBL_EPIC, LBL_BE],
                }
            },
        )
        issue = result["issueCreate"]["issue"]
        state["epics"]["PHASE5-SAM"] = {"id": issue["id"], "identifier": issue["identifier"]}
        save_state(state)
        created += 1
        print(f"  + PHASE5-SAM -> {issue['identifier']}")

    print(f"Phase A done — created {created} epic(s)")


# ---------------------------------------------------------------------------
# Phase B — reparent + relabel existing VER issues that are NOT split
# ---------------------------------------------------------------------------

# (existing VER number, parent epic key, new label set)
KEEP_RELABEL = [
    (7,  "US-001", "backend"),
    (8,  "US-001", "backend"),
    (9,  "US-001", "backend"),
    (10, "US-002", "backend"),
    (11, "US-003", "backend"),
    (12, "US-004", "backend"),
    (14, "US-030", "frontend"),
    (15, "US-001", "frontend"),
    (16, "US-002", "frontend"),
    (17, "US-009", "frontend"),
    (18, "US-016", "frontend"),
    (19, "US-017", "frontend"),
    (20, "US-023", "frontend"),
    (21, "US-024", "frontend"),
    (22, "US-025", "frontend"),
    (40, "US-030", "backend"),
    (41, "US-030", "frontend"),
    (42, "US-022", "backend"),  # bug — also gets bug label
    (45, "PHASE5-SAM", "backend"),
    (46, "PHASE5-SAM", "backend"),
    (47, "PHASE5-SAM", "backend"),
    (48, "PHASE5-SAM", "backend"),
    (49, "PHASE5-SAM", "backend"),
]


def get_issue_uuid(identifier):
    result = gql(
        "query($id: String!) { issue(id:$id) { id identifier title labels { nodes { id name } } } }",
        {"id": identifier},
    )
    return result["issue"]


def phase_b(state):
    print("=== Phase B: reparent + relabel existing VER issues (in-place) ===")
    for ver_n, parent_key, scope in KEEP_RELABEL:
        ident = f"VER-{ver_n}"
        if ident in state["relabeled"]:
            continue
        try:
            issue = get_issue_uuid(ident)
        except Exception as e:
            print(f"  ! {ident} fetch failed: {e}")
            continue
        if not issue:
            print(f"  ! {ident} does not exist, skipping")
            state["relabeled"].append(ident)
            save_state(state)
            continue

        existing_labels = {lbl["id"]: lbl["name"] for lbl in issue["labels"]["nodes"]}
        new_labels = set(existing_labels.keys())
        new_labels.add(LBL_SUB)
        new_labels.add(LBL_FE if scope == "frontend" else LBL_BE)
        if ver_n == 42:
            new_labels.add(LBL_BUG)

        parent_id = state["epics"][parent_key]["id"]
        gql(
            "mutation($id:String!,$input:IssueUpdateInput!) { issueUpdate(id:$id,input:$input) { success } }",
            {
                "id": issue["id"],
                "input": {
                    "parentId": parent_id,
                    "projectId": PROJECT_ID,
                    "labelIds": list(new_labels),
                },
            },
        )
        state["relabeled"].append(ident)
        save_state(state)
        print(f"  ~ {ident} -> parent={parent_key} +{scope}+sub-issue")
        time.sleep(0.05)
    print("Phase B done")


# ---------------------------------------------------------------------------
# Phase C — split hybrid VER issues into FE/BE sub-issue pairs
# ---------------------------------------------------------------------------

# (existing VER number, parent epic key, BE title suffix, FE title suffix)
SPLIT_HYBRIDS = [
    (23, "US-026", "Audit-trail filterable endpoint", "Audit-trail filter UI + JSON export"),
    (25, "US-006", "Evidence dashboard endpoint (GET /cases/{id}/evidence)", "Evidence dashboard UI"),
    (26, "US-007", "Fact timeline endpoint (GET /cases/{id}/timeline)", "Fact timeline UI"),
    (27, "US-008", "Source document drill-down endpoint", "Source drill-down split-pane viewer"),
    (28, "US-011", "Witnesses endpoint (incl. anticipated testimony)", "Witness profiles UI (incl. anticipated testimony)"),
    (29, "US-013", "Suggested questions endpoint", "Suggested questions editor UI"),
    (30, "US-014", "Statutes + precedents endpoints", "Legal Framework UI"),
    (31, "US-018", "Arguments endpoint", "Balanced / adversarial arguments UI"),
    (32, "US-019", "Deliberation reasoning chain + alternative outcomes endpoint", "Deliberation UI + alt outcomes"),
    (33, "US-022", "Verdict endpoint (GET /cases/{id}/verdict)", "Verdict + disclaimer UI"),
    (35, "US-020", "Hearing pack endpoint", "Hearing pack annotation UI"),
    (36, "US-027", "Case report export (PDF + JSON) endpoint", "Case report export UI + disclaimer"),
    (37, "US-028", "Advanced case search endpoint (full-text, date, pagination)", "Advanced case search UI"),
    (38, "US-029", "Dashboard metrics endpoint (trends, time windows)", "Dashboard metrics UI"),
]


def create_subissue(parent_key, title, scope, state, extra_desc=""):
    parent_id = state["epics"][parent_key]["id"]
    parent_ident = state["epics"][parent_key]["identifier"]
    label_scope = LBL_FE if scope == "frontend" else LBL_BE
    desc = (
        f"Sub-issue of {parent_ident} ({parent_key}).\n\n"
        f"**Scope:** {scope}\n\n"
        f"{extra_desc}\n"
        f"See parent epic for the full user story acceptance criteria."
    )
    result = gql(
        "mutation($input:IssueCreateInput!) { issueCreate(input:$input) { success issue { id identifier title } } }",
        {
            "input": {
                "title": f"[{parent_key}] {title}",
                "description": desc,
                "teamId": TEAM_ID,
                "projectId": PROJECT_ID,
                "parentId": parent_id,
                "stateId": STATE_BACKLOG,
                "labelIds": [LBL_SUB, label_scope],
            }
        },
    )
    return result["issueCreate"]["issue"]


def phase_c(state):
    print("=== Phase C: split hybrid VER issues ===")
    for ver_n, parent_key, be_suffix, fe_suffix in SPLIT_HYBRIDS:
        key = f"VER-{ver_n}"
        if key in state["split"]:
            continue
        try:
            original = get_issue_uuid(key)
        except Exception as e:
            print(f"  ! {key} fetch failed: {e}")
            continue
        if not original:
            print(f"  ! {key} not found, skipping")
            state["split"][key] = {"status": "missing"}
            save_state(state)
            continue

        be = create_subissue(parent_key, be_suffix, "backend", state,
                             extra_desc=f"Replaces the backend portion of the original hybrid issue {key} (\"{original['title']}\").")
        fe = create_subissue(parent_key, fe_suffix, "frontend", state,
                             extra_desc=f"Replaces the frontend portion of the original hybrid issue {key} (\"{original['title']}\").")

        # Cancel the original with a comment + state change
        comment = (
            f"Replaced by **{be['identifier']}** (backend) and **{fe['identifier']}** (frontend) "
            f"as part of the user-story sync (see specs/user-stories-sync.md). "
            f"Both sub-issues are parented to epic {state['epics'][parent_key]['identifier']} ({parent_key})."
        )
        gql(
            "mutation($input:CommentCreateInput!) { commentCreate(input:$input) { success } }",
            {"input": {"issueId": original["id"], "body": comment}},
        )
        gql(
            "mutation($id:String!,$input:IssueUpdateInput!) { issueUpdate(id:$id,input:$input) { success } }",
            {
                "id": original["id"],
                "input": {"stateId": STATE_CANCELED},
            },
        )

        state["split"][key] = {
            "be": be["identifier"],
            "fe": fe["identifier"],
            "parent": parent_key,
        }
        save_state(state)
        print(f"  / {key} -> BE {be['identifier']} + FE {fe['identifier']} (canceled original)")
        time.sleep(0.1)
    print("Phase C done")


# ---------------------------------------------------------------------------
# Phase D — gap-filler sub-issues
# ---------------------------------------------------------------------------

# (slug, parent_key, title, scope, description)
GAP_FILLERS = [
    ("us005-be", "US-005", "Document append endpoint + selective stage re-trigger",
     "backend",
     "POST /cases/{id}/documents to append files to an existing case. Detect which pipeline stages are affected by the new material and re-enqueue only those stages. Preserve prior outputs from unaffected stages. Maintain a document version history table."),
    ("us005-fe", "US-005", "Add Documents UI + version history list",
     "frontend",
     "\"Add Documents\" button on the case detail view. Multi-file picker, optional reason text. Display the document version history (filename, upload time, who uploaded). Show which pipeline stages are re-running after the upload."),
    ("us024-be", "US-024", "Escalation endpoint + audit log",
     "backend",
     "Backend support for the escalation flow currently only covered on the FE by VER-21. Endpoint to list escalated cases, accept a judge's continue/refer/proceed decision, and write the decision + rationale to the audit trail. The escalation can be raised by Agent 2 (complexity) or Agent 9 (governance)."),
    ("us031-be", "US-031", "Vector store refresh job + diff report endpoint",
     "backend",
     "Background job runner for vector store re-ingest. POST /admin/kb/refresh with store name. GET /admin/kb/refresh/{job_id} for status (pending/fetching/embedding/indexing/complete/failed). On completion, store the diff (added/removed/unchanged counts). Audit trail entry per refresh."),
    ("us031-fe", "US-031", "Knowledge base admin page + refresh trigger UI",
     "frontend",
     "Admin-only Settings → Knowledge Base page. Lists each vector store with doc count, last refresh timestamp, and status. \"Refresh\" button per store. Reason input on trigger. Live progress indicator. Diff report on completion."),
    ("us032-be", "US-032", "Agent + pipeline health metrics endpoint",
     "backend",
     "GET /admin/ops/health returning per-agent metrics (last invocation, success rate, p50/p95 latency, failure count over 5min/1hr/24hr windows), Solace queue depth, dead-letter queue count, and the pipeline stage distribution histogram. Recent failures with stack traces accessible via a separate endpoint."),
    ("us032-fe", "US-032", "Ops pipeline health dashboard UI",
     "frontend",
     "Admin-only Ops → Pipeline Health page. 9-agent grid with green/amber/red tiles. Click-through to recent failures. Solace queue and dead-letter visibility. Historical 7-day view with hourly buckets."),
    ("us033-be", "US-033", "User account + role management endpoints",
     "backend",
     "Admin endpoints for account CRUD, password reset (one-time link), role assignment (judge / senior_judge / admin), deactivation (non-destructive). Role-based route access enforcement on all protected endpoints. Audit trail for every account/role mutation."),
    ("us033-fe", "US-033", "User & role management admin UI",
     "frontend",
     "Admin-only Settings → Users page. List with role + status filters. New User form. Per-user actions: deactivate, reset password, change role. Surface role-change history."),
    ("us034-be", "US-034", "Cost tracking + quota enforcement",
     "backend",
     "Track OpenAI API spend per request and per user. Configurable monthly cap, warning thresholds, and per-user quotas. Hard cap blocks new case submissions when exceeded with a clear error. One-time admin override mechanism. Daily spend rollups for the dashboard."),
    ("us034-fe", "US-034", "Cost & quota admin dashboard",
     "frontend",
     "Admin-only Settings → Costs page. Cap configuration form. Live spend, projected month-end, top spenders, remaining quota per user. Override action for blocked users."),
    ("us035-be", "US-035", "Hearing notes endpoint + persistence",
     "backend",
     "Endpoints to start/end hearing mode, append timestamped notes (per-item or general), tag notes as probative or administrative, and retrieve notes for a case. Notes are immutable once hearing mode ends. Notes integrated into the audit trail."),
    ("us035-fe", "US-035", "In-hearing notes UI + offline-resilient sync",
     "frontend",
     "Hearing-mode toggle on the hearing pack view. Notes sidebar with per-item anchoring, free-text input, probative/administrative tag. Local-first persistence with periodic sync to the backend; resilient to connectivity loss."),
    ("us036-be", "US-036", "Decision amendment endpoint + immutable chain",
     "backend",
     "POST /cases/{id}/decision/amend with amendment type (clerical_correction, post_hearing_update, error_correction) and reason. Creates a new decision record linked to the predecessor; preserves the original. GET /cases/{id}/decision returns the full chain. Role check: recording judge or senior judge only."),
    ("us036-fe", "US-036", "Amend decision UI + history view",
     "frontend",
     "\"Amend Decision\" action on the Verdict tab. Form with type selector and reason. Audit trail tab shows the full amendment chain. Exported case report includes amendment history."),
    ("us037-be", "US-037", "Reopen request + senior judge approval workflow",
     "backend",
     "POST /cases/{id}/reopen-request with reason and justification (and optional attachments). Routes to the senior judge inbox. POST /cases/{id}/reopen-approve and /cases/{id}/reopen-reject (senior judge only). On approve, transitions case state and re-triggers pipeline via US-005 flow. Original closure record preserved. Audit trail entries throughout."),
    ("us037-fe", "US-037", "Reopen request UI + senior judge approval inbox",
     "frontend",
     "\"Request Reopen\" action on closed case detail view with reason + justification + file attach. Senior judge inbox view listing pending reopen requests with approve/reject actions. Reopened case flag in the case list."),
]


def phase_d(state):
    print("=== Phase D: gap-filler sub-issues ===")
    for slug, parent_key, title, scope, desc in GAP_FILLERS:
        if slug in state["gap_filler"]:
            continue
        issue = create_subissue(parent_key, title, scope, state, extra_desc=desc)
        state["gap_filler"][slug] = {
            "identifier": issue["identifier"],
            "parent": parent_key,
            "scope": scope,
        }
        save_state(state)
        print(f"  + {slug} -> {issue['identifier']} ({scope}, parent={parent_key})")
        time.sleep(0.05)
    print("Phase D done")


# ---------------------------------------------------------------------------
# Phase E — project document
# ---------------------------------------------------------------------------

def phase_e(state):
    print("=== Phase E: project document 'User Stories' ===")
    if state.get("doc_id"):
        print(f"  ~ document already exists: {state['doc_id']}")
        return

    body_lines = [
        "# VerdictCouncil MVP — User Stories",
        "",
        "Master index of the 37 user stories for the VerdictCouncil judicial",
        "decision-support MVP. Canonical text lives in the backend repo at",
        "`docs/architecture/01-user-stories.md`. The wiki summary is at",
        "`.linear-sdlc/wiki/concepts/user-stories-index.md`.",
        "",
        "Each user story is represented in this project as an **epic** issue",
        "with one or more sub-issues for the backend (Python/FastAPI) and",
        "frontend (React/Vite) work. Labels:",
        "",
        "- `epic` — parent issue per user story",
        "- `sub-issue` — concrete unit of work under an epic",
        "- `frontend` — React/Vite work",
        "- `backend` — Python/FastAPI/9-agent pipeline work",
        "- `bug` — non-feature defect",
        "",
        "## Roster",
        "",
        "| US | Title | Persona | Linear epic |",
        "|----|-------|---------|-------------|",
    ]
    for us_id, title, persona in STORIES:
        ident = state["epics"].get(us_id, {}).get("identifier", "tbd")
        body_lines.append(f"| {us_id} | {title} | {persona} | {ident} |")

    if "PHASE5-SAM" in state["epics"]:
        body_lines.append("")
        body_lines.append("## Phase 5 — SAM Mesh + Solace Migration")
        body_lines.append("")
        body_lines.append(
            f"Orthogonal infrastructure track tracked under "
            f"{state['epics']['PHASE5-SAM']['identifier']}. Not user-story aligned."
        )

    body = "\n".join(body_lines)

    result = gql(
        "mutation($input: DocumentCreateInput!) { documentCreate(input:$input) { success document { id title url } } }",
        {
            "input": {
                "title": "User Stories",
                "content": body,
                "projectId": PROJECT_ID,
            }
        },
    )
    doc = result["documentCreate"]["document"]
    state["doc_id"] = doc["id"]
    save_state(state)
    print(f"  + document {doc['id']} -> {doc['url']}")
    print("Phase E done")


# ---------------------------------------------------------------------------

def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    state = load_state()
    if phase in ("a", "all"):
        phase_a(state)
    if phase in ("b", "all"):
        phase_b(state)
    if phase in ("c", "all"):
        phase_c(state)
    if phase in ("d", "all"):
        phase_d(state)
    if phase in ("e", "all"):
        phase_e(state)
    print("DONE")


if __name__ == "__main__":
    main()
