# OpenAI API Usage Audit

**Date:** 2026-04-23  
**Branch:** feat/domain-rag  
**Scope:** All `openai.AsyncOpenAI` call sites in `src/`

---

## TL;DR

Our core approach — **managed vector stores + Responses API `file_search` tool** — is correct and is exactly the path OpenAI recommends for hosted RAG. We do **not** need raw embeddings. Two issues found: one incorrect API call (`parse_document.py`) and one waste pattern (fresh client per invocation). Details below.

---

## Background: Two RAG approaches with OpenAI

Before auditing our code, it helps to understand what OpenAI actually offers.

### Option A — Managed Vector Stores (what we use)
OpenAI handles chunking, embedding, and indexing for you.

```
You:    Upload file → client.files.create(purpose="assistants")
You:    Create store → client.vector_stores.create()
You:    Index file  → client.vector_stores.files.create_and_poll(store_id, file_id)
OpenAI: Automatically chunks text, generates embeddings, stores them
Query:  client.responses.create(tools=[{type: "file_search", vector_store_ids: [...]}])
        OR client.vector_stores.search(vector_store_id, query)
```

### Option B — DIY Embeddings
You manage everything yourself.

```
You:    Chunk the text yourself
You:    client.embeddings.create(model="text-embedding-3-small", input=chunk)  → float[]
You:    Store vectors in your own DB (pgvector, Pinecone, Weaviate, etc.)
Query:  Embed the query → cosine similarity search in your DB → feed results to LLM
```

**Verdict on which to use:** Option A is correct for our use case. Option B only makes sense if you need your own vector DB (e.g., already have pgvector in Postgres), need extreme control over chunking, or have a scale/cost constraint that OpenAI's hosted service can't meet. For per-domain curated legal knowledge bases, Option A is simpler, more capable (hybrid semantic + keyword search with automatic reranking), and doesn't require a separate vector infrastructure.

---

## Audit by call site

### 1. Vector store provisioning — `src/services/knowledge_base.py`

```python
# Create
store = await client.vector_stores.create(
    name=f"domain-{domain_code}-{settings.namespace}",
    metadata={"domain_code": domain_code, ...}
)

# Index a file
vs_file = await client.vector_stores.files.create_and_poll(
    vector_store_id=store.id,
    file_id=file.id,
)

# Direct search (raw retrieval, no LLM)
results = await client.vector_stores.search(
    vector_store_id=vector_store_id,
    query=query,
    max_num_results=max_results,
)
```

**Status: CORRECT.**

- `client.vector_stores.create()` — correct top-level namespace (the `beta.` prefix belongs to the older Assistants API path; standalone vector stores have graduated out of beta)
- `files.create_and_poll()` — correct polling helper for indexing completion
- `client.vector_stores.search()` — this is the standalone search endpoint added in late 2024; it returns raw ranked chunks without going through an LLM. Used correctly in `search_kb()` which feeds the judge's personal knowledge base endpoint
- `purpose="assistants"` for uploaded files — confirmed correct by current docs

---

### 2. Domain guidance retrieval — `src/tools/search_domain_guidance.py`

```python
response = await client.responses.create(
    model=settings.openai_model_lightweight,
    input=f"Find relevant statutes, practice directions, or procedural rules for: {query}",
    tools=[{
        "type": "file_search",
        "vector_store_ids": [vector_store_id],
        "max_num_results": max_results,
    }]
)
```

**Status: CORRECT.**

This is the right modern approach. The Responses API (`/v1/responses`) with `file_search` is exactly what OpenAI's current documentation recommends for agent-side RAG. When the model executes this, it:
1. Automatically queries the vector store with hybrid semantic + keyword search
2. Reranks results
3. Incorporates the retrieved content into its response

The response structure we parse (`response.output` → `file_search_call` items → `results[].filename/text/score`) matches the documented schema.

**Minor issue:** A new `AsyncOpenAI` client is instantiated on every call to this function. See §5 below.

---

### 3. Precedent search fallback — `src/tools/vector_store_fallback.py`

Same pattern as §2. `client.responses.create()` with `file_search`. **CORRECT.**

Same minor client instantiation issue applies.

---

### 4. Document parsing — `src/tools/parse_document.py` ⚠️

```python
response = await client.chat.completions.create(
    model=settings.openai_model_lightweight,
    messages=[
        {"role": "system", "content": "Extract all text content..."},
        {
            "role": "user",
            "content": [
                {"type": "file", "file": {"file_id": file_id}},   # ← this line
                {"type": "text", "text": "Extract all text..."},
            ],
        },
    ],
    response_format={"type": "json_object"},
)
```

**Status: WORKS TODAY, BUT WRONG API.**

The `type: "file"` content part in a Chat Completions message is not a stable, documented feature of the `/v1/chat/completions` endpoint. It was quietly surfaced alongside the Responses API launch but is not in the Chat Completions reference. The Responses API is the correct and documented home for file inputs.

The equivalent using the correct API is:

```python
response = await client.responses.create(
    model=settings.openai_model_lightweight,
    input=[
        {
            "type": "input_file",
            "file_id": file_id,
        },
        {
            "type": "input_text",
            "text": "Extract all text from this document. Return JSON with keys: ...",
        }
    ],
    text={"format": {"type": "json_object"}},
)
# Result is in response.output_text (or the first message output item)
```

**Why this matters:**
- The `type: "file"` in Chat Completions is undocumented and could be deprecated or changed without notice
- The Responses API is OpenAI's stated direction for all new tool + multimodal work
- The current code functionally works but sits on an unofficial surface

**Recommended fix:** Migrate `parse_document.py` to `client.responses.create()` with `input_file`. The logic, retry decorator, and result processing remain unchanged — only the API call changes.

---

### 5. Client instantiation pattern — multiple files ⚠️

```python
# BAD — happens in search_domain_guidance.py and vector_store_fallback.py
async def search_domain_guidance(query, vector_store_id, ...):
    client = AsyncOpenAI(api_key=settings.openai_api_key)  # fresh every call
    ...
```

```python
# GOOD — knowledge_base.py already does this correctly
_client: AsyncOpenAI | None = None

def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client
```

**Why this matters:** `AsyncOpenAI()` construction initialises an `httpx.AsyncClient` with its own connection pool. Creating one per invocation means no connection reuse, no keep-alive benefit, and extra overhead on every tool call. For the legal-knowledge agent this can fire 4-6 tool calls per gate run.

**Recommended fix:** Extract a module-level `_get_client()` singleton in `search_domain_guidance.py`, `vector_store_fallback.py`, and `parse_document.py`, mirroring `knowledge_base.py`.

---

### 6. File upload `purpose` for domain documents — `src/api/routes/domains.py`

```python
oa_file = await client.files.create(
    file=(file.filename, content, content_type),
    purpose="assistants",
)
```

**Status: CORRECT.**

Current OpenAI documentation consistently shows `purpose="assistants"` for files destined for vector stores. The `purpose="responses"` value does not exist; `"assistants"` remains the correct value regardless of whether you access the file through the Assistants API or the standalone Responses API.

---

## Summary table

| Call site | API used | Status | Issue |
|---|---|---|---|
| `knowledge_base.py` — vector store CRUD | `client.vector_stores.*` | ✅ Correct | — |
| `knowledge_base.py` — direct search | `client.vector_stores.search()` | ✅ Correct | — |
| `search_domain_guidance.py` — RAG | `client.responses.create()` + `file_search` | ✅ Correct | Fresh client per call |
| `vector_store_fallback.py` — RAG | `client.responses.create()` + `file_search` | ✅ Correct | Fresh client per call |
| `parse_document.py` — text extraction | `client.chat.completions.create()` + `type:"file"` | ⚠️ Wrong API | Use `client.responses.create()` |
| `domains.py` — file upload | `client.files.create(purpose="assistants")` | ✅ Correct | — |
| `domains.py` — vector store indexing | `client.vector_stores.files.create_and_poll()` | ✅ Correct | — |

---

## Do we need raw embeddings instead?

No. The managed vector store approach is strictly better for our use case:

| | Managed (our approach) | Raw embeddings |
|---|---|---|
| Chunking | OpenAI automatic | You write + maintain |
| Embedding model | Managed, updated by OpenAI | You call `text-embedding-3-small` per chunk |
| Hybrid search (semantic + keyword) | Built-in | Not available without extra tooling |
| Reranking | Built-in | Not available without extra tooling |
| Vector DB | None needed | Requires pgvector/Pinecone/etc. |
| Infra overhead | Zero | Moderate |
| Use case fit | Curated per-domain documents | If you already have your own vector DB |

The only scenario where raw embeddings would make sense is if we needed to store vectors in our own Postgres instance (e.g., for data residency requirements or to avoid per-document storage costs at very high volume). Neither applies here.

---

## Recommended actions

**P1 — Fix `parse_document.py`:** Migrate from `chat.completions.create()` with undocumented `type:"file"` to `responses.create()` with `input_file`. Low risk, same behaviour, correct API surface.

**P2 — Singleton client pattern:** Extract `_get_client()` in `search_domain_guidance.py`, `vector_store_fallback.py`, and `parse_document.py`. Reduces overhead on every agent gate run.

Neither issue affects correctness today, but P1 is a latent breakage risk if OpenAI removes `type:"file"` from Chat Completions in a future SDK update.
