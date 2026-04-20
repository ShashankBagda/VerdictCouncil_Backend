# VerdictCouncil — Backend

Multi-agent AI judicial decision-support system. FastAPI backend with 9 specialised agents, async PostgreSQL, Redis, and Solace Agent Mesh.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Local Development](#local-development)
3. [Architecture](#architecture)
4. [AI Agentic Workflow with Linear](#ai-agentic-workflow-with-linear)
5. [Branch Naming](#branch-naming)
6. [PR Template](#pr-template)

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.12 | `brew install python@3.12` |
| PostgreSQL | 15+ | `brew install postgresql@15` |
| Redis | 7+ | `brew install redis` |
| Docker | latest | [docker.com](https://docker.com) |
| Node.js | latest | `brew install node` (required for Linear MCP server) |
| GitHub CLI | latest | `brew install gh` |
| Claude Code | latest | [claude.ai/code](https://claude.ai/code) |
| linear-sdlc | latest | [linear-sdlc](https://github.com/douglasswm/linear-sdlc) |

---

## Local Development

```bash
cp .env.example .env          # fill in your values
docker compose -f docker-compose.infra.yml up -d   # start postgres + redis
make dev                      # run the API
```

See `docs/architecture/README.md` for the full architecture documentation.

---

## Architecture

See [`docs/architecture/README.md`](docs/architecture/README.md) for the complete architecture documentation including:
- System architecture and agent configurations
- Tech stack and infrastructure
- CI/CD pipeline
- Local development guide

---

## AI Agentic Workflow with Linear

VerdictCouncil uses **linear-sdlc** — a Claude Code skill suite that wraps the Linear MCP server with a complete SDLC workflow. All Linear issue management goes through skills, never the `linear` CLI directly.

```
┌─────────────────────────────────────────────────────────┐
│                    LINEAR WORKSPACE                       │
│  Issues ← dependencies → Issues                          │
│  Parent issues ← sub-issues (for cross-repo work)        │
│    ↕ native GitHub integration (auto PR linking)         │
│    ↕ status auto-updates on PR merge                     │
└───────────────────────┬─────────────────────────────────┘
                        │ Linear MCP server
            ┌───────────┴───────────┐
            │     CLAUDE CODE       │
            │   (linear-sdlc skills)│
            │                       │
            │  /brainstorm          │
            │  /create-tickets      │
            │  /next                │
            │  /implement VER-XX    │
            │  /checkpoint          │
            │  /health              │
            └───────────┬───────────┘
                        │
            ┌───────────┴───────────┐
            │       GITHUB          │
            │  PRs ← auto-linked   │
            │  Branches ← created  │
            │  Reviews ← human     │
            └───────────────────────┘
```

**Native layer (always-on):** Linear's GitHub integration auto-links PRs when the branch name contains the issue ID, and auto-updates issue status when PRs merge.

**linear-sdlc skills:** Handle brainstorming, ticket creation, ticket selection, full implementation lifecycle (with parallel specialist code reviews), checkpoints, and health monitoring.

---

### One-time Setup

#### 1. Install tools

```bash
brew install node      # required for Linear MCP server
brew install gh
gh auth login
```

#### 2. Install linear-sdlc

Paste this one-liner into Claude Code:

```
Install linear-sdlc: run git clone --single-branch --depth 1 https://github.com/douglasswm/linear-sdlc.git ~/.claude/skills/linear-sdlc && cd ~/.claude/skills/linear-sdlc && ./setup
```

The setup script will prompt for your Linear API key (Linear Settings → API → Personal API keys) and configure the MCP server in `~/.claude/settings.json`. Restart Claude Code afterward so the MCP server loads.

#### 3. Connect Linear to GitHub (one-time per workspace)

1. Linear Settings → Integrations → GitHub → Connect
2. Link the `VerdictCouncil_Backend` repo to your team
3. Enable: auto-link PRs via branch name, auto-close issues on PR merge

**Verify:** Create a test branch named `feat/ver-1-test`, open a PR, and confirm it appears linked on the Linear issue.

#### 4. Verify linear-sdlc

In Claude Code, after restart:

- Ask **"List my Linear teams"** — should return your team(s) via the MCP server
- Run `/next` — should query Linear and present unblocked tickets
- Run `/health` — should detect project tools (pytest, ruff, mypy) and show a quality score

---

### Phase 1: Planning a Feature

Use `/brainstorm` to explore an idea and write a spec, then `/create-tickets` to convert it into Linear issues.

```
/brainstorm rate limiting
```

Walks you through a structured discussion (problem, impact, solution shape, scope, technical approach) and writes a spec to `specs/rate-limiting.md`. For complex features (multi-system, architecture decisions), it can escalate to `superpowers:brainstorming` for a full design spec.

```
/create-tickets specs/rate-limiting.md
```

Creates a parent issue and sub-issues in Linear with proper dependencies, priorities, and labels. You confirm the breakdown before anything is created.

#### Cross-repo issues

When a feature needs both backend and frontend changes, create a parent issue with sub-issues per repo:

```
Parent: VER-100  "Add user search"
  ├── VER-101  "Backend: search API endpoint"   → this repo, one branch, one PR
  └── VER-102  "Frontend: search UI component"  → Frontend repo, one branch, one PR
```

The parent auto-closes when all sub-issues close. `/create-tickets` handles parent/sub-issue linking automatically.

---

### Phase 2: Implementing a Ticket

```
/next
```

Queries your assigned Linear tickets, filters out blocked ones, ranks by priority and cycle deadline, and presents the top 3. When you pick one, it hands off to `/implement`.

```
/implement VER-42
```

Full lifecycle for a single ticket:

1. **Loads the ticket** from Linear (title, description, parent, spec)
2. **Pre-flight checks** — verifies the ticket isn't blocked, checks for existing branches, ensures clean working tree
3. **Sets status** to "In Progress" in Linear
4. **Creates a branch** (`feat/ver-42-short-description`) from `development`
5. **Plans** the implementation if the ticket is complex (>3 acceptance criteria)
6. **You code** with Claude's help
7. **Specialist self-review** — dispatches parallel sub-agents that review the diff:
   - **Testing specialist** — missing tests, weak assertions, untested paths
   - **Security specialist** — injection, hardcoded secrets, auth gaps (when relevant code changed)
   - **Performance specialist** — N+1 queries, missing indexes, unbounded results (when backend code changed)
   - **Code quality specialist** — dead code, DRY violations, naming issues
8. **Creates a PR** via `gh` targeting `development`, with the ticket linked
9. **Sets status** to "In Review" in Linear
10. **Logs learnings and timeline** for future sessions

Critical findings from specialists must be fixed before the PR is created. Warnings are presented for your decision.

#### Rules

1. **One issue = one branch = one PR.** Shared branches are not permitted.
2. Never start an issue with unresolved `blocked_by` relations — `/implement` blocks this automatically.
3. Always branch from `development` — `/implement` enforces this.
4. Commits must **not** include co-author lines or AI attribution (see CLAUDE.md).

---

### Phase 3: Saving Progress and Resuming

```
/checkpoint
```

Captures git state, current ticket context, what you've done, and what's remaining. Writes a checkpoint file to `~/.linear-sdlc/projects/{slug}/checkpoints/`.

In a new session, resume:

```
/checkpoint resume
```

Loads the latest checkpoint, shows where you left off, offers to switch to the right branch and continue.

---

### Phase 4: Code Health Monitoring

```
/health
```

Auto-detects your project's quality tools (pytest, ruff, mypy, etc.), runs each one, and computes a weighted composite score:

- **Tests** (30%) — pass rate and coverage
- **Lint** (25%) — errors and warnings
- **Type checking** (25%) — type errors
- **Dead code** (20%) — unused code findings

Displays a dashboard with per-tool scores, composite score, trend vs previous run, and top 3 actionable recommendations.

---

### Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Skills not appearing in Claude Code | linear-sdlc not installed or not symlinked | Re-run `cd ~/.claude/skills/linear-sdlc && ./setup` |
| "List my Linear teams" returns nothing | MCP server not loaded or wrong API key | Restart Claude Code; check `~/.claude/settings.json` for `mcpServers.linear` |
| `/implement` fails to create branch | Working tree dirty or wrong base branch | Stash/commit changes; checkout `development` first |
| PR not auto-linked in Linear | Branch name missing issue ID | `/implement` creates the right format; if you branched manually, ensure `feat/ver-XX-*` |
| Issue auto-closed before frontend PR merges | Cross-repo issue not using sub-issues | Use `/create-tickets` which creates parent + sub-issue per repo |
| Specialist review hangs | Sub-agent timeout | Re-run `/implement` and skip specialist review temporarily |

---

## Branch Naming

All feature branches include the Linear issue ID:

```
feat/<issue-id>-<short-description>

Examples:
  feat/ver-123-add-user-search
  feat/ver-456-fix-auth-token-expiry
  feat/ver-789-refactor-agent-pipeline
```

The `/implement` skill creates this automatically when you start a ticket. The issue ID enables Linear's GitHub integration to auto-link PRs.

---

## PR Template

All PRs follow the template defined in `CLAUDE.md`. Key points:

- Title: `<type>(<scope>): <short description>` under 70 characters
- Target branch: `development` for feature branches
- No co-author lines or AI attribution in commits or PR bodies
- Run tests before opening a PR
