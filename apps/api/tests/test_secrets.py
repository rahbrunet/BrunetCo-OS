"""Bitwarden secret fetcher (D10) — no DB, no network, no real credential.

The `bws` CLI is injected as a runner so these tests exercise the caching/refresh/miss semantics
the credential broker depends on.
"""
from __future__ import annotations

import json

import pytest
from py_shared.secrets import (
    BitwardenSecretFetcher,
    SecretNotFound,
    SecretUnavailable,
    default_secret_fetcher,
    load_secrets_into_env,
)


class FakeBws:
    """Stands in for `bws secret list <project>`; records how often it was called."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = secrets
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        return json.dumps([{"key": k, "value": v} for k, v in self.secrets.items()])


def test_fetcher_resolves_a_slot() -> None:
    bws = FakeBws({"cipo/twocaptcha-api-key": "k-123"})
    fetch = BitwardenSecretFetcher("tok", "proj", runner=bws)
    assert fetch("cipo/twocaptcha-api-key") == "k-123"
    assert bws.calls == [["bws", "secret", "list", "proj"]]


def test_fetcher_caches_the_project_listing() -> None:
    bws = FakeBws({"a/one": "1", "a/two": "2"})
    fetch = BitwardenSecretFetcher("tok", "proj", runner=bws)
    assert fetch("a/one") == "1"
    assert fetch("a/two") == "2"
    assert len(bws.calls) == 1, "second known slot must come from cache, not another CLI call"


def test_fetcher_refreshes_once_on_a_miss_then_resolves() -> None:
    """A slot added to Bitwarden after startup resolves without restarting the process."""
    bws = FakeBws({"a/one": "1"})
    fetch = BitwardenSecretFetcher("tok", "proj", runner=bws)
    assert fetch("a/one") == "1"
    bws.secrets["a/new"] = "n"
    assert fetch("a/new") == "n"
    assert len(bws.calls) == 2


def test_fetcher_raises_not_found_after_refresh() -> None:
    bws = FakeBws({"a/one": "1"})
    fetch = BitwardenSecretFetcher("tok", "proj", runner=bws)
    with pytest.raises(SecretNotFound):
        fetch("a/absent")
    assert len(bws.calls) == 2, "a miss refreshes exactly once before giving up"


def test_fetcher_never_puts_the_value_in_the_error() -> None:
    bws = FakeBws({"a/one": "super-secret-value"})
    fetch = BitwardenSecretFetcher("tok", "proj", runner=bws)
    with pytest.raises(SecretNotFound) as exc:
        fetch("a/absent")
    assert "super-secret-value" not in str(exc.value)


def test_fetcher_refresh_picks_up_a_rotated_value() -> None:
    bws = FakeBws({"a/one": "old"})
    fetch = BitwardenSecretFetcher("tok", "proj", runner=bws)
    assert fetch("a/one") == "old"
    bws.secrets["a/one"] = "new"
    assert fetch("a/one") == "old", "cached until an explicit refresh"
    fetch.refresh()
    assert fetch("a/one") == "new"


def test_unparseable_cli_output_is_secret_unavailable() -> None:
    fetch = BitwardenSecretFetcher("tok", "proj", runner=lambda argv: "not json")
    with pytest.raises(SecretUnavailable):
        fetch("a/one")


def test_default_fetcher_is_none_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Bitwarden config → the broker uses its dev placeholder (never a hard error)."""
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BWS_PROJECT_ID", raising=False)
    assert default_secret_fetcher() is None


def test_default_fetcher_is_wired_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("BWS_PROJECT_ID", "proj")
    bws = FakeBws({"cipo/twocaptcha-api-key": "k-123"})
    fetch = default_secret_fetcher(runner=bws)
    assert fetch is not None
    assert fetch("cipo/twocaptcha-api-key") == "k-123"


def test_load_secrets_into_env_redacts_returned_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWS_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("BWS_PROJECT_ID", "proj")
    monkeypatch.delenv("SOME_KEY", raising=False)
    loaded = load_secrets_into_env(runner=FakeBws({"SOME_KEY": "v"}))
    assert loaded == {"SOME_KEY": "***"}
    import os

    assert os.environ["SOME_KEY"] == "v"


def test_load_secrets_into_env_noop_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BWS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("BWS_PROJECT_ID", raising=False)
    assert load_secrets_into_env() == {}
