"""Secret loading — Bitwarden Secrets Manager (D10) with a documented local-dev fallback.

Production: secrets come from Bitwarden Secrets Manager via the `bws` CLI, keyed by
BWS_ACCESS_TOKEN + BWS_PROJECT_ID, and injected into the process environment at startup.
Local dev: `.env.local` (gitignored) provides the same keys.

No secret material ever lives in the repo. See infra/secrets.md for the Bitwarden project layout.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess


def load_secrets_into_env() -> dict[str, str]:
    """Populate os.environ from Bitwarden if a token is present; otherwise rely on .env.local.

    Returns the dict of keys that were loaded from Bitwarden (empty if falling back to local).
    """
    token = os.environ.get("BWS_ACCESS_TOKEN", "")
    project = os.environ.get("BWS_PROJECT_ID", "")
    if not token or not project:
        # Local-dev fallback: pydantic-settings already reads .env.local in config.py.
        return {}

    bws = shutil.which("bws")
    if bws is None:
        raise RuntimeError(
            "BWS_ACCESS_TOKEN is set but the `bws` CLI is not installed. "
            "Install Bitwarden Secrets Manager CLI or unset the token for local dev."
        )

    # `bws secret list <project>` returns JSON [{key, value, ...}]. Never log values.
    proc = subprocess.run(
        [bws, "secret", "list", project],
        capture_output=True,
        text=True,
        env={**os.environ, "BWS_ACCESS_TOKEN": token},
        check=True,
    )
    loaded: dict[str, str] = {}
    for item in json.loads(proc.stdout):
        key, value = item["key"], item["value"]
        os.environ[key] = value
        loaded[key] = "***"  # redacted marker; never expose the value
    return loaded
