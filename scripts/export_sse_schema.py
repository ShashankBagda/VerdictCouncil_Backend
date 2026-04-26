"""Export the SSE event JSON Schema to a file.

Usage:
    python -m scripts.export_sse_schema [output_path]

Default output: docs/sse-schema.json
"""

import json
import sys
from pathlib import Path


def render_schema() -> str:
    from pydantic import TypeAdapter

    from src.api.schemas.pipeline_events import Event

    schema = TypeAdapter(Event).json_schema()
    return json.dumps(schema, indent=2) + "\n"


def main() -> None:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/sse-schema.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_schema())
    print(f"SSE schema written to {output_path}")


if __name__ == "__main__":
    main()
