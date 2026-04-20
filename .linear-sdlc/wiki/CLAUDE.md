# Wiki Schema — instructions for maintaining this wiki

This file is the **schema** for the project wiki. It is scoped to the
`.linear-sdlc/wiki/` directory by proximity: Claude Code only reads it when
editing files inside this subtree, so it never collides with the root
`CLAUDE.md` of the repo.

You (the LLM) are the **author** of this wiki. A human reviews the diffs
in `git status` before committing. Your job is to keep the wiki current
with every ingested source, maintain cross-references, flag contradictions,
and never leak secrets.

## Three-layer model

Per the llm_wiki pattern:

1. **Raw sources** (immutable). Everything you read to learn — code in the
   repo, Linear tickets (`lsdlc-linear get-issue VER-42`), private learnings
   in `~/.linear-sdlc/projects/<slug>/learnings.jsonl`, and files dropped
   under `sources/` in this wiki (articles, transcripts, assets, legacy).
   **Never modify raw sources.**

2. **The wiki** (you own this layer). The markdown files in `entities/`,
   `concepts/`, `tickets/`, `incidents/`, `queries/`, plus `index.md` and
   `log.md`. You create, update, cross-reference, and lint. The human reads
   and reviews.

3. **This schema** (co-evolved). `CLAUDE.md` — the instructions you're
   reading right now. If you discover a better workflow while using the
   wiki, update this file to document it.

## Directory conventions

```
.linear-sdlc/wiki/
├── CLAUDE.md       # this file — the schema
├── index.md        # content catalog, one line per page, grouped by category
├── log.md          # chronological append-only activity log (git union merge)
├── entities/       # subsystems, modules, services, key files — one page each
├── concepts/       # patterns, conventions, architecture decisions, shared vocabulary
├── tickets/        # synthesis of completed Linear tickets (one per ID)
├── incidents/      # root-caused bugs from /debug (one per slug)
├── queries/        # filed-back answers from /wiki query that compound over time
└── sources/        # RAW LAYER — never modify, only read
    ├── articles/     # external articles, PDFs, blog posts
    ├── transcripts/  # meeting notes, customer calls, chat logs
    ├── assets/       # images referenced from wiki pages
    └── legacy/       # populated by /wiki migrate from the old home-dir wiki
```

### Page naming

- `entities/<kebab-case-name>.md` — one per subsystem. Example: `entities/auth.md`, `entities/api-gateway.md`.
- `concepts/<kebab-case-name>.md` — one per pattern. Example: `concepts/session-handling.md`, `concepts/rate-limiting.md`.
- `tickets/<TICKET-ID>.md` — exactly the Linear identifier. Example: `tickets/VER-42.md`.
- `incidents/<short-slug>.md` — descriptive of the symptom. Example: `incidents/login-loop-on-expired-token.md`.
- `queries/<question-slug>.md` — derived from the question. Example: `queries/how-does-auth-work.md`.

### Cross-references

Use **standard relative markdown links**, not Obsidian-style wiki-links:

```markdown
The session handler is documented in [session-handling](../concepts/session-handling.md).
Affected entity: [auth](../entities/auth.md). See also [VER-42](../tickets/VER-42.md).
```

These render in GitHub, in any markdown viewer, and in every editor without
vault tooling. If a user later adopts Obsidian, these links still resolve.

### Page frontmatter

Minimal YAML frontmatter, used by `lsdlc-wiki lint` only — **not** required
for readers. Example:

```markdown
---
updated: 2026-04-09T14:30:00Z
sources:
  - tickets/VER-42.md
  - sources/articles/rate-limiting.md
---
# Authentication

...
```

- `updated:` — ISO 8601 timestamp. Bump on every substantive edit. Lint flags
  pages older than 90 days as potentially stale.
- `sources:` — list of wiki-relative paths or external refs the page draws from.

## Index.md format

Content catalog, grouped by category. One line per page. **Never write
`index.md` by hand** — always use `lsdlc-wiki index-upsert <page-path>
<category> <one-line-summary>`. It does atomic reads, dedups, and respects
the union-merge driver configured in `.gitattributes`.

```markdown
## Entities

- [entities/auth.md](entities/auth.md) — Authentication subsystem and token flow
- [entities/api-gateway.md](entities/api-gateway.md) — Request routing and rate limiting
```

## Log.md format

Chronological append-only. Every entry starts with a consistent prefix:

```markdown
## [YYYY-MM-DD HH:MM] <kind> | <title>
- list of touched pages (optional)
```

Valid `kind` values: `ingest`, `query-filed`, `lint`, `sync-linear`,
`migrate`, `init`, `ingest-source`.

**Never write `log.md` by hand** — always use `lsdlc-wiki log-append <kind>
<title> --touched file1,file2,...`. It respects the union-merge driver so
concurrent appends from different teammates merge cleanly.

Quick access to recent activity:
```bash
grep '^## \[' log.md | tail -5
```

## The ingest operation (fan-out)

**Critical:** ingest is NOT "write one page." A single source typically
touches 10–15 wiki pages (per the llm_wiki pattern). When a skill triggers
you to ingest something:

1. **Read the source.** For `/implement`: the ticket via `lsdlc-linear
   get-issue <ID>`, plus `git diff main...HEAD`. For `/debug`: the incident
   repro, boundary walk, and root cause. For `/wiki ingest-source <path>`:
   the file's contents.
2. **Read `index.md`.** Find related existing pages.
3. **Read those related pages.** Understand what the wiki already says.
4. **Draft the primary page.**
   - `/implement` → `tickets/<ID>.md` with summary, affected files, design
     notes, links to touched entity/concept pages.
   - `/debug` → `incidents/<slug>.md` with repro, boundary walk, root cause,
     fix (commit link).
   - `/wiki ingest-source` → a summary page in `concepts/` or `sources/`.
5. **Draft updates to related entity and concept pages.** Add sections,
   update claims, extend cross-reference lists. This is where the fan-out
   happens.
6. **Check for contradictions.** Before overwriting a claim on an existing
   page, compare the new statement to the old one. If they disagree, do
   **NOT** silently overwrite — insert a callout:

   ```markdown
   > **⚠ Contradiction noted:** Previously this page said *"X"* (sourced
   > from <old ref>, <date>). New evidence from <new ref>, <date> says
   > *"Y"*. Needs human review.
   ```

   Contradictions stay until a human resolves them. Lint surfaces them on
   every pass.
7. **Secret-scan every draft.** Run `lsdlc-wiki secret-scan <draft-file>` on
   every file you are about to write. Exit code 3 means abort the **entire**
   ingest — no partial writes, no "I'll fix that page later."
8. **Write all drafts.**
9. **Update `index.md`** via `lsdlc-wiki index-upsert` for each new or
   changed page.
10. **Append to `log.md`** via `lsdlc-wiki log-append ingest "<source
    title>" --touched page1.md,page2.md,...` listing every page touched.
11. **Leave everything in the working tree.** Never run `git add`, `git
    commit`, or `git push`. The human reviews and commits when ready.

## The query operation (file-back loop)

When the user runs `/wiki query <question>`:

1. **Read `index.md` first.** Find candidate pages by category and summary.
2. **Drill into the top matches.** Read them fully.
3. **If search is needed**, use `lsdlc-wiki search <term>`. It auto-routes
   to qmd if installed, else grep. Output is `{path, score, snippet}`.
4. **Synthesize an answer** with inline relative-markdown citations to the
   pages you used.
5. **Offer to file the answer back** as `queries/<slug>.md`. If the user
   accepts: secret-scan, write, `index-upsert`, `log-append query-filed`.
6. **If you notice gaps during the walk** (missing concept pages, terms
   referenced repeatedly but never defined), note them in the log entry so
   `/wiki lint` can surface them later.

This is how explorations compound: a question asked once becomes a
searchable, citable page forever.

## The lint operation

`/wiki lint` runs `lsdlc-wiki lint` and explains each finding. When you
review the report:

- **Contradictions** — flagged for human review. Never auto-resolve.
- **Orphan pages** — offer to add them to `index.md` or link them from a
  related page. Per-fix confirmation.
- **Stale pages** — check whether the referenced code has actually changed
  since the `updated:` timestamp. If yes, propose an update.
- **Data gaps / TODO** — surface the question for the user to answer.
- **Broken references** — offer to fix the link or mark the page for
  archival.

**Never silently mutate the wiki during lint.** Every fix requires the user
to say yes.

## Privacy rules (non-negotiable)

This wiki is **committed to the repo and visible to every teammate with
read access**. Never write:

- **Secrets** — API keys, tokens, passwords, OAuth client secrets, database
  URLs with embedded credentials, private keys. `lsdlc-wiki secret-scan`
  catches the common patterns, but your prior matters: never type them in
  the first place.
- **PII** — customer names, email addresses, phone numbers, account IDs,
  personal data from logs or databases.
- **Customer names or vendor identities under NDA.** Describe the
  capability, not the company.
- **Internal URLs** that leak hostnames of staging/prod infra.
- **Sensitive business context** — contract terms, pricing negotiations,
  legal, security incident details that haven't been publicly disclosed.

If you are unsure, **omit it**. A wiki with holes is better than a wiki
that leaks. The human reviewer cannot un-push.

## Tools

- `lsdlc-wiki path` — resolve the effective wiki directory
- `lsdlc-wiki init` — scaffold (idempotent)
- `lsdlc-wiki index-upsert <page> <category> <summary>` — update index.md
- `lsdlc-wiki log-append <kind> <title> [--touched ...]` — append to log.md
- `lsdlc-wiki secret-scan <file>` — exit 3 on hit; call before every write
- `lsdlc-wiki search <query>` — auto-routed grep/qmd search, returns JSON
- `lsdlc-wiki lint` — structural report
- `lsdlc-wiki sync-linear` — one-way push wiki → Linear Project Documents
- `lsdlc-wiki migrate` — import legacy home-dir wiki content into sources/legacy/
- `lsdlc-wiki qmd-setup` — register the wiki as a qmd search collection

For ticket data and Linear operations, use `lsdlc-linear` (ignore the
first-party Linear MCP; these skills don't depend on it).
