# OpenAPI Contract

`docs/openapi.json` is the canonical contract between this backend and any
client (notably `VerdictCouncil_Frontend`). It is committed to the repo so that
drift between the running app and the spec is visible in PR diffs.

## Rules

- The frontend must not call paths that are absent from `docs/openapi.json`.
  If you need a new route, add it to FastAPI, regenerate the snapshot, and
  commit the updated JSON in the same PR.
- Do not hand-edit `docs/openapi.json`. Every change must come from the app.
- CI runs `make openapi-check` on every PR. A stale snapshot fails the build.

## Regenerating

```bash
make openapi-snapshot
```

Runs `python -m scripts.export_openapi docs/openapi.json`. The script imports
`src.api.app:app` and writes the FastAPI-generated spec.

## Verifying

```bash
make openapi-check
```

Regenerates into `docs/openapi.json` and fails if `git diff --exit-code` shows
changes. Used in CI; run locally before opening a PR that touches routes.
