# Task Todo

## Objective

Create a VerdictCouncil use case diagram in both PlantUML and Mermaid, save them as separate source files under `diagrams/`, and make the diagrams directly viewable on GitHub.

## Checklist

- [x] Review the architecture reference and extract all user stories from `US-001` to `US-033`
- [x] Create a grouped use case model that stays readable while covering every referenced story
- [x] Add a PlantUML use case diagram source file in `diagrams/`
- [x] Add a Mermaid use case diagram source file in `diagrams/`
- [x] Verify that both files reference every user story from the source document
- [x] Render the PlantUML diagram to SVG for GitHub viewing
- [x] Add a Markdown page in `diagrams/` that embeds the Mermaid diagram and the PlantUML SVG
- [x] Rework actor modeling so only external roles and systems are actors
- [x] Simplify the diagram by grouping story-level details into higher-level judge-facing capabilities
- [x] Record final review notes and verification results

## Review

- Coverage verification passed: `docs/verdictcouncil_architecture.md`, `diagrams/verdictcouncil_use_case.puml`, and `diagrams/verdictcouncil_use_case.mmd` each contain `US-001` through `US-033`.
- PlantUML syntax verification passed with `plantuml -checkonly diagrams/verdictcouncil_use_case.puml`.
- PlantUML SVG rendered successfully to `diagrams/verdictcouncil_use_case.svg` for GitHub display.
- GitHub-friendly diagram page added at `diagrams/README.md`.
- Diagram semantics revised: actors are now limited to the judge and external supporting systems. The 9 AI agents, SAM gateway, Solace broker/audit trail, and JWT/session handling are treated as internal VerdictCouncil components rather than actors.
- Diagram readability improved by collapsing 33 story nodes into 8 higher-level use cases while still referencing every `US-*` item explicitly inside those grouped use cases.
- Mermaid CLI was not installed in the workspace, so Mermaid verification was limited to source inspection and `US-*` coverage cross-checking.

---

## Objective

Align local backend startup so `./dev.sh` and `make dev` launch every currently runnable backend process, including the custom `layer2-aggregator`, while keeping `whatif-controller` documented as an API-internal capability rather than a separate local process.

## Checklist

- [x] Update `configs/services/layer2-aggregator.yaml` to use the real custom aggregator app contract
- [x] Enable `layer2-aggregator` in `Procfile.dev`
- [x] Update `dev.sh` startup messaging to match the real backend process list
- [x] Correct root/backend startup docs that still describe the old service topology
- [x] Run Procfile validation and targeted backend tests
- [x] Run end-to-end startup verification for `./dev.sh`
- [x] Record review notes and verification results

## Review

- `configs/services/layer2-aggregator.yaml` now includes the shared anchor file, points at `src.services.layer2_aggregator.app`, and uses the fields required by `Layer2AggregatorApp` (`namespace`, `service_name`, `response_subscription_topic`, `redis_url`).
- `Procfile.dev` now launches `web-gateway`, 9 SAM agents, `layer2-aggregator`, and `api`, and it uses the backend `.venv` executables plus the installed `solace-agent-mesh run <yaml>` CLI form instead of the stale `python ... --config` form.
- `dev.sh` now describes the real backend topology and refreshes the backend install if `.venv/bin/honcho` is missing, which was necessary because `make dev` depends on honcho but the repo had not declared it in `pyproject.toml`.
- Root and backend startup docs were aligned so `whatif-controller` is documented as API-internal rather than a standalone local process, and the local-dev guide now shows the real Procfile/native SAM invocation.
- Procfile validation passed with `\.venv/bin/honcho -f Procfile.dev check`, which reported `web-gateway, case-processing, complexity-routing, evidence-analysis, fact-reconstruction, witness-analysis, legal-knowledge, argument-construction, deliberation, governance-verdict, layer2-aggregator, api`.
- Targeted tests passed with `\.venv/bin/pytest tests/unit/test_layer2_aggregator_sam_wrapper.py tests/unit/test_mesh_runner.py -v --tb=short` (`25 passed`).
- End-to-end `./dev.sh` verification progressed far enough to prove the updated backend process set starts under honcho and includes `layer2-aggregator`, but the SAM processes still cannot stay up locally because the Solace broker bootstrap is missing beyond the scope of this task. The standalone `layer2-aggregator` command now gets through app wiring and retries broker connection instead of failing on config import.
- Remaining blocker captured during verification: local Docker infra exposes the broker but does not bootstrap the `verdictcouncil` VPN / `vc-agent` user expected by the backend stack. The next failure is in the Solace startup path, not in `dev.sh` service coverage.

---

## Objective

Realign the backend integration contract to the product source of truth in `docs/architecture/01-user-stories.md` and `/Users/douglasswm/Project/AAS/VER/AGENT_ARCHITECTURE.md`, then expose story-aligned APIs that the frontend can consume without inventing its own data model.

## Checklist

- [x] Re-read the user stories and agent architecture to extract the required case, pipeline, workflow, and review concepts
- [x] Expand case intake and case summary/detail schemas only where the user stories and owned `CaseState` fields justify the contract
- [x] Align pipeline stage ordering and progress payloads with the fixed 9-agent architecture
- [x] Standardize escalation, reopen, amendment, and senior-review workflow item shapes around story-aligned fields
- [x] Enrich dossier endpoints with the evidence, fact, witness, statute, precedent, argument, deliberation, verdict, and fairness details required by the user stories
- [x] Add or update backend tests that prove the new contract and workflow semantics
- [x] Record verification results and any residual gaps against the stories and grading docs

## Review

- Source of truth reset: all API changes in this pass were checked back against `docs/architecture/01-user-stories.md` and `/Users/douglasswm/Project/AAS/VER/AGENT_ARCHITECTURE.md`, not against pre-existing frontend assumptions.
- Case intake and summary/detail contracts now persist and expose story-critical metadata including title, filing date, parties, claim amount / SCT consent, offence code, jurisdiction summary, progress, decision history, reopen state, amendment state, and escalation reason.
- Pipeline status and dossier-facing routes now use the fixed 9-agent order from the architecture doc: `case-processing -> complexity-routing -> evidence-analysis -> fact-reconstruction -> witness-analysis -> legal-knowledge -> argument-construction -> deliberation -> governance-verdict`.
- Escalation and senior-review routes now share a story-aligned workflow shape across escalations, reopen requests, and amendments, while hearing-pack and dashboard responses were expanded to expose the fields the user stories actually call for.
- Verification passed with `./.venv/bin/ruff check src tests` and `./.venv/bin/pytest tests/unit/test_cases.py tests/unit/test_persist_case_results.py tests/unit/test_decisions.py tests/unit/test_escalation.py -q` (`39 passed`).
- Residual backend gaps remain where the stories still ask for workflow behavior beyond the current API surface: rejection override / resume (`US-004`), selective re-processing after supplementary uploads (`US-005`), amendment submission / approval workflow (`US-036`), and more complete reopen / senior-inbox action semantics (`US-037`, `US-040`).
