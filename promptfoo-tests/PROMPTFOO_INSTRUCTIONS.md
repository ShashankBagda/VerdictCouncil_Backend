# Promptfoo Tests — VerdictCouncil

Per-phase prompt regression suite. Lecturer-style: flat layout, one YAML per
phase, deterministic JS assertions only (no LLM-as-judge).

The pipeline phase prompts live in `../prompts/*.md`. Each suite reads one of
those files, drives the LLM directly via the `openai:chat:*` provider, and
asserts on the structured JSON output against the corresponding Pydantic
schema in `../src/pipeline/graph/schemas.py`.

## Project Structure

```
promptfoo-tests/
├── package.json                   # npm scripts to run all or individual suites
├── intake.yaml                    # 10 golden cases
├── research-evidence.yaml         # 1 case (traffic-1) — see Coverage below
├── research-facts.yaml            # 1 case (traffic-1)
├── research-law.yaml              # 1 case (traffic-1)
├── research-witnesses.yaml        # 1 case (traffic-1)
├── synthesis.yaml                 # 1 case (traffic-1)
├── audit.yaml                     # 1 case (traffic-1)
├── fixtures/                      # Hand-authored upstream-context JSONs for traffic-1
│   ├── intake_traffic1.json
│   ├── research_traffic1.json
│   └── synthesis_traffic1.json
├── prompt_builders/
│   ├── _lib.js                    # Shared chat-message builder + fixture loader
│   ├── intake.js                  # Reads ../prompts/intake.md, no upstream context
│   ├── research-{evidence,facts,law,witnesses}.js   # +intake fixture
│   ├── synthesis.js               # +intake +research fixtures
│   └── audit.js                   # +intake +research +synthesis fixtures
└── test_loaders/
    ├── _lib.js                    # Shared golden-case loader
    ├── intake.js                  # Yields all 10 golden cases as tests
    └── <phase>.js                 # 1 test (traffic-1) per downstream phase
```

## Coverage

| Phase | Cases | Why |
|---|---|---|
| intake | 10 | Raw case input is the prompt's only input — every golden case is usable |
| research-{evidence,facts,law,witnesses} | 1 | Each consumes the prior intake output; goldens don't carry per-case `expected.intake` upstream fixtures, so we use one hand-authored fixture for traffic-1 |
| synthesis | 1 | Consumes intake + 4× research; one hand-authored upstream set for traffic-1 |
| audit | 1 | Consumes intake + research + synthesis; one hand-authored upstream set for traffic-1 |

Per-case regression for the 6 downstream phases is intentionally not duplicated
here — `eval.yml` (LangSmith golden-set, end-to-end) is the authoritative gate
for that. This suite catches per-phase prompt-edit regressions cheaply, before
the heavier pipeline eval runs.

## How to Run

From `promptfoo-tests/`:

```bash
# Validate every suite without calling the model
npm run validate

# Run all suites (sequential)
npm run eval

# Run a single suite
npm run eval:intake
npm run eval:research-evidence
npm run eval:research-facts
npm run eval:research-law
npm run eval:research-witnesses
npm run eval:synthesis
npm run eval:audit

# View results in the local UI
npm run view
```

Or directly:

```bash
npx promptfoo@latest eval -c <suite>.yaml --no-cache
```

## Required Env

- `OPENAI_API_KEY` — read by the `openai:chat:*` provider.

## Adding More Cases to a Downstream Phase

To upgrade a downstream phase from single-case to N-case coverage:

1. Author additional `fixtures/intake_<case>.json` (and research/synthesis as
   relevant) for each new golden case.
2. Update the matching `prompt_builders/<phase>.js` to pick the right fixture
   per test (e.g. `vars.case_input.case_id`).
3. Update the matching `test_loaders/<phase>.js` to emit one test per case.

Or — if the underlying golden-case JSONs grow `expected.<phase>` ground-truth
blocks for downstream phases — replace the hand-authored fixtures with
generation from those.

## What this suite covers

Per the standard eval taxonomy:

- **Accuracy / structural correctness**: deterministic JS assertions per phase test the prompt's declared output shape on golden inputs.
- **Groundedness**: one `llm-rubric` per phase checks that outputs cite concrete case details / real Singapore statutes / parties from the source — not fabricated content. Pinned grader (`openai:chat:gpt-4.1-mini`) at threshold 0.8.
- **Cost & latency**: every test asserts `cost ≤ $0.10` and `latency ≤ 45s` (90s for synthesis). Tracked in the per-test result and surfaced in the Job Summary.
- **Quality gate**: `baseline.json` carries per-suite minimum pass-rates. The CI workflow's threshold-gate step fails the matrix job if a suite drops below its threshold. Setting threshold to 0.0 keeps a known-failing suite *visible* (Job Summary still shows the failures) without *blocking* the workflow — useful when the failure is a real prompt-violation finding waiting on a fix.

## What this suite does NOT cover (yet)

- **Security / red-team** — no prompt-injection probes, no jailbreak datasets, no PII detection, no policy-violation assertions. Promptfoo has a `redteam` subcommand and dedicated providers; would live in a separate workflow.
- **RAG retrieval quality** — the pipeline uses PAIR API + per-judge vector stores via `precedent_search` / `knowledge_base/search` endpoints, but this suite drives the model directly with hand-authored fixtures and never touches retrieval. A retrieval-quality dataset (queries → expected docs/citations) would be needed first.
- **Cross-run regression deltas** — the threshold gate compares to a static `baseline.json`, not to the previous run. `eval.yml` (LangSmith) carries that.

## Notes vs Lecturer's Reference

Diverged from the lecturer's pattern only where the codebase forced it:
- Provider is `openai:chat:gpt-4.1-mini` (direct to the model) instead of an
  `https` provider — the VerdictCouncil pipeline runs phases asynchronously
  via an arq worker, so there is no synchronous LLM-driven HTTP endpoint to
  POST against.
- `defaultTest` collapses repeated shape assertions instead of duplicating
  them across every test.
- `--no-cache` on every CI run to avoid stale results when prompts change.
- CI uses a matrix strategy so suites run in parallel and one suite's failure
  doesn't hide another's.
