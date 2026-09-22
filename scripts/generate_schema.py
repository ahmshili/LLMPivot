"""Regenerates config.schema.json from ai_gateway.config.models.GatewayConfig.

Run this after changing any config model so the shipped JSON Schema never
drifts from the actual Pydantic models the app validates against:

    uv run python scripts/generate_schema.py
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_gateway.config.models import GatewayConfig

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "config.schema.json"


def main() -> None:
    schema = GatewayConfig.model_json_schema()
    schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    schema["title"] = "AI Gateway Configuration"
    OUTPUT_PATH.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
