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
