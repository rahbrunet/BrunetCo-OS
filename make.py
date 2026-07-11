#!/usr/bin/env python3
"""Cross-platform task runner for BrunetCo OS (Windows has no `make`).

Usage: python make.py <target>
Targets: dev | api | web | test | lint | gen-contracts | migrate | rls-proof
"""
from __future__ import annotations

import subprocess
import sys

TARGETS: dict[str, list[list[str]]] = {
    # Boot the Supabase local stack, then run API and web. `dev` prints guidance because
    # the three processes are long-running; run them in separate terminals or via a Procfile.
    "api": [["uv", "run", "uvicorn", "app.main:app", "--reload", "--app-dir", "apps/api"]],
    "web": [["pnpm", "--filter", "@brunetco/web", "dev"]],
    "migrate": [["npx", "supabase", "db", "reset", "--local"]],
    "gen-contracts": [
        # 1) export OpenAPI from the API, 2) regenerate the TS client
        ["uv", "run", "python", "scripts/export_openapi.py"],
        ["pnpm", "--filter", "@brunetco/contracts", "generate"],
    ],
    "lint": [
        ["uv", "run", "ruff", "check", "."],
        ["uv", "run", "mypy", "apps/api/app", "apps/workers/worker_app",
         "packages/py-shared/py_shared"],
        ["pnpm", "-r", "lint"],
    ],
    "test": [
        ["uv", "run", "pytest", "-q"],
        ["pnpm", "-r", "test", "--", "--run"],
    ],
    "rls-proof": [["uv", "run", "pytest", "-q", "apps/api/tests/test_rls.py"]],
}


def run(target: str) -> int:
    if target == "dev":
        print(
            "Start these in separate terminals:\n"
            "  1) npx supabase start        # Postgres + Auth on :54321/:54322\n"
            "  2) python make.py migrate    # apply migrations (demo RLS table)\n"
            "  3) python make.py api         # FastAPI on :8000\n"
            "  4) python make.py web         # Vite SPA on :5173\n"
        )
        return 0
    steps = TARGETS.get(target)
    if steps is None:
        print(f"Unknown target: {target}\nValid: dev, {', '.join(TARGETS)}", file=sys.stderr)
        return 2
    for cmd in steps:
        print(f"$ {' '.join(cmd)}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(run(sys.argv[1]))
