"""Fail if docs/sse-schema.json is out of sync with the Pydantic source.

Usage:
    python -m scripts.check_sse_schema [path]

Default path: docs/sse-schema.json
"""

import sys
from pathlib import Path

from scripts.export_sse_schema import render_schema


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/sse-schema.json")
    if not path.exists():
        print(f"error: {path} does not exist — run 'make sse-schema-snapshot'", file=sys.stderr)
        return 1

    expected = render_schema()
    actual = path.read_text()
    if actual == expected:
        print(f"ok: {path} matches Pydantic source")
        return 0

    print(
        f"error: {path} is out of date — run 'make sse-schema-snapshot' and commit the diff",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
