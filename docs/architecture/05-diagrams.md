# Part 5: Diagrams

This part holds the diagrams that are stable enough to hand to a new engineer. The authoritative DB schema lives in `alembic/versions/`; the authoritative graph topology lives in `src/pipeline/graph/builder.py`. If this file disagrees with either, the code wins.

## 5.1 Entity-Relationship Diagram (logical view)

Only the load-bearing entities and their primary relationships are shown. Tables with only service-plumbing fields (`system_config`, `password_reset_tokens`, `sessions`) are omitted.

```mermaid
erDiagram
    users {
        uuid id PK
        varchar name
        varchar email UK
        enum role "user | judge | senior_judge | admin"
        varchar password_hash
        timestamp created_at
        timestamp updated_at
    }

    cases {
        uuid id PK
        enum domain "small_claims | traffic_violation"
        enum status "pending | processing | awaiting_review_gate1..4 | ready_for_review | escalated | closed | failed | failed_retryable"
        jsonb case_metadata
        uuid created_by FK
        timestamp created_at
        timestamp updated_at
    }

    parties {
        uuid id PK
        uuid case_id FK
        varchar name
        enum role "claimant | respondent | accused | prosecution"
        jsonb contact_info
    }

    documents {
        uuid id PK
        uuid case_id FK
        varchar openai_file_id
        varchar filename
        enum kind "notice_of_traffic_offence | charge_sheet | police_report | witness_statement | speed_camera_record | medical_report | letter_of_mitigation | evidence_bundle | other"
        varchar document_type
        jsonb parsed_content
        uuid uploaded_by FK
        timestamp uploaded_at
    }

    evidence {
        uuid id PK
        uuid case_id FK
        uuid document_id FK
        enum evidence_type "documentary | testimonial | physical | digital | expert"
        enum strength "strong | medium | weak"
        jsonb admissibility_flags
        jsonb linked_claims
    }

    facts {
        uuid id PK
        uuid case_id FK
        date event_date
        text description
        uuid source_document_id FK
        enum status "agreed | disputed"
        float confidence
        jsonb conflicting_versions
    }

    witnesses {
        uuid id PK
        uuid case_id FK
        varchar name
        varchar role
        uuid party_id FK
        int credibility_score
        jsonb bias_indicators
    }

    legal_rules {
        uuid id PK
        uuid case_id FK
        varchar statute_ref
        varchar section
        text verbatim_text
        float relevance_score
        text application_to_facts
    }

    precedents {
        uuid id PK
        uuid case_id FK
        varchar citation
        varchar court
        varchar outcome
        text reasoning_summary
        float similarity_score
        text distinguishing_factors
        enum source "curated | live_search"
    }

    arguments {
        uuid id PK
        uuid case_id FK
        enum side "prosecution | defense | claimant | respondent"
        text legal_basis
        jsonb supporting_evidence
        text weaknesses
        jsonb judicial_questions
    }

    hearing_analyses {
        uuid id PK
        uuid case_id FK
        uuid run_id
        jsonb established_facts
        jsonb applicable_law
        jsonb application
        jsonb argument_evaluation
        jsonb witness_impact
        jsonb precedent_alignment
        text preliminary_conclusion
        int confidence_score
        jsonb uncertainty_flags
        jsonb fairness_check
    }

    hearing_notes {
        uuid id PK
        uuid case_id FK
        uuid author_id FK
        text content
        boolean locked
        timestamp created_at
    }

    what_if_scenarios {
        uuid id PK
        uuid case_id FK
        uuid parent_run_id FK
        varchar modification_type
        jsonb modification_payload
        enum status "pending | running | completed | failed"
        timestamp created_at
    }

    what_if_results {
        uuid id PK
        uuid scenario_id FK
        jsonb modified_verdict
        jsonb diff_view
        float stability_score
        timestamp completed_at
    }

    pipeline_checkpoints {
        uuid id PK
        uuid case_id FK
        uuid run_id
        jsonb checkpoint_blob
        int schema_version
        timestamp created_at
    }

    pipeline_jobs {
        uuid id PK
        uuid case_id FK
        varchar job_type
        enum status "queued | dispatched | running | completed | failed"
        int attempts
        timestamp claimed_at
        timestamp completed_at
    }

    domains {
        uuid id PK
        varchar name
        varchar jurisdiction_code
        varchar vector_store_id
        uuid created_by FK
        timestamp created_at
    }

    domain_documents {
        uuid id PK
        uuid domain_id FK
        varchar openai_file_id
        varchar filename
        enum status "pending | active | retired | rejected"
        timestamp uploaded_at
    }

    audit_logs {
        uuid id PK
        uuid case_id FK
        varchar agent_name
        varchar action
        jsonb input_payload
        jsonb output_payload
        text system_prompt
        jsonb llm_response
        jsonb tool_calls
        varchar model
        jsonb token_usage
        timestamp created_at
    }

    users ||--o{ cases : creates
    users ||--o{ hearing_notes : authors
    cases ||--o{ parties : involves
    cases ||--o{ documents : contains
    cases ||--o{ evidence : has
    documents ||--o{ evidence : supports
    cases ||--o{ facts : establishes
    documents ||--o{ facts : sources
    cases ||--o{ witnesses : involves
    parties ||--o{ witnesses : associated_with
    cases ||--o{ legal_rules : applies
    cases ||--o{ precedents : references
    cases ||--o{ arguments : presents
    cases ||--o{ hearing_analyses : produces
    cases ||--o{ hearing_notes : annotated_by
    cases ||--o{ what_if_scenarios : has
    what_if_scenarios ||--|| what_if_results : produces
    cases ||--o{ pipeline_checkpoints : checkpoints
    cases ||--o{ pipeline_jobs : queues
    cases ||--o{ audit_logs : tracks
    domains ||--o{ domain_documents : contains
    domains ||--o{ cases : classifies
```

---

## 5.2 Sequence Diagram — Full Pipeline Flow

Live pipeline runs are enqueued by the API onto an arq queue and executed by the worker. The graph is a single in-process LangGraph StateGraph; there is no inter-process messaging between agents.

```mermaid
sequenceDiagram
    actor Judge
    participant API as FastAPI (vc-api)
    participant PG as PostgreSQL
    participant RQ as Redis (arq queue)
    participant W as arq worker
    participant GR as GraphRunner
    participant CP as case-processing
    participant CR as complexity-routing
    participant GD as gate2_dispatch
    participant EA as evidence-analysis
    participant FR as fact-reconstruction
    participant WA as witness-analysis
    participant LK as legal-knowledge
    participant GJ as gate2_join
    participant AC as argument-construction
    participant HA as hearing-analysis
    participant HG as hearing-governance
    participant OAI as OpenAI API
    participant PAIR as PAIR Search API
    participant ML as MLflow

    Note over Judge,API: Gate 1 — Case intake
    Judge ->>+ API: POST /cases (multipart upload)
    API ->> OAI: Upload files (Files API)
    OAI -->> API: file_ids[]
    API ->> PG: INSERT case (status=processing), documents, parties
    API ->> PG: INSERT pipeline_jobs (job_type=run_case_pipeline, status=queued)
    API ->> RQ: enqueue run_case_pipeline_job(case_id, run_id)
    API -->>- Judge: 202 Accepted

    Note over W,GR: Worker claims the job
    RQ -->>+ W: dequeue
    W ->> PG: UPDATE pipeline_jobs SET status=running (FOR UPDATE SKIP LOCKED)
    W ->> GR: invoke_graph(case_id, run_id)
    GR ->> ML: pipeline_run(run_id)

    Note over GR,CP: pre_run_guardrail (injection scan)
    GR ->> CP: route to case-processing (start_agent)
    activate CP
    CP ->> OAI: parse_document via Files API (per doc)
    OAI -->> CP: structured content
    CP ->> OAI: ChatOpenAI (gpt-5.4-nano) — triage + classify + jurisdiction
    OAI -->> CP: case_metadata, parties, red_flags
    CP ->> ML: agent_run audit entry
    CP -->> GR: partial state (case.case_metadata, parties, status)
    deactivate CP
    GR ->> PG: checkpoint (AsyncPostgresSaver)

    Note over GR,CR: complexity-routing
    GR ->> CR: invoke
    activate CR
    CR ->> OAI: ChatOpenAI (gpt-5.4-nano) — complexity tier + route decision
    OAI -->> CR: complexity_tier, route_decision
    CR -->> GR: partial state
    deactivate CR

    alt route = escalate_human
        GR ->> PG: checkpoint, set halt
        GR -->> W: route to terminal → END
    else route = proceed*
        GR ->> GD: gate2_dispatch (no LLM)
    end

    Note over GD,GJ: Gate 2 — parallel fan-out
    GD -->> GR: dispatch into 4 parallel agents
    par evidence-analysis
        GR ->> EA: invoke
        EA ->> OAI: parse_document + cross_reference
        EA ->> OAI: ChatOpenAI (gpt-5)
        OAI -->> EA: evidence_analysis
        EA -->> GR: partial state
    and fact-reconstruction
        GR ->> FR: invoke
        FR ->> OAI: ChatOpenAI (gpt-5) + timeline_construct tool
        OAI -->> FR: extracted_facts, timeline
        FR -->> GR: partial state
    and witness-analysis
        GR ->> WA: invoke
        WA ->> OAI: ChatOpenAI (gpt-5-mini) + generate_questions tool
        OAI -->> WA: witnesses
        WA -->> GR: partial state
    and legal-knowledge
        GR ->> LK: invoke
        LK ->> PAIR: search_precedents (circuit-breaker guarded)
        PAIR -->> LK: higher-court decisions
        LK ->> OAI: search_domain_guidance (vector store)
        OAI -->> LK: domain rules
        LK ->> OAI: ChatOpenAI (gpt-5)
        OAI -->> LK: legal_rules, precedents
        LK -->> GR: partial state
    end

    Note over GR,GJ: gate2_join — fan-in barrier
    GR ->> GJ: invoke (after all 4 complete, reducer merges)
    alt retry required
        GJ -->> GR: loop to {agent} with extra_instructions[agent]
        Note over GR: retry_counts++; cap enforced
    else advance
        GJ -->> GR: proceed to argument-construction
    end

    GR ->> AC: argument-construction
    activate AC
    AC ->> OAI: ChatOpenAI (gpt-5.4) + confidence_calc tool
    OAI -->> AC: arguments
    AC -->> GR: partial state
    deactivate AC

    GR ->> HA: hearing-analysis
    activate HA
    HA ->> OAI: ChatOpenAI (gpt-5.4)
    OAI -->> HA: HearingAnalysis (preliminary_conclusion, etc.)
    HA -->> GR: partial state
    deactivate HA

    GR ->> HG: hearing-governance (Gate 4)
    activate HG
    HG ->> OAI: ChatOpenAI (gpt-5.4, strict schema)
    OAI -->> HG: FairnessCheck
    HG -->> GR: partial state
    deactivate HG

    alt critical_issues_found
        GR ->> PG: checkpoint, set halt=fairness_audit, status=escalated
        GR -->> W: terminal → END
    else audit_passed
        GR ->> PG: checkpoint, status=ready_for_review
        GR -->> W: terminal → END
    end

    W ->> PG: UPDATE pipeline_jobs SET status=completed
    W -->>- RQ: ack

    Note over Judge,API: Gate 4 — judge records the decision (no AI verdict)
    Judge ->>+ API: POST /cases/{id}/decision
    API ->> PG: UPDATE case SET judicial_decision, status=closed
    API -->>- Judge: 200 OK
```

---

## 5.3 Physical Architecture Diagram

Single Docker image, two runtime flavours (`api` and `arq-worker`). All state lives in DO Managed Postgres + DO Managed Redis; no pod-local persistence required.

```mermaid
flowchart TB
    subgraph External["External Services"]
        OAI["OpenAI API<br/>api.openai.com"]
        PAIR["PAIR Search API<br/>search.pair.gov.sg"]
    end

    subgraph Managed["DigitalOcean Managed Services"]
        PG["Managed PostgreSQL 16<br/>verdictcouncil"]
        RD["Managed Redis 7"]
    end

    subgraph K8s["Kubernetes Cluster — namespace: verdictcouncil"]
        ING["NGINX Ingress<br/>HTTPS :443"]

        subgraph Runtime["Application Runtime"]
            API["vc-api (Deployment)<br/>uvicorn src.api.app:app<br/>:8001<br/>HPA on CPU/RPS"]
            WRK["vc-arq-worker (Deployment)<br/>arq src.workers.worker_settings<br/>HPA on queue depth"]
        end

        subgraph Observability["Observability"]
            ML["MLflow tracking server<br/>:5001"]
            PROM["Prometheus scrape<br/>/metrics on :8001"]
        end

        subgraph Jobs["One-shot Jobs"]
            MIG["alembic-migrate (Job)"]
            WATCH["stuck-case-watchdog (CronJob)"]
        end

        subgraph Services["ClusterIP Services"]
            APISVC["vc-api-svc"]
            MLSVC["mlflow-svc"]
        end
    end

    ING -->|HTTPS| APISVC
    APISVC --> API

    API --> PG
    API --> RD
    API --> MLSVC
    API -.->|HTTPS| OAI

    WRK --> PG
    WRK --> RD
    WRK --> MLSVC
    WRK -.->|HTTPS| OAI
    WRK -.->|HTTPS| PAIR

    MLSVC --> ML
    MIG --> PG
    WATCH --> PG
    WATCH --> RD
```

**Notes:**

- The API and worker ship from the same image. The Procfile in dev runs both locally via honcho; in K8s they're two separate Deployments with different `command` overrides.
- The worker reaches the PAIR API; the API does not (precedent search only happens inside `legal-knowledge`, which runs in the worker).
- MLflow is optional in local dev (`MLFLOW_ENABLED=false` by default); in production it's an internal ClusterIP.
- There is no message broker, no per-agent pod, and no fan-in aggregator service. All nine agents run in-process inside the arq worker as LangGraph nodes.

---

## 5.4 Class Diagram — Graph nodes, state, tools, services

Agent "classes" from the previous SAM-era design are now `async def` node functions. The diagram captures the type relationships that matter at runtime: the graph state, the per-agent wrapper, the tool implementations, and the domain services used by the API.

```mermaid
classDiagram
    class GraphState {
        <<TypedDict>>
        +case: CaseState
        +run_id: str
        +extra_instructions: dict~str, str~
        +retry_counts: dict~str, int~
        +halt: dict|None
        +mlflow_run_ids: dict~str, tuple~
        +is_resume: bool
        +start_agent: str|None
    }

    class CaseState {
        <<Pydantic>>
        +schema_version: int
        +case_id: str
        +run_id: str
        +parent_run_id: str|None
        +domain: CaseDomainEnum|None
        +status: CaseStatusEnum
        +parties: list~dict~
        +case_metadata: dict
        +raw_documents: list~dict~
        +evidence_analysis: EvidenceAnalysis
        +extracted_facts: ExtractedFacts
        +witnesses: Witnesses
        +legal_rules: list~dict~
        +precedents: list~dict~
        +arguments: dict
        +hearing_analyses: list~HearingAnalysis~
        +judicial_decision: dict
        +audit_log: list~AuditEntry~
    }

    class AgentNode {
        <<function>>
        +async(state) partial_state
    }

    class _run_agent_node {
        <<shared wrapper>>
        +agent_run_mlflow()
        +resolve_model()
        +bind_tools()
        +invoke_chat_openai()
        +validate_output()
        +append_audit_entry()
    }

    class GraphBuilder {
        +build_graph(checkpointer) StateGraph
    }

    class AsyncPostgresSaver {
        <<langgraph-checkpoint-postgres>>
        +aput(state)
        +aget(thread_id) state
    }

    class ToolFactory {
        +make_tools(case_state) list~Tool~
    }

    class ParseDocument
    class CrossReference
    class TimelineConstruct
    class GenerateQuestions
    class ConfidenceCalc
    class SearchPrecedents
    class SearchDomainGuidance

    class CircuitBreaker {
        +state: open|half_open|closed
        +call(fn)
        +record_success()
        +record_failure()
    }

    class GraphPipelineRunner {
        +invoke(case_id, run_id, start_agent)
        +run_from(start_agent)
    }

    class WhatIfController {
        +run_scenario(scenario)
        +_apply_modification(case_state, mod)
        +_determine_re_entry(mod_type) str
    }

    class DiffEngine {
        +diff(original_state, modified_state) dict
    }

    class CaseService
    class AuditService
    class AuthService

    GraphState *-- CaseState
    AgentNode ..> _run_agent_node : delegates
    _run_agent_node ..> ToolFactory : binds tools
    _run_agent_node ..> CaseState : reads/writes
    GraphBuilder --> AgentNode : registers
    GraphBuilder --> AsyncPostgresSaver : attaches
    GraphPipelineRunner ..> GraphBuilder : uses

    ToolFactory --> ParseDocument
    ToolFactory --> CrossReference
    ToolFactory --> TimelineConstruct
    ToolFactory --> GenerateQuestions
    ToolFactory --> ConfidenceCalc
    ToolFactory --> SearchPrecedents
    ToolFactory --> SearchDomainGuidance
    SearchPrecedents --> CircuitBreaker : guarded by

    WhatIfController ..> GraphPipelineRunner : re-enters via start_agent
    WhatIfController ..> DiffEngine : composes

    CaseService ..> CaseState : projects to DB
    AuditService ..> CaseState : reads audit_log
```

**Layer boundaries:**

- **Graph layer** (`src/pipeline/graph/`) — pure compute. Reads/writes `GraphState` only. No DB, no HTTP side-effects outside of declared tools.
- **Tool layer** (`src/tools/`) — narrow, single-responsibility callables. The only place outbound HTTP lives.
- **Service layer** (`src/services/`) — orchestration glue: `WhatIfController`, `GraphPipelineRunner`, and domain services that the API calls. Crosses the graph boundary via `GraphPipelineRunner`.
- **API layer** (`src/api/`) — FastAPI routers, request/response models, cookie auth, rate-limit middleware. Never calls an agent directly.
- **Data layer** (`src/models/`, `src/db/`) — SQLAlchemy projections of `CaseState` plus tables that live outside the graph (hearing notes, what-if scenarios, audit logs, pipeline jobs).

---
