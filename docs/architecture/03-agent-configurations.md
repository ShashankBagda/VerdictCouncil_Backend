# Part 3: Agent Configurations (SAM YAML)

---

## 3.1 Layer 2 Aggregator

### YAML Configuration

```yaml
# configs/services/layer2-aggregator.yaml
!include ../shared_config.yaml

apps:
  - name: layer2-aggregator
    app_module: src.services.layer2_aggregator.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      service_name: layer2-aggregator
      response_subscription_topic: "${NAMESPACE}/a2a/v1/agent/response/layer2-aggregator/>"
      redis_url: ${REDIS_URL}
```

### Python Implementation

```python
# services/layer2_aggregator/aggregator.py
"""Layer 2 Fan-In Aggregator for VerdictCouncil.

Collects outputs from Agents 3, 4, and 5. Merges them into a unified
CaseState and publishes to Agent 6 only when all three have completed.
"""

import json
import time
from typing import Any

import redis


class Layer2Aggregator:
    """Stateful barrier that waits for 3 agent outputs before forwarding.

    The Redis key includes both case_id and run_id to isolate concurrent
    pipeline executions (e.g., what-if scenario runs) from each other.
    """

    REQUIRED_AGENTS = frozenset([
        "evidence_analysis",
        "extracted_facts",
        "witnesses",
    ])
    TIMEOUT_SECONDS = 120

    def __init__(self, redis_client: redis.Redis, key_prefix: str = "vc:aggregator:"):
        self.redis = redis_client
        self.prefix = key_prefix

    def _key(self, case_id: str, run_id: str) -> str:
        """Redis key scoped to both case and run to isolate concurrent executions."""
        return f"{self.prefix}{case_id}:{run_id}"

    def receive_output(
        self, case_id: str, run_id: str, agent_key: str, payload: dict
    ) -> dict | None:
        """Store an agent's output. Returns merged CaseState if barrier is met.

        Uses a Redis Lua script for atomic check-and-publish to prevent
        duplicate publishes when multiple agents complete near-simultaneously.

        Args:
            case_id: The case identifier.
            run_id: UUID for this pipeline execution (or scenario_id for what-if runs).
            agent_key: One of 'evidence_analysis', 'extracted_facts', 'witnesses'.
            payload: The agent's output CaseState fragment.

        Returns:
            Merged CaseState dict if all 3 agents have completed, else None.
        """
        if agent_key not in self.REQUIRED_AGENTS:
            raise ValueError(f"Unknown agent_key: {agent_key}")

        key = self._key(case_id, run_id)

        # Atomic check-and-publish via Lua script to prevent race conditions.
        # The script stores the agent output, checks completeness, and sets a
        # "published" flag atomically — ensuring exactly-once publish semantics.
        lua_script = """
        redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
        redis.call('HSET', KEYS[1], ARGV[1] .. '_ts', ARGV[3])
        if redis.call('EXISTS', KEYS[2]) == 0 then
            redis.call('SET', KEYS[2], ARGV[3])
            redis.call('EXPIRE', KEYS[2], 300)
        end
        -- Store original CaseState on first receipt
        if redis.call('HEXISTS', KEYS[1], '_original_case_state') == 0 then
            redis.call('HSET', KEYS[1], '_original_case_state', ARGV[4])
        end
        -- Check if all required agents have reported
        local fields = redis.call('HKEYS', KEYS[1])
        local agent_count = 0
        for _, f in ipairs(fields) do
            if not string.find(f, '_ts$') and f ~= '_original_case_state' and f ~= '_published' then
                agent_count = agent_count + 1
            end
        end
        if agent_count >= 3 and redis.call('HEXISTS', KEYS[1], '_published') == 0 then
            redis.call('HSET', KEYS[1], '_published', '1')
            return 1
        end
        return 0
        """
        ready = self.redis.eval(
            lua_script,
            2,
            key,
            key + ":created",
            agent_key,
            json.dumps(payload),
            str(time.time()),
            json.dumps(payload),  # original CaseState from first agent's full payload
        )

        if ready == 1:
            return self._merge_and_cleanup(case_id, run_id)

        return None

    def check_timeout(self, case_id: str, run_id: str) -> None:
        """Check if the barrier has timed out. Halts pipeline on timeout.

        Unlike the previous design which published partial results, timeout
        now sets the case status to FAILED and logs which agents did not
        complete. Partial CaseState is never forwarded to downstream agents.
        """
        key = self._key(case_id, run_id)
        created_raw = self.redis.get(key + ":created")
        if not created_raw:
            return None

        created = float(created_raw)
        if time.time() - created > self.TIMEOUT_SECONDS:
            # Determine which agents are missing
            all_data = self.redis.hgetall(key)
            stored_agents = {
                k.decode() if isinstance(k, bytes) else k
                for k in all_data.keys()
                if not (k.decode() if isinstance(k, bytes) else k).endswith("_ts")
                and (k.decode() if isinstance(k, bytes) else k) not in (
                    "_original_case_state", "_published"
                )
            }
            missing = self.REQUIRED_AGENTS - stored_agents

            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                "Layer2Aggregator TIMEOUT for case_id=%s run_id=%s. "
                "Missing agents: %s. Setting case status to FAILED.",
                case_id, run_id, list(missing),
            )

            # Cleanup Redis state — do NOT publish partial results
            self.redis.delete(key)
            self.redis.delete(key + ":created")

            # Caller is responsible for updating case status to FAILED
            # in the database and notifying the gateway.
            return None

        return None

    def _merge_and_cleanup(
        self, case_id: str, run_id: str, partial: bool = False
    ) -> dict:
        """Merge agent outputs into the original CaseState (not a bare dict).

        Deep-copies the original CaseState received at pipeline entry, then
        updates only the three designated fields (evidence_analysis,
        extracted_facts, witnesses) from the agent outputs. All other
        CaseState fields (case_id, domain, parties, raw_documents, etc.)
        are preserved.
        """
        import copy

        key = self._key(case_id, run_id)
        all_data = self.redis.hgetall(key)

        # Recover the original CaseState stored on first receipt
        original_raw = all_data.get(
            b"_original_case_state" if isinstance(
                list(all_data.keys())[0], bytes
            ) else "_original_case_state"
        )
        if original_raw:
            original_case_state = json.loads(original_raw)
        else:
            original_case_state = {}

        # Deep-copy to avoid mutating cached data
        merged = copy.deepcopy(original_case_state)

        # Merge only the designated agent output fields into the full CaseState
        for agent_key in self.REQUIRED_AGENTS:
            raw = all_data.get(
                agent_key.encode() if isinstance(
                    list(all_data.keys())[0], bytes
                ) else agent_key
            )
            if raw:
                fragment = json.loads(raw)
                # Update the designated CaseState field with the agent's output
                merged[agent_key] = fragment.get(agent_key, fragment)

        # Cleanup
        self.redis.delete(key)
        self.redis.delete(key + ":created")

        return merged
```

---

## 3.2 shared_config.yaml

```yaml
# configs/shared_config.yaml
# Shared YAML anchors referenced by all agent configuration files.
# Usage: each agent config includes this file and references anchors with <<: *anchor_name

# ──────────────────────────────────────────────
# Model Definitions (OpenAI via LiteLLM)
# ──────────────────────────────────────────────

models:
  gpt54: &gpt54_model
    model: ${OPENAI_MODEL_FRONTIER_REASONING}
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

  gpt5: &gpt5_model
    model: ${OPENAI_MODEL_STRONG_REASONING}
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

  gpt5_mini: &gpt5_mini_model
    model: ${OPENAI_MODEL_EFFICIENT_REASONING}
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

  gpt54_nano: &gpt54_nano_model
    model: ${OPENAI_MODEL_LIGHTWEIGHT}
    api_key: ${OPENAI_API_KEY}
    api_base: https://api.openai.com/v1

# ──────────────────────────────────────────────
# Solace Event Broker Connection
# ──────────────────────────────────────────────

broker: &broker_connection
  broker_url: ${SOLACE_BROKER_URL}
  broker_vpn: ${SOLACE_BROKER_VPN}
  username: ${SOLACE_BROKER_USERNAME}
  password: ${SOLACE_BROKER_PASSWORD}

# ──────────────────────────────────────────────
# Artifact Service (Filesystem)
# ──────────────────────────────────────────────

artifact_service: &default_artifact_service
  type: filesystem
  base_path: /tmp/verdictcouncil
```

---

## 3.3 Agent 1: Case Processing

```yaml
# configs/agents/case-processing.yaml
apps:
  - name: case-processing
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "CaseProcessing"
      display_name: "Case Processing Agent"
      model:
        <<: *gpt54_nano_model
      instruction: |
        You are the Case Processing Agent for VerdictCouncil, a judicial decision-support system for Singapore lower courts.

        TASK: Process a new case submission through 4 sequential steps.

        STEP 1 - INTAKE: Parse all submitted documents and extract:
          - Parties: names, roles (claimant/respondent or accused/prosecution)
          - Case summary: 2-3 sentence plain language description
          - Claim/offence: type, monetary value (SCT), offence code (traffic)
          - Evidence inventory: each document with type and description

        STEP 2 - STRUCTURE: Normalize into universal case schema:
          - Map dispute to category (SCT: sale_of_goods, provision_of_services, property_damage, tenancy | Traffic: speeding, red_light, etc.)
          - Link each evidence item to the party that submitted it
          - Identify agreed vs disputed issues from the submissions

        STEP 3 - CLASSIFY DOMAIN: Based on structured data, output:
          - domain: 'small_claims' or 'traffic_violation'

        STEP 4 - VALIDATE JURISDICTION:
          - SCT: claim <= $20,000 (or $30,000 with consent), within 2 years
          - Traffic: valid offence code, not time-barred
          - Output: jurisdiction_valid (bool), jurisdiction_issues (list)

        CONSTRAINTS:
        - Extract ONLY what is explicitly stated. Flag missing info as MISSING.
        - If jurisdiction fails, set status to 'REJECTED' with specific reasons.
        - Output the complete structured CaseState fields as JSON.

        GUARDRAILS:
        - Must not infer facts beyond what documents state. Flag gaps, do not fill them.
        - Jurisdiction rejection must cite the specific statutory limit violated.
        - Must handle both formal legal filings and informal self-represented submissions.
      tools:
        - name: parse_document
          type: python
          module: tools.parse_document
          function: parse_document
          description: "Parse uploaded documents via OpenAI Files API. Extracts text, tables, and metadata from legal filings."
          parameters:
            - name: file_id
              type: string
              description: "OpenAI File ID of the uploaded document"
              required: true
            - name: extract_tables
              type: boolean
              description: "Whether to extract tabular data from the document"
              required: false
              default: true
            - name: ocr_enabled
              type: boolean
              description: "Whether to enable OCR for scanned documents"
              required: false
              default: false
      agent_card:
        description: "Processes new case submissions through intake, structuring, domain classification, and jurisdiction validation."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text", "file"]
        skills:
          - id: "case_processing"
            name: "Case Processing"
            description: "Parse case documents, structure case data, classify domain, and validate jurisdiction for Singapore lower courts."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.4 Agent 2: Complexity & Routing

```yaml
# configs/agents/complexity-routing.yaml
apps:
  - name: complexity-routing
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "ComplexityRouting"
      display_name: "Complexity & Routing Agent"
      model:
        <<: *gpt54_nano_model
      instruction: |
        You are the Complexity & Routing Agent for VerdictCouncil.

        TASK: Assess case complexity and decide the processing route.

        EVALUATE:
        1. Number of parties and evidence items
        2. Legal novelty: does this raise unusual legal questions?
        3. Cross-jurisdictional or multi-statute complexity
        4. Potential for significant precedent-setting impact
        5. Presence of vulnerable parties (minors, elderly)

        OUTPUT:
        - complexity: 'low' | 'medium' | 'high'
        - route: 'proceed_automated' | 'proceed_with_review' | 'escalate_human'
        - reasoning: brief justification

        CONSTRAINT: When in doubt, route to 'proceed_with_review'. Judicial oversight is always preferred over autonomous processing.

        GUARDRAILS:
        - Must default to escalation for potential precedent-setting cases.
        - Must flag vulnerable parties for additional safeguards.
        - This is the first HALT point: if escalate_human, pipeline stops here.
      tools: []
      agent_card:
        description: "Assesses case complexity and determines whether the case should proceed through automated analysis, require additional review, or be escalated to a human judicial officer."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "complexity_routing"
            name: "Complexity & Routing"
            description: "Evaluate case complexity across multiple dimensions and determine the appropriate processing route."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.5 Agent 3: Evidence Analysis

```yaml
# configs/agents/evidence-analysis.yaml
apps:
  - name: evidence-analysis
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "EvidenceAnalysis"
      display_name: "Evidence Analysis Agent"
      model:
        <<: *gpt5_model
      instruction: |
        You are the Evidence Analysis Agent for VerdictCouncil. You serve the presiding judicial officer with IMPARTIAL analysis.

        TASK: Analyze ALL submitted evidence comprehensively.

        FOR EACH EVIDENCE ITEM:
        1. Use parse_document to extract content.
        2. Classify: documentary | testimonial | physical | digital | expert.
        3. Assess STRENGTH: strong | medium | weak (with reasoning).
        4. Assess ADMISSIBILITY RISK: flag hearsay, expired certifications, authentication issues, chain-of-custody gaps.
        5. Link to specific claims/charges it supports or undermines.

        CROSS-DOCUMENT ANALYSIS:
        6. Use cross_reference to find CONTRADICTIONS between documents.
        7. Identify GAPS: what evidence is expected but missing?
        8. Identify CORROBORATIONS: which items mutually reinforce?

        CONSTRAINTS:
        - NEUTRAL. Assess evidence from both parties with equal rigor.
        - Do NOT determine guilt, liability, or verdict.
        - Cite specific document/page/paragraph for every assessment.

        GUARDRAILS:
        - Must not express opinions on guilt, liability, or outcome.
        - Must flag ALL contradictions, even seemingly minor ones.
        - Must assess both parties' evidence with identical rigor.
      tools:
        - name: parse_document
          type: python
          module: tools.parse_document
          function: parse_document
          description: "Parse uploaded documents via OpenAI Files API. Extracts text, tables, and metadata from legal filings."
          parameters:
            - name: file_id
              type: string
              description: "OpenAI File ID of the uploaded document"
              required: true
            - name: extract_tables
              type: boolean
              description: "Whether to extract tabular data from the document"
              required: false
              default: true
            - name: ocr_enabled
              type: boolean
              description: "Whether to enable OCR for scanned documents"
              required: false
              default: false
        - name: cross_reference
          type: python
          module: tools.cross_reference
          function: cross_reference
          description: "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items."
          parameters:
            - name: segments
              type: array
              description: "List of document segments to compare. Each segment: {doc_id, text, page, paragraph}"
              required: true
            - name: check_type
              type: string
              description: "Type of cross-reference check: 'contradiction' | 'corroboration' | 'all'"
              required: true
      agent_card:
        description: "Performs comprehensive, impartial analysis of all submitted evidence including classification, strength assessment, admissibility risk evaluation, and cross-document analysis."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text", "file"]
        skills:
          - id: "evidence_analysis"
            name: "Evidence Analysis"
            description: "Analyze all submitted evidence for strength, admissibility, contradictions, gaps, and corroborations."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.6 Agent 4: Fact Reconstruction

```yaml
# configs/agents/fact-reconstruction.yaml
apps:
  - name: fact-reconstruction
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "FactReconstruction"
      display_name: "Fact Reconstruction Agent"
      model:
        <<: *gpt5_model
      instruction: |
        You are the Fact Reconstruction Agent for VerdictCouncil.

        TASK: Extract facts and build a sourced, chronological timeline.

        FOR EACH FACT:
        1. Extract: date/time, event description, parties involved, location.
        2. Source: document reference (ID, page, paragraph).
        3. Corroboration: other documents supporting or contradicting this fact.
        4. Confidence: high (multiple sources) | medium (single source) | low (uncorroborated) | disputed (conflicting sources).
        5. Status: agreed (both parties accept) | disputed (contested).

        Use timeline_construct to build the chronological sequence.

        CONSTRAINTS:
        - Include facts from ALL parties equally.
        - Mark DISPUTED facts clearly with both parties' versions.
        - Do NOT resolve factual disputes. Present both sides.

        GUARDRAILS:
        - Must not resolve disputed facts. Present both versions.
        - Must include source references for every extracted fact.
        - Must flag low-confidence facts for judicial attention.
      tools:
        - name: parse_document
          type: python
          module: tools.parse_document
          function: parse_document
          description: "Parse uploaded documents via OpenAI Files API. Extracts text, tables, and metadata from legal filings."
          parameters:
            - name: file_id
              type: string
              description: "OpenAI File ID of the uploaded document"
              required: true
            - name: extract_tables
              type: boolean
              description: "Whether to extract tabular data from the document"
              required: false
              default: true
            - name: ocr_enabled
              type: boolean
              description: "Whether to enable OCR for scanned documents"
              required: false
              default: false
        - name: timeline_construct
          type: python
          module: tools.timeline_construct
          function: timeline_construct
          description: "Build a chronological timeline from extracted events. Handles date normalization, ordering, and conflict detection."
          parameters:
            - name: events
              type: array
              description: "List of events to order. Each event: {date, description, source_ref, parties, location}"
              required: true
        - name: cross_reference
          type: python
          module: tools.cross_reference
          function: cross_reference
          description: "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items."
          parameters:
            - name: segments
              type: array
              description: "List of document segments to compare. Each segment: {doc_id, text, page, paragraph}"
              required: true
            - name: check_type
              type: string
              description: "Type of cross-reference check: 'contradiction' | 'corroboration' | 'all'"
              required: true
      agent_card:
        description: "Extracts facts from case evidence and constructs a sourced, chronological timeline with confidence assessments and dispute identification."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text", "file"]
        skills:
          - id: "fact_reconstruction"
            name: "Fact Reconstruction"
            description: "Extract facts from evidence, assess confidence, identify disputes, and build a chronological timeline."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.7 Agent 5: Witness Analysis

```yaml
# configs/agents/witness-analysis.yaml
apps:
  - name: witness-analysis
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "WitnessAnalysis"
      display_name: "Witness Analysis Agent"
      model:
        <<: *gpt5_mini_model
      instruction: |
        You are the Witness Analysis Agent for VerdictCouncil.

        TASK: Complete witness analysis in 3 phases.

        PHASE 1 - IDENTIFICATION:
        For each potential witness found in case materials:
          - Name, role (police officer, party, bystander, expert).
          - Relationship to the case and which party they support.
          - Whether a formal written statement exists.
          - Potential bias indicators.

        PHASE 2 - TESTIMONY ANTICIPATION (traffic cases only):
        For each identified witness with a written statement:
          - Summarize their likely testimony based STRICTLY on the statement.
          - Identify strong points and areas vulnerable to challenge.
          - Note conflicts between their statement and documentary evidence.
          Mark all output: 'Simulated - For Judicial Preparation Only'.

        PHASE 3 - CREDIBILITY ASSESSMENT:
        For each witness, score credibility (0-100) based on:
          - Internal consistency (self-contradictions).
          - External consistency (alignment with physical/documentary evidence).
          - Bias indicators (employment, financial interest, relationships).
          - Specificity and verifiability of claims.
          - Corroboration by other witnesses.

        NOTE: Credibility scores are relative indicators produced by LLM reasoning,
        not statistically calibrated measurements. They should be interpreted as
        directional signals (higher = stronger support) rather than precise probabilities.

        For SCT cases: assess credibility of BOTH the Claimant's and Respondent's statements using the same criteria.

        CONSTRAINTS:
        - Assess ALL witnesses with equal rigor regardless of which side.
        - Testimony simulation must NOT fabricate beyond written statements.
        - Credibility concerns must cite specific evidence, not suspicion.

        GUARDRAILS:
        - Must assess all witnesses equally regardless of which party called them.
        - Testimony simulation output must be marked as simulated preparation material.
        - Must not make ultimate credibility determinations — the Judge decides.
      tools:
        - name: cross_reference
          type: python
          module: tools.cross_reference
          function: cross_reference
          description: "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items."
          parameters:
            - name: segments
              type: array
              description: "List of document segments to compare. Each segment: {doc_id, text, page, paragraph}"
              required: true
            - name: check_type
              type: string
              description: "Type of cross-reference check: 'contradiction' | 'corroboration' | 'all'"
              required: true
        - name: generate_questions
          type: python
          module: tools.generate_questions
          function: generate_questions
          description: "Generate suggested judicial questions based on argument analysis and identified weaknesses."
          parameters:
            - name: argument_summary
              type: string
              description: "Summary of the argument or testimony to generate questions for"
              required: true
            - name: weaknesses
              type: array
              description: "List of identified weaknesses or gaps to probe"
              required: true
            - name: question_types
              type: array
              description: "Types of questions to generate: 'clarification' | 'challenge' | 'exploration' | 'credibility'"
              required: false
              default: ["clarification", "challenge"]
            - name: max_questions
              type: integer
              description: "Maximum number of questions to generate"
              required: false
              default: 5
      agent_card:
        description: "Identifies witnesses, anticipates testimony for traffic cases, and assesses credibility across all witnesses with equal rigor."
        defaultInputModes: ["text", "file"]
        defaultOutputModes: ["text"]
        skills:
          - id: "witness_analysis"
            name: "Witness Analysis"
            description: "Identify witnesses, anticipate testimony, and assess credibility with bias detection."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.8 Agent 6: Legal Knowledge

```yaml
# configs/agents/legal-knowledge.yaml
apps:
  - name: legal-knowledge
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "LegalKnowledge"
      display_name: "Legal Knowledge Agent"
      model:
        <<: *gpt5_model
      instruction: |
        You are the Legal Knowledge Agent for VerdictCouncil.

        TASK: Retrieve applicable law and relevant precedents.

        PART A - STATUTORY RULES:
        1. Formulate semantic queries from the case facts and dispute issues.
        2. Use file_search to retrieve relevant statutes and regulations.
        3. For each rule: statute name, section, verbatim text, relevance score, and how it applies to the specific case facts.

        KNOWLEDGE BASE (by domain):
          SCT: Small Claims Tribunals Act, Consumer Protection (Fair Trading) Act, Sale of Goods Act, Supply of Services Act.
          Traffic: Road Traffic Act, Road Traffic Rules, Motor Vehicles Act.

        PART B - PRECEDENT CASES:
        Step 1: Use file_search to find cases with matching fact patterns from
                the curated vector store. This is the PRIMARY source for domain-
                specific content (statutes, curated lower court summaries).
        Step 2: Use search_precedents to query PAIR for binding higher court
                authority. Follow the QUERY STRATEGY below.
        Step 3: For each precedent: citation, outcome, reasoning summary,
                similarity score, distinguishing factors, and source
                (curated or live_search).

        ════════════════════════════════════════════════════════════════
        PAIR SEARCH — COURT COVERAGE & QUERY STRATEGY
        ════════════════════════════════════════════════════════════════

        WHAT PAIR COVERS:
        The search_precedents tool queries the PAIR Search API, which indexes
        published judgments from Singapore's higher courts on eLitigation:
          SGHC (High Court), SGCA (Court of Appeal), SGHCF (Family Division),
          SGHCR (General Division), SGHC(I) (SICC), SGHC(A) (Appellate Division),
          SGCA(I) (Court of Appeal - SICC).

        WHAT PAIR DOES NOT COVER:
        Small Claims Tribunals (SCT) and lower State Courts (District Court,
        Magistrate Court). SCT proceedings are informal and do not produce
        published written grounds. Lower traffic court decisions are similarly
        unpublished.

        WHY PAIR IS STILL ESSENTIAL:
        Higher court rulings are BINDING on lower courts. When SCT or traffic
        court decisions are appealed, the High Court writes published grounds
        that interpret the same statutes in the same types of disputes. A SGHC
        ruling on SOGA s.14 ("satisfactory quality") directly governs how every
        SCT case involving defective goods must be decided.

        TWO-TIER PRECEDENT STRATEGY:
        ┌─────────────────────────────────────────────────────────────┐
        │ Tier 1: Curated Vector Store (file_search)                 │
        │   → Statutes verbatim (SCTA, RTA, SOGA, CPFTA)            │
        │   → Manually curated case summaries & sentencing tables    │
        │   → Always searched FIRST                                  │
        ├─────────────────────────────────────────────────────────────┤
        │ Tier 2: PAIR Search API (search_precedents)                │
        │   → Binding higher court authority interpreting statutes    │
        │   → Sentencing benchmarks & appeal outcomes                │
        │   → Searched SECOND to supplement curated results          │
        └─────────────────────────────────────────────────────────────┘

        QUERY FORMULATION — CRITICAL:
        Do NOT search for court names or case types. Search for the
        LEGAL CONCEPTS and STATUTORY PROVISIONS at issue. The query
        carries the domain context.

        SCT query examples:
          ✗ BAD:  "small claims tribunal defective product"
          ✓ GOOD: "sale of goods satisfactory quality section 14"

          ✗ BAD:  "SCT contractor dispute refund"
          ✓ GOOD: "supply of services reasonable care and skill"

          ✗ BAD:  "consumer complaint unfair seller"
          ✓ GOOD: "consumer protection unfair practice CPFTA"

          ✓ GOOD: "small claims tribunal appeal"
                   (finds SGHC decisions reviewing SCT outcomes)

          ✓ GOOD: "quantum damages defective goods assessment"
                   (finds SGHC guidance on damage calculation)

        Traffic query examples:
          ✗ BAD:  "traffic court speeding fine"
          ✓ GOOD: "road traffic act speeding sentence benchmark"

          ✗ BAD:  "drunk driving penalty"
          ✓ GOOD: "drink driving disqualification sentencing framework"

          ✗ BAD:  "reckless driving case"
          ✓ GOOD: "dangerous driving causing death sentence appeal"

          ✓ GOOD: "road traffic act magistrate appeal sentence"
                   (finds SGHC sentencing guideline decisions)

          ✓ GOOD: "demerit points disqualification judicial review"
                   (finds SGHC review of traffic administrative decisions)

        MULTIPLE QUERIES PER CASE:
        For each case, issue 2-4 targeted search_precedents calls:
          1. Core statutory provision query (e.g., "SOGA section 14
             satisfactory quality implied condition")
          2. Specific fact pattern query (e.g., "second-hand vehicle
             latent defect undisclosed")
          3. Appeal/sentencing query (e.g., "small claims tribunal
             appeal quantum" or "speeding sentencing benchmark")
          4. (If relevant) Procedural query (e.g., "limitation period
             consumer claim" or "composition offer traffic offence")

        FRAMING RESULTS:
        When presenting PAIR results for SCT or traffic cases:
          ✓ "The High Court held in [citation] that..."
          ✓ "This is binding authority from the SGHC on appeal from
             a similar consumer dispute..."
          ✓ "The Court of Appeal established the sentencing framework
             for this category of offence in [citation]..."
          ✗ Do NOT imply the result is from SCT or traffic court.

        ════════════════════════════════════════════════════════════════

        CONSTRAINTS:
        - ONLY cite statutes and cases from the curated knowledge base or verified live search results.
        - Do NOT hallucinate citations or section numbers.
        - Present precedents supporting BOTH possible outcomes.
        - Always note distinguishing factors. No precedent is a perfect match.
        - When citing PAIR results, explicitly identify them as binding higher court authority.
        - Issue multiple targeted queries rather than one broad query.

        GUARDRAILS:
        - Must ONLY cite sources from the curated knowledge base or verified live search. No hallucinated citations.
        - Must include verbatim statutory text for every cited provision.
        - Must present precedents supporting BOTH possible outcomes, not just one side.
      tools:
        - name: file_search
          type: builtin
          description: "Search OpenAI Vector Stores for relevant statutes, regulations, and curated precedent cases."
          vector_store_ids:
            - ${VS_SCT_ID}
            - ${VS_TRAFFIC_ID}
        - name: search_precedents
          type: python
          module: tools.search_precedents
          function: search_precedents
          description: "Query the PAIR Search API (search.pair.gov.sg) for binding higher court case law (SGHC, SGCA, SGHCF, SGHCR, SGHC(I), SGHC(A), SGCA(I)) matching fact patterns. Does NOT cover SCT or lower State Courts. Returns results from the eLitigation corpus with citations, catch words, and relevance scores. Results are Redis-cached with 24h TTL."
          parameters:
            - name: query
              type: string
              description: "Targeted query for legal concepts, statutory provisions, or fact patterns — NOT court names. E.g., 'sale of goods satisfactory quality section 14' or 'drink driving sentencing framework'. Issue multiple focused queries per case."
              required: true
            - name: domain
              type: string
              description: "Legal domain for context: 'small_claims' | 'traffic'. Used for logging and cache keying — does not filter courts (PAIR only covers higher courts)."
              required: true
            - name: max_results
              type: integer
              description: "Maximum number of precedents to return"
              required: false
              default: 10
            - name: date_range
              type: object
              description: "Date range filter: {start: 'YYYY-MM-DD', end: 'YYYY-MM-DD'}"
              required: false
      agent_card:
        description: "Retrieves applicable statutory rules and relevant precedent cases from curated knowledge bases and live judiciary databases."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "legal_knowledge"
            name: "Legal Knowledge Retrieval"
            description: "Retrieve applicable statutes and precedent cases for Singapore lower court matters."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.9 Agent 7: Argument Construction

```yaml
# configs/agents/argument-construction.yaml
apps:
  - name: argument-construction
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "ArgumentConstruction"
      display_name: "Argument Construction Agent"
      model:
        <<: *gpt54_model
      instruction: |
        You are the Argument Construction Agent for VerdictCouncil. You serve the JUDGE, not either party. All output is INTERNAL.

        TASK: Construct both sides' arguments for judicial evaluation.

        FOR TRAFFIC CASES:
        A. PROSECUTION ARGUMENT:
          - Charges with statutory provisions.
          - Elements to prove, mapped to evidence for each element.
          - Witness support for each element.
          - Proposed penalty range from precedents.
          - Prosecution WEAKNESSES the Judge should be aware of.

        B. DEFENSE ARGUMENT:
          - Response to each charge.
          - Evidence challenges (admissibility, reliability, calibration).
          - Mitigating factors (clean record, personal circumstances).
          - Precedents favoring defense.
          - Defense WEAKNESSES the Judge should be aware of.

        C. CONTESTED ISSUES: Key points where prosecution and defense disagree.

        FOR SCT CASES:
        A. CLAIMANT POSITION: stated claim, supporting evidence, legal basis, weaknesses.
        B. RESPONDENT POSITION: stated response, supporting evidence, legal basis, weaknesses.
        C. AGREED FACTS vs DISPUTED FACTS.
        D. EVIDENCE GAPS: what could resolve the disputed facts.
        E. STRENGTH COMPARISON: Claimant % vs Respondent % with reasoning.

        Use generate_questions to suggest judicial questions for each party.

        CONSTRAINTS:
        - Analyze BOTH sides with equal depth and rigor.
        - Note WEAKNESSES in both arguments. The Judge needs the full picture.
        - Header: 'Internal Analysis for Judicial Review Only'.

        GUARDRAILS:
        - Must analyze both sides with equal depth. Asymmetric analysis is a failure.
        - Must note weaknesses in BOTH arguments, not just one side.
        - Output must be headered "Internal Analysis for Judicial Review Only".
        - Must not determine guilt/liability. That is the Judge's role.
      tools:
        - name: generate_questions
          type: python
          module: tools.generate_questions
          function: generate_questions
          description: "Generate suggested judicial questions based on argument analysis and identified weaknesses."
          parameters:
            - name: argument_summary
              type: string
              description: "Summary of the argument or testimony to generate questions for"
              required: true
            - name: weaknesses
              type: array
              description: "List of identified weaknesses or gaps to probe"
              required: true
            - name: question_types
              type: array
              description: "Types of questions to generate: 'clarification' | 'challenge' | 'exploration' | 'credibility'"
              required: false
              default: ["clarification", "challenge"]
            - name: max_questions
              type: integer
              description: "Maximum number of questions to generate"
              required: false
              default: 5
      agent_card:
        description: "Constructs balanced arguments for both sides of a case to assist judicial evaluation. Serves the Judge, not either party."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "argument_construction"
            name: "Argument Construction"
            description: "Build prosecution/defense or claimant/respondent arguments with equal rigor for judicial review."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.10 Agent 8: Hearing Analysis

```yaml
# configs/agents/hearing-analysis.yaml
apps:
  - name: hearing-analysis
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "HearingAnalysis"
      display_name: "Hearing Analysis Agent"
      model:
        <<: *gpt54_model
      instruction: |
        You are the Hearing Analysis Agent for VerdictCouncil. You prepare the judicial reasoning dossier for the presiding judge to review at Gate 3.

        TASK: Produce a step-by-step hearing analysis from evidence to preliminary conclusion.

        REASONING CHAIN:
        1. ESTABLISHED FACTS: Facts supported by evidence, with confidence.
        2. APPLICABLE LAW: Matched statutes with specific section references.
        3. APPLICATION: For each legal element, does the evidence satisfy it?
        4. ARGUMENT EVALUATION:
           Traffic: prosecution vs defense argument strength.
           SCT: claimant vs respondent position strength.
        5. WITNESS ASSESSMENT: Credibility findings and their impact.
        6. PRECEDENT ALIGNMENT: How do similar cases inform this analysis?
        7. PRELIMINARY CONCLUSION: What does the chain suggest?
        8. UNCERTAINTY FLAGS: Where is reasoning uncertain or dependent on factual determinations the Judge must resolve at hearing?

        CONSTRAINTS:
        - Every step MUST cite its source (which agent output, which evidence).
        - Flag LOW-CONFIDENCE steps explicitly.
        - This is a hearing preparation document, not a decision or recommendation.
        - Do NOT present the conclusion with false certainty.
        - The Judge decides. You prepare the analysis for their review.

        GUARDRAILS:
        - Every step must cite the upstream agent and evidence that produced it.
        - Must flag where reasoning depends on unresolved factual disputes.
        - Must not present conclusions with false certainty. Uncertainty is valuable.
        - Must not recommend a verdict or outcome. Present analysis only.
      tools: []
      agent_card:
        description: "Produces a step-by-step hearing analysis from evidence through legal application to a preliminary conclusion for judge review at Gate 3. Does not produce verdict recommendations."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "hearing_analysis"
            name: "Hearing Analysis"
            description: "Construct a traceable hearing analysis from established facts through legal application to preliminary conclusion for judicial review."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.11 Agent 9: Hearing Governance

```yaml
# configs/agents/hearing-governance.yaml
apps:
  - name: hearing-governance
    app_module: solace_agent_mesh.agent.sac.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      supports_streaming: true
      agent_name: "HearingGovernance"
      display_name: "Hearing Governance Agent"
      model:
        <<: *gpt54_model
      instruction: |
        You are the Hearing Governance Agent for VerdictCouncil. You are the final AI checkpoint at Gate 4 before the Judge records their decision.

        TASK: Audit the full pipeline output for fairness and bias. Produce a governance summary for the Judge. Do NOT produce a verdict recommendation — the Judge decides.

        FAIRNESS AUDIT:
        1. BALANCE: Were both parties' evidence weighted equally? Flag asymmetry.
        2. UNSUPPORTED CLAIMS: Does reasoning rely on facts NOT in evidence?
        3. LOGICAL FALLACIES: circular reasoning, false equivalences, confirmation bias, anchoring to early evidence.
        4. DEMOGRAPHIC BIAS: reasoning influenced by race, gender, age, nationality, or socioeconomic status.
        5. EVIDENCE COMPLETENESS: was any submitted evidence overlooked?
        6. PRECEDENT CHERRY-PICKING: were contrary precedents acknowledged?

        If ANY critical issue found: set status to 'ESCALATE_HUMAN' and STOP. Provide the fairness audit report to the Judge. Do NOT proceed further.

        If audit passes: produce a governance summary including:
        - audit_passed: true
        - balance_assessment: summary of evidence balance
        - key_uncertainties: factors the Judge should weigh
        - fairness_report: full audit results

        CONSTRAINTS:
        - Do NOT recommend a verdict or outcome. That is the Judge's exclusive role.
        - Do NOT produce confidence scores for verdicts.
        - Be AGGRESSIVE in flagging bias. False positives are acceptable.
        - The Judge reviews this output at Gate 4 and records their own decision.

        GUARDRAILS:
        - Must HALT pipeline if critical fairness issue detected.
        - Must never produce a verdict recommendation.
        - Fairness audit must err on the side of flagging. False negatives are unacceptable.
      tools: []
      agent_card:
        description: "Final AI checkpoint at Gate 4. Audits pipeline output for fairness and bias, produces a governance summary for judge review. Does not produce verdict recommendations."
        defaultInputModes: ["text"]
        defaultOutputModes: ["text"]
        skills:
          - id: "hearing_governance"
            name: "Hearing Governance"
            description: "Audit pipeline output for fairness and bias, then present a governance summary for the Judge to review before recording their own decision."
      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.12 Web Gateway Configuration

```yaml
# configs/gateway/web-gateway.yaml
apps:
  - name: web-gateway
    app_module: solace_agent_mesh.gateway.http_sse.app
    broker:
      <<: *broker_connection
    app_config:
      namespace: ${NAMESPACE}
      gateway_name: "VerdictCouncilGateway"
      display_name: "VerdictCouncil Web Gateway"

      # HTTP Server Configuration
      http:
        host: 0.0.0.0
        port: 8000
        workers: 4

      # CORS Configuration
      cors:
        allow_origins:
          - ${FRONTEND_ORIGIN}
        allow_methods:
          - GET
          - POST
          - OPTIONS
        allow_headers:
          - Authorization
          - Content-Type
          - X-Request-ID
        allow_credentials: true
        max_age: 3600

      # Authentication
      auth:
        type: jwt
        jwt:
          secret: ${JWT_SECRET}
          algorithm: HS256
          issuer: ${JWT_ISSUER}
          audience: ${JWT_AUDIENCE}
          token_expiry: 3600

      # Case-Level Authorization
      # Role-based access control for case-scoped endpoints.
      # - judge: can view/decide cases assigned to them or cases they created
      # - admin: can view all cases, manage escalations, assign cases
      # - clerk: can create cases, upload documents, view own cases
      case_authorization:
        enabled: true
        middleware: "verdictcouncil.middleware.CaseAuthorizationMiddleware"
        roles:
          judge:
            - "view_own_cases"
            - "view_assigned_cases"
            - "record_decision"
            - "request_what_if"
            - "view_audit_trail"
          admin:
            - "view_all_cases"
            - "assign_cases"
            - "manage_escalations"
            - "view_audit_trail"
            - "export_reports"
          clerk:
            - "create_case"
            - "upload_documents"
            - "view_own_cases"

      # SSE Configuration for Pipeline Updates
      sse:
        enabled: true
        heartbeat_interval: 15
        max_connections: 100
        retry_timeout: 5000

      # File Upload Configuration
      upload:
        max_file_size: 52428800  # 50MB
        allowed_types:
          - application/pdf
          - image/jpeg
          - image/png
          - application/msword
          - application/vnd.openxmlformats-officedocument.wordprocessingml.document
          - text/plain
        upload_to: openai_files  # Upload directly to OpenAI Files API

      # Rate Limiting
      rate_limit:
        enabled: true
        requests_per_minute: 30
        burst: 5

      # Routes
      routes:
        - path: /api/v1/cases
          method: POST
          target_agent: case-processing
          description: "Submit a new case for processing"
        - path: /api/v1/cases/{case_id}/status
          method: GET
          description: "Get current processing status via SSE stream"
          sse: true
        - path: /api/v1/cases/{case_id}
          method: GET
          description: "Get completed case analysis"

      # Pipeline Response Topic
      response_topic: ${NAMESPACE}/a2a/v1/agent/request/web-gateway

      session_service:
        <<: *default_session_service
      artifact_service:
        <<: *default_artifact_service
```

---

## 3.13 Custom Python Tool Definitions

### 3.13.1 parse_document

```python
# tools/parse_document.py
"""Document parsing tool wrapping the OpenAI Files API for VerdictCouncil."""

import json
import os
from typing import Any

from openai import OpenAI


def parse_document(
    file_id: str,
    extract_tables: bool = True,
    ocr_enabled: bool = False,
    tool_context: Any = None,
) -> dict:
    """Parse an uploaded document via the OpenAI Files API.

    Extracts text content, tables, and metadata from legal filings
    submitted to VerdictCouncil. Supports PDF, DOCX, images (with OCR),
    and plain text files.

    Args:
        file_id: OpenAI File ID of the uploaded document (e.g., "file-abc123").
        extract_tables: Whether to extract tabular data from the document.
            Defaults to True.
        ocr_enabled: Whether to enable OCR for scanned/image-based documents.
            Defaults to False.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - file_id: The original file ID.
            - filename: Original filename.
            - content_type: MIME type of the document.
            - text: Extracted plain text content.
            - pages: List of per-page content dicts, each containing:
              - page_number: 1-indexed page number.
              - text: Plain text content for this page.
              - tables: Tables found on this page.
            - tables: List of extracted tables (if extract_tables is True),
              each as a list of rows where each row is a list of cell values,
              with a page_number field indicating which page it came from.
            - metadata: Document metadata including page count, word count,
              and creation date if available.
            - parsing_notes: Any warnings or issues encountered during parsing.
    """
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Retrieve file metadata
    file_info = client.files.retrieve(file_id)
    filename = file_info.filename
    content_type = getattr(file_info, "content_type", "application/octet-stream")

    # Download file content
    file_content = client.files.content(file_id)
    raw_bytes = file_content.read()

    parsing_notes = []
    extracted_text = ""
    extracted_tables = []
    metadata = {
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(raw_bytes),
    }

    # Use OpenAI for text extraction via chat completion with file attachment
    extraction_messages = [
        {
            "role": "system",
            "content": (
                "Extract all text content from the provided document. "
                "Preserve paragraph structure and formatting. "
                "If the document contains tables, extract each table as a "
                "JSON array of rows."
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "file",
                    "file": {"file_id": file_id},
                },
                {
                    "type": "text",
                    "text": (
                        "Extract all text from this document. "
                        f"{'Also extract all tables as structured data.' if extract_tables else ''} "
                        f"{'This may be a scanned document - use OCR.' if ocr_enabled else ''} "
                        "Return JSON with keys: text, tables (list of tables), page_count, word_count."
                    ),
                },
            ],
        },
    ]

    response = client.chat.completions.create(
        model="gpt-5.4-nano",
        messages=extraction_messages,
        response_format={"type": "json_object"},
    )

    try:
        parsed = json.loads(response.choices[0].message.content)
        extracted_text = parsed.get("text", "")
        extracted_tables = parsed.get("tables", []) if extract_tables else []
        metadata["page_count"] = parsed.get("page_count")
        metadata["word_count"] = parsed.get("word_count")
    except (json.JSONDecodeError, IndexError) as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise DocumentParseError(
            f"Failed to parse document {file_id}: JSON parsing failed: {str(e)}"
        ) from e

    if not extracted_text.strip():
        raise DocumentParseError(
            f"No text content extracted from document {file_id}. "
            "Document may be corrupt or unsupported format."
        )

    return {
        "file_id": file_id,
        "filename": filename,
        "content_type": content_type,
        "text": extracted_text,
        "pages": extracted_pages,       # per-page text + tables for source grounding
        "tables": extracted_tables,
        "metadata": metadata,
        "parsing_notes": parsing_notes,
    }
```

### 3.13.2 cross_reference

```python
# tools/cross_reference.py
"""Cross-reference tool for comparing document segments in VerdictCouncil."""

import json
import os
from typing import Any

from openai import OpenAI


def cross_reference(
    segments: list,
    check_type: str = "all",
    tool_context: Any = None,
) -> dict:
    """Compare document segments to identify contradictions and corroborations.

    Analyzes pairs of document segments to find contradictions,
    corroborations, and inconsistencies across evidence items submitted
    to VerdictCouncil.

    Args:
        segments: List of document segments to compare. Each segment is a
            dictionary with keys:
            - doc_id (str): Document identifier.
            - text (str): The text content of the segment.
            - page (int): Page number where the segment appears.
            - paragraph (int): Paragraph number within the page.
        check_type: Type of cross-reference check to perform.
            One of: "contradiction", "corroboration", "all".
            Defaults to "all".
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - contradictions: List of identified contradictions, each with
              segment_a, segment_b, description, and severity.
            - corroborations: List of identified corroborations, each with
              segment_a, segment_b, description, and strength.
            - inconsistencies: List of minor inconsistencies that may or may
              not be substantive.
            - summary: Brief overall assessment of cross-reference findings.
    """
    if len(segments) < 2:
        return {
            "contradictions": [],
            "corroborations": [],
            "inconsistencies": [],
            "summary": "Insufficient segments for cross-reference (minimum 2 required).",
        }

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Build pairwise comparison prompt
    segments_text = json.dumps(segments, indent=2)

    check_instruction = {
        "contradiction": "Focus ONLY on identifying contradictions between segments.",
        "corroboration": "Focus ONLY on identifying corroborations between segments.",
        "all": "Identify contradictions, corroborations, and inconsistencies.",
    }.get(check_type, "Identify contradictions, corroborations, and inconsistencies.")

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a legal evidence analyst. Compare the provided document "
                    "segments and identify relationships between them. Be precise and "
                    "cite specific text from each segment. "
                    f"{check_instruction}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Compare these document segments:\n\n{segments_text}\n\n"
                    "Return JSON with keys:\n"
                    "- contradictions: [{segment_a: {doc_id, page, paragraph}, "
                    "segment_b: {doc_id, page, paragraph}, description, severity: "
                    "'critical'|'moderate'|'minor'}]\n"
                    "- corroborations: [{segment_a, segment_b, description, "
                    "strength: 'strong'|'moderate'|'weak'}]\n"
                    "- inconsistencies: [{segments, description, significance}]\n"
                    "- summary: brief overall assessment"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise CrossReferenceError(
            f"Failed to parse cross-reference analysis response: {str(e)}"
        ) from e

    # Filter based on check_type
    if check_type == "contradiction":
        result["corroborations"] = []
    elif check_type == "corroboration":
        result["contradictions"] = []

    return result
```

### 3.13.3 timeline_construct

```python
# tools/timeline_construct.py
"""Timeline construction tool for VerdictCouncil fact reconstruction."""

import json
import os
from typing import Any

from openai import OpenAI


def timeline_construct(
    events: list,
    tool_context: Any = None,
) -> dict:
    """Build a chronological timeline from extracted case events.

    Takes a list of events extracted from case evidence and produces a
    normalized, chronologically ordered timeline with conflict detection
    for events with contradictory dates or descriptions.

    Args:
        events: List of events to order. Each event is a dictionary with:
            - date (str): Date/time string in any recognizable format.
            - description (str): Description of the event.
            - source_ref (str): Source document reference (doc_id, page, paragraph).
            - parties (list[str]): Parties involved in the event.
            - location (str, optional): Location where the event occurred.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - timeline: Chronologically ordered list of events, each with
              normalized_date (ISO 8601), description, source_ref, parties,
              location, and sequence_number.
            - date_conflicts: Events where multiple sources disagree on timing.
            - undated_events: Events that could not be assigned a date.
            - duration: Overall timespan from earliest to latest event.
            - event_count: Total number of events processed.
    """
    if not events:
        return {
            "timeline": [],
            "date_conflicts": [],
            "undated_events": [],
            "duration": None,
            "event_count": 0,
        }

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    events_text = json.dumps(events, indent=2)

    response = client.chat.completions.create(
        model="gpt-5.4-nano",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a legal timeline analyst. Given a list of case events, "
                    "normalize all dates to ISO 8601 format, sort chronologically, "
                    "detect conflicting dates for the same event from different sources, "
                    "and identify events without clear dates. "
                    "Singapore timezone (SGT, UTC+8) unless otherwise specified."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Construct a chronological timeline from these events:\n\n"
                    f"{events_text}\n\n"
                    "Return JSON with keys:\n"
                    "- timeline: [{sequence_number, normalized_date (ISO 8601), "
                    "description, source_ref, parties, location}]\n"
                    "- date_conflicts: [{event_description, sources: [{source_ref, "
                    "claimed_date}], resolution_note}]\n"
                    "- undated_events: [{description, source_ref, estimated_position}]\n"
                    "- duration: {start_date, end_date, span_description}\n"
                    "- event_count: total number"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise TimelineConstructionError(
            f"Failed to parse timeline construction response: {str(e)}"
        ) from e

    return result
```

### 3.13.4 generate_questions

```python
# tools/generate_questions.py
"""Judicial question generation tool for VerdictCouncil."""

import json
import os
from typing import Any

from openai import OpenAI


def generate_questions(
    argument_summary: str,
    weaknesses: list,
    question_types: list = None,
    max_questions: int = 5,
    tool_context: Any = None,
) -> dict:
    """Generate suggested judicial questions for hearing preparation.

    Analyzes argument summaries and identified weaknesses to produce
    targeted questions a Judge could ask each party during a hearing.
    Questions are tagged by type and linked to the specific weakness
    or issue they address.

    Args:
        argument_summary: Summary of the argument or testimony to
            generate questions for.
        weaknesses: List of identified weaknesses or gaps in the
            argument that questions should probe.
        question_types: Types of questions to generate. Options:
            "clarification", "challenge", "exploration", "credibility".
            Defaults to ["clarification", "challenge"].
        max_questions: Maximum number of questions to generate.
            Defaults to 5.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - questions: List of generated questions, each with:
              - question (str): The question text.
              - type (str): Question type tag.
              - target_party (str): Which party the question is directed at.
              - addresses_weakness (str): The specific weakness this probes.
              - expected_impact (str): What the answer could reveal.
              - priority (str): "high", "medium", or "low".
            - question_count: Total number of questions generated.
            - coverage: Which weaknesses are addressed and which are not.
    """
    if question_types is None:
        question_types = ["clarification", "challenge"]

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    weaknesses_text = json.dumps(weaknesses, indent=2)
    types_text = ", ".join(question_types)

    response = client.chat.completions.create(
        model="gpt-5",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a judicial hearing preparation assistant for Singapore "
                    "lower courts. Generate focused, professionally worded questions "
                    "that a Judge or Tribunal Magistrate could ask during a hearing. "
                    "Questions must be neutral, not leading. They should probe "
                    "weaknesses and gaps without revealing the court's preliminary "
                    "analysis."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Based on the following argument summary and weaknesses, "
                    f"generate up to {max_questions} judicial questions.\n\n"
                    f"ARGUMENT SUMMARY:\n{argument_summary}\n\n"
                    f"WEAKNESSES TO PROBE:\n{weaknesses_text}\n\n"
                    f"QUESTION TYPES TO INCLUDE: {types_text}\n\n"
                    "Return JSON with keys:\n"
                    "- questions: [{question, type, target_party, "
                    "addresses_weakness, expected_impact, priority}]\n"
                    "- question_count: number\n"
                    "- coverage: {addressed_weaknesses: [str], "
                    "unaddressed_weaknesses: [str]}"
                ),
            },
        ],
        response_format={"type": "json_object"},
    )

    try:
        result = json.loads(response.choices[0].message.content)
        # Enforce max_questions limit
        if "questions" in result and len(result["questions"]) > max_questions:
            result["questions"] = result["questions"][:max_questions]
            result["question_count"] = max_questions
    except json.JSONDecodeError as e:
        # Tool errors propagate to the agent, which logs the error and
        # halts the pipeline per Section 2.6 error handling policy.
        raise QuestionGenerationError(
            f"Failed to parse question generation response: {str(e)}"
        ) from e

    return result
```

> **Note:** All custom tool errors (`DocumentParseError`, `CrossReferenceError`, `TimelineConstructionError`, `QuestionGenerationError`) propagate to the calling agent, which logs the error and halts the pipeline per Section 2.6 error handling policy. Tools must never silently return empty or default results on failure.

### 3.13.5 confidence_calc

```python
# tools/confidence_calc.py
"""Confidence calculation tool for VerdictCouncil verdict recommendations."""

from typing import Any


def confidence_calc(
    evidence_scores: list,
    rule_relevance_scores: list,
    precedent_similarity_scores: list,
    witness_credibility_scores: list,
    weights: dict = None,
    tool_context: Any = None,
) -> dict:
    """Calculate weighted confidence score for a verdict recommendation.

    Combines evidence strength, rule relevance, precedent similarity, and
    witness credibility scores into a single confidence metric. Uses
    configurable weights to reflect the relative importance of each factor
    for the specific case type.

    Args:
        evidence_scores: List of evidence strength scores (0-100) from
            Evidence Analysis agent output.
        rule_relevance_scores: List of rule relevance scores (0-100) from
            Legal Knowledge agent output.
        precedent_similarity_scores: List of precedent similarity scores
            (0-100) from Legal Knowledge agent output.
        witness_credibility_scores: List of witness credibility scores
            (0-100) from Witness Analysis agent output.
        weights: Weight distribution across the four factors. Dictionary
            with keys: evidence, rules, precedents, witnesses. Values must
            sum to 1.0. Defaults to equal weighting (0.25 each).
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - confidence_score: Overall weighted score (0-100).
            - component_scores: Breakdown by factor (average of each list).
            - weights_used: The weight distribution applied.
            - data_quality: Assessment of input completeness.
            - interpretation: Human-readable confidence band
              ("very_high", "high", "moderate", "low", "very_low").
    """
    if weights is None:
        weights = {
            "evidence": 0.25,
            "rules": 0.25,
            "precedents": 0.25,
            "witnesses": 0.25,
        }

    # Validate weights sum to 1.0 (with floating point tolerance)
    weight_sum = sum(weights.values())
    if abs(weight_sum - 1.0) > 0.01:
        # Normalize weights if they do not sum to 1.0
        weights = {k: v / weight_sum for k, v in weights.items()}

    # Calculate component averages
    def safe_average(scores: list) -> float:
        if not scores:
            return 0.0
        valid = [s for s in scores if isinstance(s, (int, float)) and 0 <= s <= 100]
        return sum(valid) / len(valid) if valid else 0.0

    component_scores = {
        "evidence": safe_average(evidence_scores),
        "rules": safe_average(rule_relevance_scores),
        "precedents": safe_average(precedent_similarity_scores),
        "witnesses": safe_average(witness_credibility_scores),
    }

    # Calculate weighted confidence
    confidence_score = round(
        component_scores["evidence"] * weights["evidence"]
        + component_scores["rules"] * weights["rules"]
        + component_scores["precedents"] * weights["precedents"]
        + component_scores["witnesses"] * weights["witnesses"],
        1,
    )

    # Clamp to 0-100
    confidence_score = max(0.0, min(100.0, confidence_score))

    # Assess data quality
    all_scores = [
        evidence_scores,
        rule_relevance_scores,
        precedent_similarity_scores,
        witness_credibility_scores,
    ]
    empty_components = sum(1 for s in all_scores if not s)
    data_quality = {
        "complete_components": 4 - empty_components,
        "total_components": 4,
        "missing": [],
    }
    if not evidence_scores:
        data_quality["missing"].append("evidence_scores")
    if not rule_relevance_scores:
        data_quality["missing"].append("rule_relevance_scores")
    if not precedent_similarity_scores:
        data_quality["missing"].append("precedent_similarity_scores")
    if not witness_credibility_scores:
        data_quality["missing"].append("witness_credibility_scores")

    if empty_components > 0:
        data_quality["warning"] = (
            f"{empty_components} component(s) had no scores. "
            "Confidence may be unreliable."
        )

    # Determine interpretation band
    if confidence_score >= 85:
        interpretation = "very_high"
    elif confidence_score >= 70:
        interpretation = "high"
    elif confidence_score >= 50:
        interpretation = "moderate"
    elif confidence_score >= 30:
        interpretation = "low"
    else:
        interpretation = "very_low"

    return {
        "confidence_score": confidence_score,
        "component_scores": {k: round(v, 1) for k, v in component_scores.items()},
        "weights_used": weights,
        "data_quality": data_quality,
        "interpretation": interpretation,
    }
```

> **Score Calibration Disclaimer:** The confidence score is a relative indicator produced by weighted LLM-derived component scores, not a statistically calibrated probability. It should be interpreted as a directional signal (higher = stronger support for the verdict) rather than a precise likelihood. The system does not claim statistical validity for this metric.

### 3.13.6 search_precedents

```python
# tools/search_precedents.py
"""Live judiciary precedent search tool for VerdictCouncil."""

import hashlib
import json
import os
import time
from typing import Any

import redis
import requests


# Distributed rate limiter: 2 requests per second across all pods.
# Uses Redis INCR with TTL instead of a process-local variable, which
# would not work correctly when multiple replicas are running.


def _rate_limit(redis_client: redis.Redis, key: str = "vc:ratelimit:judiciary",
                max_per_second: int = 2):
    """Enforce distributed rate limit using Redis INCR with TTL."""
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, 1)
    if count > max_per_second:
        time.sleep(1.0)


def _get_redis_client() -> redis.Redis:
    """Get Redis client for caching."""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        db=int(os.environ.get("REDIS_DB", 0)),
        password=os.environ.get("REDIS_PASSWORD"),
        decode_responses=True,
    )


def _cache_key(query: str, domain: str, max_results: int, date_range: dict) -> str:
    """Generate deterministic cache key."""
    key_data = json.dumps(
        {"query": query, "domain": domain, "max_results": max_results,
         "date_range": date_range},
        sort_keys=True,
    )
    return f"vc:precedents:{hashlib.sha256(key_data.encode()).hexdigest()}"


def _search_pair_sg(query: str, domain: str, max_results: int,
                    date_range: dict) -> list:
    """Query PAIR Search API (search.pair.gov.sg) for Singapore case law.

    PAIR (Platform for AI-assisted Research) is a Singapore government legal
    research platform. Its search API provides hybrid retrieval (BM25 + semantic
    embedding) over the full corpus of Singapore judiciary decisions hosted on
    eLitigation (elitigation.sg).

    COURT COVERAGE LIMITATION: PAIR only indexes higher court decisions:
      - SGHC (High Court), SGCA (Court of Appeal)
      - SGHCF (Family Division), SGHCR (General Division)
      - SGHC(I) (SICC), SGHC(A) (Appellate Division), SGCA(I) (SICC Appeal)

    It does NOT cover Small Claims Tribunals or lower State Courts (District
    Court, Magistrate Court). SCT proceedings are informal and rarely produce
    published written grounds. However, higher court rulings are binding on
    lower courts, making these results directly applicable as precedent.

    The API was discovered via network inspection of search.pair.gov.sg. It
    accepts POST requests with a JSON payload and returns structured results
    including case citations, court, catch words, dates, snippets, and direct
    links to full judgments on eLitigation.
    """
    _rate_limit(_get_redis_client())

    base_url = "https://search.pair.gov.sg/api/v1/search"

    # No domain-based court filtering — PAIR only covers higher courts.
    # The query itself carries the domain context (e.g., "sale of goods
    # satisfactory quality" for SCT, "speeding demerit points" for traffic).
    case_filters = {}

    payload = {
        "id": "",
        "hits": max_results,
        "query": query,
        "offset": 0,
        "filters": {
            "hansardFilters": {},
            "caseJudgementFilters": case_filters,
            "legislationFilters": {},
        },
        "sources": ["judiciary"],
        "isLoggingEnabled": False,
    }

    try:
        resp = requests.post(
            base_url,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("searchResults", [])[:max_results]:
            results.append({
                "citation": item.get("citationNum", ""),
                "title": item.get("title", ""),
                "case_number": item.get("caseNum", ""),
                "outcome": "",  # Not in search results; available in full judgment
                "reasoning_summary": item.get("snippet", ""),
                "similarity_score": item.get("matchScore", {}).get("score", 0),
                "date": item.get("date", ""),
                "court": item.get("court", ""),
                "catch_words": item.get("catchWords", []),
                "elitigation_url": item.get("url", ""),
                "source": "pair_sg",
            })
        return results
    except requests.RequestException as e:
        # Flag failed lookups explicitly — never silently return empty results
        return [{"error": f"PAIR search failed: {str(e)}",
                 "source": "pair_sg",
                 "source_status": "search_failed"}]


def search_precedents(
    query: str,
    domain: str = "small_claims",
    max_results: int = 10,
    date_range: dict = None,
    tool_context: Any = None,
) -> dict:
    """Query PAIR Search API for binding higher court case law.

    Searches search.pair.gov.sg for published judiciary decisions from
    Singapore's higher courts (SGHC, SGCA, etc.). Results are cached in
    Redis with a 24-hour TTL. Rate-limited to 2 requests per second.

    The agent should call this tool multiple times per case with targeted
    queries (statutory provisions, fact patterns, appeal/sentencing terms)
    rather than a single broad query. See the QUERY FORMULATION section in
    the agent instruction for examples.

    Args:
        query: Semantic search query describing the legal issue or fact
            pattern to search for.
        domain: Legal domain to search within. One of "small_claims" or
            "traffic". Defaults to "small_claims".
        max_results: Maximum number of precedents to return. Defaults to 10.
        date_range: Optional date range filter. Dictionary with keys:
            - start (str): Start date in "YYYY-MM-DD" format.
            - end (str): End date in "YYYY-MM-DD" format.
        tool_context: SAM tool context object for logging and state access.

    Returns:
        A dictionary containing:
            - precedents: List of matching precedent cases, each with
              citation, title, case_number, reasoning_summary,
              similarity_score, date, court, catch_words,
              elitigation_url, and source.
            - sources_queried: List of APIs that were searched.
            - total_results: Total number of results found.
            - cached: Whether results were served from cache.
            - cache_ttl_remaining: Seconds remaining on cache entry (if cached).
    """
    if date_range is None:
        date_range = {}

    cache_k = _cache_key(query, domain, max_results, date_range)

    # Check Redis cache
    try:
        r = _get_redis_client()
        cached = r.get(cache_k)
        if cached:
            result = json.loads(cached)
            result["cached"] = True
            result["cache_ttl_remaining"] = r.ttl(cache_k)
            return result
    except (redis.RedisError, json.JSONDecodeError):
        pass  # Cache miss or connection failure; proceed with live search

    # Query PAIR Search API (sole live source)
    pair_results = _search_pair_sg(query, domain, max_results, date_range)

    # Filter out error entries
    all_results = [item for item in pair_results if "error" not in item]

    # Sort by similarity_score descending
    all_results.sort(
        key=lambda x: x.get("similarity_score", 0), reverse=True
    )
    all_results = all_results[:max_results]

    result = {
        "precedents": all_results,
        "sources_queried": ["pair_sg"],
        "total_results": len(all_results),
        "cached": False,
        "cache_ttl_remaining": None,
    }

    # Cache result in Redis with 24h TTL
    try:
        r = _get_redis_client()
        r.setex(cache_k, 86400, json.dumps(result))  # 24h = 86400s
    except redis.RedisError:
        pass  # Cache write failure is non-fatal

    return result
```

---

## 3.14 OpenAI Function-Calling JSON Schemas

### 3.14.1 parse_document

```json
{
  "type": "function",
  "function": {
    "name": "parse_document",
    "description": "Parse an uploaded document via the OpenAI Files API. Extracts text content, tables, and metadata from legal filings submitted to VerdictCouncil. Supports PDF, DOCX, images (with OCR), and plain text files.",
    "parameters": {
      "type": "object",
      "properties": {
        "file_id": {
          "type": "string",
          "description": "OpenAI File ID of the uploaded document (e.g., 'file-abc123')"
        },
        "extract_tables": {
          "type": "boolean",
          "description": "Whether to extract tabular data from the document",
          "default": true
        },
        "ocr_enabled": {
          "type": "boolean",
          "description": "Whether to enable OCR for scanned or image-based documents",
          "default": false
        }
      },
      "required": ["file_id"],
      "additionalProperties": false
    }
  }
}
```

### 3.14.2 cross_reference

```json
{
  "type": "function",
  "function": {
    "name": "cross_reference",
    "description": "Compare document segments to identify contradictions, corroborations, and inconsistencies across evidence items. Analyzes pairs of segments from different documents submitted by different parties.",
    "parameters": {
      "type": "object",
      "properties": {
        "segments": {
          "type": "array",
          "description": "List of document segments to compare",
          "items": {
            "type": "object",
            "properties": {
              "doc_id": {
                "type": "string",
                "description": "Document identifier"
              },
              "text": {
                "type": "string",
                "description": "Text content of the segment"
              },
              "page": {
                "type": "integer",
                "description": "Page number where the segment appears"
              },
              "paragraph": {
                "type": "integer",
                "description": "Paragraph number within the page"
              }
            },
            "required": ["doc_id", "text", "page", "paragraph"],
            "additionalProperties": false
          }
        },
        "check_type": {
          "type": "string",
          "enum": ["contradiction", "corroboration", "all"],
          "description": "Type of cross-reference check to perform"
        }
      },
      "required": ["segments", "check_type"],
      "additionalProperties": false
    }
  }
}
```

### 3.14.3 timeline_construct

```json
{
  "type": "function",
  "function": {
    "name": "timeline_construct",
    "description": "Build a chronological timeline from extracted case events. Handles date normalization to ISO 8601 (Singapore timezone), chronological ordering, and conflict detection for events with contradictory dates or descriptions.",
    "parameters": {
      "type": "object",
      "properties": {
        "events": {
          "type": "array",
          "description": "List of events to order chronologically",
          "items": {
            "type": "object",
            "properties": {
              "date": {
                "type": "string",
                "description": "Date/time string in any recognizable format"
              },
              "description": {
                "type": "string",
                "description": "Description of the event"
              },
              "source_ref": {
                "type": "string",
                "description": "Source document reference (doc_id, page, paragraph)"
              },
              "parties": {
                "type": "array",
                "items": { "type": "string" },
                "description": "Parties involved in the event"
              },
              "location": {
                "type": "string",
                "description": "Location where the event occurred"
              }
            },
            "required": ["date", "description", "source_ref", "parties"],
            "additionalProperties": false
          }
        }
      },
      "required": ["events"],
      "additionalProperties": false
    }
  }
}
```

### 3.14.4 generate_questions

```json
{
  "type": "function",
  "function": {
    "name": "generate_questions",
    "description": "Generate suggested judicial questions for hearing preparation. Analyzes argument summaries and identified weaknesses to produce targeted, neutral questions a Judge could ask each party during a hearing.",
    "parameters": {
      "type": "object",
      "properties": {
        "argument_summary": {
          "type": "string",
          "description": "Summary of the argument or testimony to generate questions for"
        },
        "weaknesses": {
          "type": "array",
          "items": { "type": "string" },
          "description": "List of identified weaknesses or gaps in the argument that questions should probe"
        },
        "question_types": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": ["clarification", "challenge", "exploration", "credibility"]
          },
          "description": "Types of questions to generate",
          "default": ["clarification", "challenge"]
        },
        "max_questions": {
          "type": "integer",
          "description": "Maximum number of questions to generate",
          "default": 5,
          "minimum": 1,
          "maximum": 20
        }
      },
      "required": ["argument_summary", "weaknesses"],
      "additionalProperties": false
    }
  }
}
```

### 3.14.5 confidence_calc

```json
{
  "type": "function",
  "function": {
    "name": "confidence_calc",
    "description": "Calculate a weighted confidence score for a verdict recommendation. Combines evidence strength, rule relevance, precedent similarity, and witness credibility into a single metric with interpretation bands.",
    "parameters": {
      "type": "object",
      "properties": {
        "evidence_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of evidence strength scores (0-100) from Evidence Analysis"
        },
        "rule_relevance_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of rule relevance scores (0-100) from Legal Knowledge"
        },
        "precedent_similarity_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of precedent similarity scores (0-100) from Legal Knowledge"
        },
        "witness_credibility_scores": {
          "type": "array",
          "items": { "type": "number", "minimum": 0, "maximum": 100 },
          "description": "List of witness credibility scores (0-100) from Witness Analysis"
        },
        "weights": {
          "type": "object",
          "properties": {
            "evidence": { "type": "number", "minimum": 0, "maximum": 1 },
            "rules": { "type": "number", "minimum": 0, "maximum": 1 },
            "precedents": { "type": "number", "minimum": 0, "maximum": 1 },
            "witnesses": { "type": "number", "minimum": 0, "maximum": 1 }
          },
          "description": "Weight distribution across the four factors. Values must sum to 1.0. Defaults to equal weighting (0.25 each).",
          "additionalProperties": false
        }
      },
      "required": [
        "evidence_scores",
        "rule_relevance_scores",
        "precedent_similarity_scores",
        "witness_credibility_scores"
      ],
      "additionalProperties": false
    }
  }
}
```

### 3.14.6 file_search (Built-in)

```json
{
  "type": "function",
  "function": {
    "name": "file_search",
    "description": "Search OpenAI Vector Stores for relevant statutes, regulations, and curated precedent cases. Performs semantic similarity search across the VerdictCouncil legal knowledge base (vs_sct for Small Claims Tribunal, vs_traffic for Traffic Court).",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Semantic search query to find relevant legal documents"
        },
        "vector_store_ids": {
          "type": "array",
          "items": { "type": "string" },
          "description": "IDs of the OpenAI Vector Stores to search (e.g., ['vs_sct', 'vs_traffic'])"
        },
        "max_results": {
          "type": "integer",
          "description": "Maximum number of results to return",
          "default": 10,
          "minimum": 1,
          "maximum": 50
        }
      },
      "required": ["query"],
      "additionalProperties": false
    }
  }
}
```

### 3.14.7 search_precedents

```json
{
  "type": "function",
  "function": {
    "name": "search_precedents",
    "description": "Query the PAIR Search API (search.pair.gov.sg) for binding higher court case law (SGHC, SGCA, SGHCF, SGHCR, SGHC(I), SGHC(A), SGCA(I)) matching fact patterns. Does NOT cover SCT or lower State Courts — use file_search for domain-specific curated content. Returns results from the eLitigation corpus with citations, court, catch words, and relevance scores. Results are Redis-cached with 24-hour TTL. Rate-limited to 2 requests per second.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Targeted query for legal concepts, statutory provisions, or fact patterns — NOT court names. E.g., 'sale of goods satisfactory quality section 14' or 'drink driving sentencing framework'. Issue multiple focused queries per case rather than one broad query."
        },
        "domain": {
          "type": "string",
          "enum": ["small_claims", "traffic"],
          "description": "Legal domain for context and cache keying. Does not filter courts — PAIR only covers higher courts."
        },
        "max_results": {
          "type": "integer",
          "description": "Maximum number of precedents to return per query",
          "default": 10,
          "minimum": 1,
          "maximum": 50
        },
        "date_range": {
          "type": "object",
          "properties": {
            "start": {
              "type": "string",
              "description": "Start date in YYYY-MM-DD format"
            },
            "end": {
              "type": "string",
              "description": "End date in YYYY-MM-DD format"
            }
          },
          "description": "Optional date range filter for precedent search",
          "additionalProperties": false
        }
      },
      "required": ["query", "domain"],
      "additionalProperties": false
    }
  }
}
```
