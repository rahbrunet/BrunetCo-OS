"""Export the FastAPI OpenAPI schema to packages/contracts/openapi.json.

Run by `python make.py gen-contracts` before the TS client is regenerated. The committed
openapi.json + generated client are the drift target CI checks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make apps/api importable without installing it.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.main import app  # noqa: E402

OUT = ROOT / "packages" / "contracts" / "openapi.json"


def main() -> None:
    schema = app.openapi()
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
