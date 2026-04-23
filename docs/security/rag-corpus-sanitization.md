# RAG Corpus Sanitization — Architecture Note

## Threat Model

Indirect prompt injection via a RAG corpus (Greshake et al., 2023) occurs when an attacker embeds adversarial instructions inside a document that is later retrieved and concatenated into a system or user prompt. The LLM then follows the attacker's instructions instead of the operator's. This attack is ranked **#1 in the OWASP Top 10 for LLM Applications (2025)** as LLM01: Prompt Injection.

In VerdictCouncil, the attack surface is the admin domain document upload endpoint (`POST /api/v1/domains/admin/{domain_id}/documents`). A compromised administrator, a supply-chain attack on a document source, or an attacker with admin access could seed the knowledge base with injection payloads that influence judicial reasoning agents during case processing.

## Two-Layer Defence

### Layer 1 — Regex fast-path (delimiter-based)

`src/shared/sanitization.py` runs before any classifier call. It strips:
- `<|im_start|>...<|im_end|>` — OpenAI ChatML delimiters
- `<|...|>` — Generic OpenAI-style tokens
- `[INST]...[/INST]` — Llama instruction delimiters
- `<<SYS>>...</SYS>>` — Llama system-prompt blocks
- `` ```system...``` `` — Markdown system blocks
- `<system>`, `<instruction>`, `<tool_call>`, `<function_call>` XML tags

Zero-latency; runs synchronously before any I/O.

### Layer 2 — DeBERTa-v3 semantic classifier (llm-guard)

`src/shared/sanitization.py::_classify_sync()` wraps `llm_guard.input_scanners.PromptInjection` with `protectai/deberta-v3-base-prompt-injection-v2` (Apache-2.0). Benchmarks: 95.25% accuracy, 99.74% recall on 20k held-out examples (Protect AI, 2024). Detects plain-English instruction overrides ("Ignore all previous instructions…", "SYSTEM: you are now…") that regex cannot catch.

Called asynchronously via `asyncio.to_thread()` to avoid blocking the FastAPI event loop.

When `is_valid=False` (injection detected), the page text is replaced with `[CONTENT_BLOCKED_BY_SCANNER]` before the sanitized artifact is uploaded to the OpenAI vector store.

## Pipeline Flow

```
Admin uploads file
    │
    ▼
[MIME type + size check]
    │
    ▼
[OpenAI Files API — store original]
    │
    ▼
[OpenAI Responses API — extract text from file]    ← OpenAI model sees raw bytes
    │
    ▼
[Layer 1: regex sanitizer] ─── per page, single pass
    │
    ▼
[Layer 2: DeBERTa-v3 classifier] ─── per page, async, only if classifier_sanitizer_enabled=True
    │
    ▼
[Sanitized artifact assembled]
    │
    ▼
[OpenAI Files API — store sanitized artifact]
    │
    ▼
[OpenAI Vector Stores — index sanitized artifact only]
    │
    ▼
[AdminEvent logged: regex_hits, classifier_hits, chunks_scanned]
```

## Ordering Limitation

The OpenAI extraction step runs **before** sanitization — this is intrinsic. Semantic injection phrased in natural language is only recognizable as text, not as bytes. Mitigation: the extraction model (`gpt-5.4-nano`) is not instructed to follow embedded instructions, only to extract content. The sanitizer then processes the extracted text.

A second mitigation line (future work) is retrieval-time re-scanning before chunks enter the chat prompt, and structural spotlighting as described by Hines et al. (2024).

## Scoped Rollout

The `run_classifier` kwarg to `parse_document()` defaults `False`. Only the domain-upload route sets it `True`. The case-processing pipeline runner continues with regex-only sanitization until field evidence validates the classifier's false-positive rate on legal corpora.

## Classifier Configuration

| Setting | Default | Env override |
|---------|---------|--------------|
| `classifier_sanitizer_enabled` | `True` | `CLASSIFIER_SANITIZER_ENABLED=false` |
| `domain_uploads_enabled` | `True` | `DOMAIN_UPLOADS_ENABLED=false` |
| Threshold | 0.9 | (code only, see `_get_prompt_injection_scanner()`) |

## References

- Greshake, K. et al. (2023). *Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection.* arXiv:2302.12173
- Liu, Y. et al. (2023). *Prompt Injection attack against LLM-integrated Applications.* arXiv:2306.05499
- Liu, Y. et al. (2023). *Formalizing and Benchmarking Prompt Injection Attacks and Defenses.* arXiv:2310.12815
- Hines, K. et al. (2024). *Defending Against Indirect Prompt Injection Attacks With Spotlighting.* arXiv:2403.14720
- Chen, S. et al. (2024). *StruQ: Defending Against Prompt Injection with Structured Queries.* arXiv:2402.06363
- OWASP Top 10 for LLM Applications (2025) — LLM01: Prompt Injection
- NIST AI 600-1 (2024) — Generative AI Risk Management Framework
- Protect AI (2024). *protectai/deberta-v3-base-prompt-injection-v2* model card. Hugging Face.

## Alternatives Considered

| Library | Verdict | Why not adopted |
|---------|---------|-----------------|
| **Meta Llama Prompt Guard 2 22M** | Strong alternative | Gated Llama 4 Community License; requires HF agreement per user |
| **NeMo Guardrails 0.21.0** | Poor fit | Dialogue-turn architecture; heavyweight for ingest-only use |
| **guardrails-ai 0.10.0 + DetectJailbreak** | OK | Framework overhead; DetectJailbreak validator roughly equivalent to llm-guard's scanner |
| **rebuff 0.1.1** | Rejected | Archived 2024-08; requires OpenAI + Pinecone for the LLM/vector layers |
| **presidio-analyzer 2.2.x** | Complementary | PII detection only — not prompt injection; add as future work if PII governance is required |
