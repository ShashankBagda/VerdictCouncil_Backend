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
