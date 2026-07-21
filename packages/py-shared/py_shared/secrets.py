"""Secret loading — Bitwarden Secrets Manager (D10) with a documented local-dev fallback.

Two distinct paths, both backed by the same `bws` CLI and the same project:

* **Startup env injection** — ``load_secrets_into_env()`` pulls every secret in the project into
  ``os.environ`` once at process start (DB URL, JWT secret, tenant ids …). Local dev uses
  ``.env.local`` (gitignored) instead.
* **Runtime broker fetch** — ``BitwardenSecretFetcher`` resolves a single *slot* on demand
  (``cipo/twocaptcha-api-key``, …). This is the fetcher the credential broker
  (``py_shared.orchestrator.fetch_secret``) injects, so a slot is only ever read after the
  agent's allow-list has authorized it. Slot names are the Bitwarden secret keys verbatim.

No secret material ever lives in the repo, and no value is ever logged or put in an exception
message. See infra/secrets.md for the Bitwarden project layout.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable

# Injectable for tests: takes the full argv, returns stdout. Never receives or returns a value we
# log — the real runner shells out to `bws`.
Runner = Callable[[list[str]], str]


class SecretUnavailable(RuntimeError):
    """Bitwarden could not be reached, or the CLI is missing while a token is configured."""


class SecretNotFound(KeyError):
    """The slot is authorized but no secret with that key exists in the Bitwarden project."""


def _resolve_bws() -> str:
    bws = shutil.which("bws")
    if bws is None:
        raise SecretUnavailable(
            "BWS_ACCESS_TOKEN is set but the `bws` CLI is not installed. "
            "Install Bitwarden Secrets Manager CLI or unset the token for local dev."
        )
    return bws


def _default_runner(argv: list[str]) -> str:
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        # stderr can echo the request but not values; still keep it short and unlogged upstream.
        raise SecretUnavailable(
            f"`bws` exited {proc.returncode}: {proc.stderr.strip()[:200] or 'no stderr'}"
        )
    return proc.stdout


def _list_project_secrets(project: str, token: str, runner: Runner | None = None) -> dict[str, str]:
    """`bws secret list <project>` → {key: value}. Values are returned to the caller only."""
    if runner is None:
        os.environ["BWS_ACCESS_TOKEN"] = token  # the CLI reads the token from the environment
        argv = [_resolve_bws(), "secret", "list", project]
        stdout = _default_runner(argv)
    else:
        stdout = runner(["bws", "secret", "list", project])
    try:
        items = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SecretUnavailable(f"could not parse `bws secret list` output: {exc}") from exc
    return {item["key"]: item["value"] for item in items}


class BitwardenSecretFetcher:
    """Resolve broker slots from Bitwarden, caching the project listing per process.

    The cache exists because the broker fetches one slot at a time while `bws` can only list a
    whole project — without it every agent run would shell out again. A rotated secret is picked
    up on the next process start, or explicitly via :meth:`refresh`. A miss refreshes once before
    raising, so a *newly added* slot works without a restart.
    """

    def __init__(self, token: str, project: str, runner: Runner | None = None) -> None:
        self._token = token
        self._project = project
        self._runner = runner
        self._cache: dict[str, str] | None = None

    def refresh(self) -> None:
        self._cache = _list_project_secrets(self._project, self._token, self._runner)

    def __call__(self, slot: str) -> str:
        if self._cache is None:
            self.refresh()
        assert self._cache is not None
        if slot not in self._cache:
            self.refresh()  # the slot may have been added since this process started
            assert self._cache is not None
        try:
            return self._cache[slot]
        except KeyError as exc:
            raise SecretNotFound(
                f"no secret keyed {slot!r} in Bitwarden project {self._project!r}"
            ) from exc


def default_secret_fetcher(runner: Runner | None = None) -> Callable[[str], str] | None:
    """The broker's production fetcher, or ``None`` when Bitwarden is not configured.

    Returning ``None`` (rather than raising) is what lets local dev and CI exercise the broker's
    authorization path against a placeholder without any real credential.
    """
    token = os.environ.get("BWS_ACCESS_TOKEN", "")
    project = os.environ.get("BWS_PROJECT_ID", "")
    if not token or not project:
        return None
    return BitwardenSecretFetcher(token, project, runner)


def load_secrets_into_env(runner: Runner | None = None) -> dict[str, str]:
    """Populate os.environ from Bitwarden if a token is present; otherwise rely on .env.local.

    Returns the loaded keys with redacted markers (empty if falling back to local dev), so a
    caller can log *which* secrets arrived without ever logging one.
    """
    token = os.environ.get("BWS_ACCESS_TOKEN", "")
    project = os.environ.get("BWS_PROJECT_ID", "")
    if not token or not project:
        # Local-dev fallback: pydantic-settings already reads .env.local in config.py.
        return {}

    loaded: dict[str, str] = {}
    for key, value in _list_project_secrets(project, token, runner).items():
        os.environ[key] = value
        loaded[key] = "***"  # redacted marker; never expose the value
    return loaded
