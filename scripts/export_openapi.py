"""Export the OpenAPI spec to a JSON file.

Usage:
    python -m scripts.export_openapi [output_path]

Default output: docs/openapi.json
"""

import json
import sys
from pathlib import Path


def main() -> None:
    from src.api.app import app

    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/openapi.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    spec = app.openapi()
    output_path.write_text(json.dumps(spec, indent=2) + "\n")
    print(f"OpenAPI spec written to {output_path}")


if __name__ == "__main__":
    main()
