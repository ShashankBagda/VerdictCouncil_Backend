# llm-guard 0.3.16 Spike Report

**Date:** 2026-04-23  
**Environment:** Python 3.12.12, Apple Silicon (MPS device)  
**Objective:** Confirm API surface, resolver compatibility, and runtime behaviour before integrating into production.

## 1. API Surface

```python
from llm_guard.input_scanners.prompt_injection import (
    MatchType,   # Enum — members: SENTENCE, FULL, TRUNCATE_TOKEN_HEAD_TAIL, TRUNCATE_HEAD_TAIL, CHUNKS
    PromptInjection,
    V2_MODEL,    # Model(path='protectai/deberta-v3-base-prompt-injection-v2', ...)
)

scanner = PromptInjection(
    model=V2_MODEL,          # pass the Model dataclass, not a string
    threshold=0.9,
    match_type=MatchType.CHUNKS,
)

sanitized_text, is_valid, risk_score = scanner.scan(text: str)
# sanitized_text: the original text unchanged (library does NOT redact)
# is_valid:       True = clean, False = injection detected
# risk_score:     float — ~1.0 when injected, ~-1.0 when clean
```

**Key finding:** The library flags but does not redact. Redaction (`[CONTENT_BLOCKED_BY_SCANNER]`) is handled by `_classify_sync()` in `src/shared/sanitization.py`.

## 2. Runtime Measurements (MPS device, model from HF cache)

| Input | is_valid | risk_score | time |
|-------|----------|------------|------|
| Clean legal sentence | True | -1.0 | 2.05 s (first run) |
| Plain-English injection | False | 1.0 | 0.18 s |
| Mixed (legal + injection) | False | 1.0 | 0.19 s |

**First instantiation:** 27.05 s (model download + MPS warm-up on cold cache)  
**Subsequent instantiation from cache:** < 3 s  
**Cached weights location:** `~/.cache/huggingface/hub/models--protectai--deberta-v3-base-prompt-injection-v2/`

## 3. Dependency Resolution

Package pulls in:
- `transformers==4.51.3`
- `torch` (any 2.4+ wheel matching the platform)
- `sentencepiece`

The project's existing `uv.lock` already pins `tokenizers==0.22.2` via litellm. Resolution succeeds because transformers 4.51.3 also bounds tokenizers appropriately. Run `uv lock` after adding the dependency to produce the updated lock file.

## 4. Python 3.12 Compatibility

Confirmed: `llm-guard==0.3.16` installs cleanly on Python 3.12.12 (package metadata: `>=3.10,<3.13`). All internal imports succeed.

## 5. MatchType.CHUNKS Behaviour

When `MatchType.CHUNKS` is used, the scanner splits text into overlapping windows internally before scoring. The aggregate `is_valid` and max `risk_score` are returned for the whole input. Importantly, `sanitized_text` is still the original input — chunking is for classification granularity, not redaction granularity. The integration in `_classify_sync()` replaces the entire input with `[CONTENT_BLOCKED_BY_SCANNER]` when `is_valid=False`.
