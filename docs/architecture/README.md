# VerdictCouncil

## Multi-Agent AI Judicial Decision-Support System

NUS Master of Software Engineering | Agentic AI Architecture Module | v7.0 (April 2026)

---

## Executive Summary

**Objective.** VerdictCouncil is a decision-support system for Singapore's lower-court tribunals (Small Claims Tribunal and Traffic Violations). It ingests case documents, produces a structured reasoning chain and fairness audit, and lets a presiding judge stress-test the preliminary conclusion via a what-if simulator. It **never emits a binding verdict** — the judge remains the decision-maker at Gate 4.

**Scope.** In: case intake, evidence/fact/witness analysis, legal-knowledge retrieval (PAIR + curated KB), argument construction, hearing analysis, fairness audit, what-if simulation, stability scoring, judge decision recording, audit trail, PDF export. Admin console for KB management, user management, health monitoring, and cost governance. Out: pre-hearing scheduling, post-decision appeals workflow, cross-case analytics, multi-tribunal federation.

**Solution highlights:**

- **Nine reasoning agents** organised into four layers (case preparation → evidence reconstruction → legal reasoning → hearing preparation & governance), each packaged as its own container Deployment on Kubernetes.
- **Central Orchestrator** built on LangGraph runs the pipeline topology, invokes each agent over HTTPS `POST /invoke` (HMAC-signed), and persists state to Postgres via `AsyncPostgresSaver` after every node — the substrate for crash recovery, audit replay, and what-if rewind.
- **Four-gate HITL** with judge review pauses; `hearing-governance` runs a strict-schema fairness audit before Gate 4.
- **Explainability by construction**: every inference writes an `AuditEntry` (prompt, response, tool calls, model, tokens); MLflow mirrors every run; source anchors link every claim back to the parsed document page.
- **Per-agent privilege separation** and plan-then-execute topology make prompt-injection attacks structurally incapable of redirecting the pipeline or reaching peer agents.

**Constraints and assumptions:**

- Singapore jurisdiction; SCT statutes and Traffic offences are the in-scope legal corpora.
- Higher-court precedents come from the public PAIR Search API (no scraping of `elitigation.sg`); lower-court / tribunal decisions are unpublished and covered by a manually curated domain KB.
- LLM provider is OpenAI; four-tier model strategy (`gpt-5.4-nano` through `gpt-5.4`) keeps per-case cost around $0.40–$0.55.
- Target cloud is DigitalOcean — DOKS for compute, Managed Postgres 16 + Managed Redis 7 for state, DOCR for images, DO Spaces for backups.
- MVP deploys all roles from a single polyvalent image; the canonical per-agent-container topology described here is the assessment deliverable and the production target.

---

## Table of Contents

- [Part 1: User Stories](01-user-stories.md) — condensed MVP scope with Judge + Admin personas.
- [Part 2: System Architecture](02-system-architecture.md) — Orchestrator + agents, graph topology, shared `CaseState`, security model.
- [Part 3: Agent Configurations](03-agent-configurations.md) — nine agents by contract, tool catalog, retry/halt/resume semantics.
- [Part 4: Tech Stack](04-tech-stack.md) — technology matrix, model tiers, key design decisions.
- [Part 5: Diagrams](05-diagrams.md) — ERD (logical), full sequence diagram, physical K8s diagram, class diagram.
- [Part 6: CI/CD Pipeline](06-cicd-pipeline.md) — live CI workflow, staging and production deploys, K8s manifests.
- [Part 7: Contestable Judgment Mode](07-contestable-judgment-mode.md) — what-if engine, stability score, evaluation fixtures.
- [Part 8: Infrastructure Setup (DigitalOcean)](08-infrastructure-setup.md) — one-time DO provisioning.
- [Part 9: Local Development](09-local-development.md) — `make dev`, honcho, docker-compose infra, dev-mode vs production topology.
- [Part 10: Explainable & Responsible AI](10-explainable-responsible-ai.md) — ERAI alignment, fairness, IMDA Model AI Governance mapping.
- [Part 11: AI Security Risk Register](11-ai-security-risk-register.md) — identified risks × mitigations × owners.
- [Part 12: Testing Summary](12-testing-summary.md) — unit, integration, evaluation, SAST/SCA/DAST results.
- **Part 13: Reflection** — deferred; authored closer to submission as part of the group report package, not the standing architecture docs.
- [Appendices](appendices.md) — cost model, environment variables, glossary.
