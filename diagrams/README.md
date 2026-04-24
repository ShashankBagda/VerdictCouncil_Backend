# VerdictCouncil Use Case Diagrams

This folder keeps the editable diagram sources and a GitHub-friendly view of the same content.

- Mermaid source: [`verdictcouncil_use_case.mmd`](./verdictcouncil_use_case.mmd)
- PlantUML source: [`verdictcouncil_use_case.puml`](./verdictcouncil_use_case.puml)
- PlantUML render: [`verdictcouncil_use_case.svg`](./verdictcouncil_use_case.svg)

## Modeling Choices

- Actors are limited to external roles and external systems at the VerdictCouncil boundary.
- The 9 AI agents are internal implementation and orchestration components, so they are not modeled as actors in this use case diagram.
- The user stories are grouped into higher-level judge-facing capabilities to keep the diagram readable while still referencing every `US-*` item from the architecture document.

## Mermaid

GitHub renders Mermaid diagrams directly from Markdown, so the diagram is embedded here for visibility.

```mermaid
flowchart LR
  %% VerdictCouncil use case diagram derived from docs/verdictcouncil_architecture.md

  classDef actor fill:#F6F2E8,stroke:#8F6A2A,stroke-width:1.5px,color:#2D2D2D;
  classDef support fill:#EDF4FF,stroke:#3B82F6,stroke-width:1.2px,color:#1F2933;
  classDef system fill:#FFF9E8,stroke:#8F6A2A,stroke-width:1.2px,color:#2D2D2D;
  classDef note fill:#F7FAFC,stroke:#5A6A72,stroke-width:1px,color:#1F2933;

  Judge["Tribunal Magistrate / Judge"]
  OpenAI["OpenAI Platform Services<br/>(Files API, Vector Stores)"]
  Judiciary["Judiciary Search Services<br/>(judiciary.gov.sg, PAIR)"]

  subgraph VerdictCouncil["VerdictCouncil"]
    direction TB
    UC1["Case Intake & Setup<br/>US-001, US-002, US-003, US-004, US-005"]
    UC2["Evidence & Fact Review<br/>US-006, US-007, US-008, US-009, US-010"]
    UC3["Witness Review<br/>US-011, US-012, US-013"]
    UC4["Legal Research<br/>US-014, US-015, US-016, US-017"]
    UC5["Arguments & Deliberation<br/>US-018, US-019, US-020, US-021"]
    UC6["Verdict & Governance<br/>US-022, US-023, US-024, US-025"]
    UC7["Audit, Export & Session<br/>US-026, US-027, US-028, US-029, US-030"]
    UC8["Advanced Review Scenarios<br/>US-031, US-032, US-033"]
    INT["Internal to VerdictCouncil, not actors:<br/>AI Agents 1-9, SAM Web Gateway, Solace broker and audit trail, JWT/session handling."]
  end

  Judge --- UC1
  Judge --- UC2
  Judge --- UC3
  Judge --- UC4
  Judge --- UC5
  Judge --- UC6
  Judge --- UC7
  Judge --- UC8

  OpenAI --- UC1
  OpenAI --- UC4
  Judiciary --- UC4

  class Judge actor
  class OpenAI,Judiciary support
  class UC1,UC2,UC3,UC4,UC5,UC6,UC7,UC8 system
  class INT note
```

## PlantUML

The PlantUML source is kept separately and rendered to SVG for GitHub display.

![VerdictCouncil Use Case Diagram](./verdictcouncil_use_case.svg)
