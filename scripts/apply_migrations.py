"""Apply supabase/migrations/*.sql (and optionally seed.sql) in order to SUPABASE_DB_URL.

A psql-free migration applier for local dev / throwaway Postgres containers (Windows has no
psql on PATH by default). CI still uses psql; this mirrors it for local gates.

Usage: uv run python scripts/apply_migrations.py [--seed]
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg
from py_shared.config import settings

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))


def main() -> None:
    files = list(MIGRATIONS)
    if "--seed" in sys.argv:
        files.append(ROOT / "supabase" / "seed.sql")
    with psycopg.connect(settings.supabase_db_url, autocommit=True) as conn:
        for f in files:
            print(f"applying {f.name}")
            conn.execute(f.read_text(encoding="utf-8"))
    print("done")


if __name__ == "__main__":
    main()
