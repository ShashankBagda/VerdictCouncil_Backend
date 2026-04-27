# Golden eval cases — Sprint 0 0.11b

Hand-curated cases for the LangSmith eval dataset (3.D1.1 syncs them).
Each fixture is a self-contained JSON document with three top-level keys:

```jsonc
{
  "metadata": { "id", "domain", "author", "date", "notes" },
  "inputs":   { "case_id", "domain", "parties", "case_metadata", "raw_documents" },
  "expected": {
    "intake":   { "domain", "parties_count", "claim_amount", "offence_code" },
    "research": {
      "legal_rules":   [ "statute citation strings expected to appear" ],
      "precedents":    [ "case citations expected to appear" ],
      "supporting_sources": [ "<file_id>:<sha256[:12]> placeholders" ]
    }
  }
}
```

## Source-id placeholders

`expected.research.supporting_sources` lists `source_id`s in the
`<file_id>:<sha256[:12]>` format produced by 3.B.1/3.B.2. The strings
here are **placeholders** — they encode the expected citation but the
file-id portion is `placeholder-<n>` because real file ids are owned
by the OpenAI vector store and are not deterministic across deployments.

`tests/eval/dataset_sync.py` (3.D1.1) is the boundary that reconciles
placeholders against the live store: when uploading the dataset, the
script substitutes the real file id for the document whose content
matches the citation. Until that script lands the placeholders pass
through unchanged and `CitationAccuracy` evaluator (3.D1.2) treats any
match where the `<file_id>` segment is `placeholder-*` as a soft pass.

## Counts

5 cases per domain (10 total). Adding cases is cheap — drop a new
JSON fixture in this directory; `dataset_sync.py` picks it up.
