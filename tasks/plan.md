# Plan: parse_document cache short-circuit

## Problem

`parse_document_tool` in `src/pipeline/graph/tools.py:270–289` always makes a live
OpenAI Responses API call, even when the document text was already extracted and
stored in `CaseState.raw_documents` before the pipeline started.

`_hydrate_raw_documents` (cases.py:154) runs at pipeline start and populates every
`raw_documents` entry with `openai_file_id` and `parsed_text`. When `parsed_text` is
non-empty the document is already parsed — paying for a redundant API call on three
separate research agents (evidence, facts, witnesses) for the same files.

The docstring in `src/tools/parse_document.py:93–99` explicitly marks this as a D13
planned-but-unimplemented short-circuit.

## Root cause

The `parse_document_tool` closure defined inside `make_tools(state)` already captures
`state` from the outer scope (the same pattern used at tools.py:259 for
`vector_store_id`). It simply never queries `state["case"].raw_documents` before
dispatching to the API.

## Fix: one surgical closure edit

Inside `parse_document_tool` (tools.py:270–289), add a cache lookup before calling
`parse_document()`:

```python
# Cache hit path (pseudo):
case = state["case"]
for doc in case.raw_documents:
    if doc.get("openai_file_id") == file_id:
        parsed_text = doc.get("parsed_text") or ""
        if parsed_text:
            pages = doc.get("pages") or [{"page_number": 1, "text": parsed_text, "tables": []}]
            return {
                "file_id": file_id,
                "filename": doc.get("filename", ""),
                "content_type": "",
                "text": parsed_text,
                "pages": pages,
                "tables": [t for p in pages for t in (p.get("tables") or [])],
                "metadata": {"filename": doc.get("filename", ""), "content_type": ""},
                "parsing_notes": ["Served from CaseState cache (pre-parsed at pipeline start)."],
                "sanitization": SanitizationResult(
                    text=parsed_text, regex_hits=0, classifier_hits=0, chunks_scanned=len(pages)
                ),
            }
        break  # found the entry but not hydrated — fall through to API
# Cache miss path — existing call unchanged
return await parse_document(file_id=file_id, ...)
```

Cache key: `doc["openai_file_id"] == file_id` — the same OpenAI file ID the tool receives.
Cache hit condition: `parsed_text` is a non-empty string.

No schema changes needed. `raw_documents` is already `list[dict[str, Any]]` with
`openai_file_id` and `parsed_text` keys populated by `_hydrate_raw_documents`.

## Dependency graph

```
cases.py:_hydrate_raw_documents   → populates CaseState.raw_documents (already done)
CaseState.raw_documents           → available in state["case"] at tool closure time
make_tools(state)                 → closure captures state (already done at line 259)
parse_document_tool               → ADD: check raw_documents before calling API
parse_document.py docstring       → UPDATE: remove "unimplemented" note
tests/unit/test_parse_document_cache.py → NEW: verify hit/miss paths on the closure
```

## Files to touch

| File | Change |
|---|---|
| `src/pipeline/graph/tools.py:270–289` | Add cache lookup in `parse_document_tool` closure |
| `src/tools/parse_document.py:93–99` | Update docstring — mark D13 note as implemented |
| `tests/unit/test_parse_document_cache.py` | New — cache hit/miss unit tests on the closure |

## Non-goals

- No change to `src/tools/parse_document.py` logic (only its docstring)
- No schema changes to `CaseState` or `RawDocument`
- No change to `_hydrate_raw_documents` — it already populates the cache
- No change to existing `test_parse_document.py` tests (they test the function directly)
