"""Multi-provider LLM abstraction (WP 6.9 §1, D45).

One entry point — `get_llm_client(task)` — routes by *sensitivity*, not by price:

  * sensitive  -> AWS Bedrock (Claude). Client-facing and quality-critical work: reply drafting,
                  OA report sections, anything a client will read.
  * bulk       -> Fireworks.ai (cheap open models, US-hosted). Classification, intent gating,
                  triage, eval — high volume, low stakes, no prose reaches a client.

Two invariants hold regardless of route:

  1. Redaction runs before every provider, unconditionally (see `redaction.py`). Provider choice
     changes cost and quality; it never changes the confidentiality posture, because by the time
     a prompt reaches this module the identities are already gone.
  2. Fireworks stays gated until the no-training/zero-retention terms are in the signed contract
     (D45 owner gate; satisfied on public documentation only as of 2026-07-18). Until
     `llm_fireworks_enabled` is set, bulk tasks transparently route to Bedrock — more expensive,
     never less safe. The gate defaults closed so that forgetting to flip it costs money rather
     than confidentiality.

Residency, stated accurately: the legacy `draft.py` docstring claimed `ca-central-1` while
production ran the cross-region `us.` inference profile. There is no `ca.` profile for a current
Claude model. The real posture is *US processing, sandboxed, not trained on, de-identified before
egress* — that is what D45 ratified and what this module documents. The comfortable claim was the
wrong one; it is corrected here rather than carried forward.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import psycopg

from py_shared.config import settings

SENSITIVE = "sensitive"
BULK = "bulk"

PROVIDER_BEDROCK = "bedrock"
PROVIDER_FIREWORKS = "fireworks"

# Logical task -> sensitivity. Adding a task without an entry routes it to `sensitive`: an
# unclassified task is one nobody has thought about, and the safe default for those is the
# careful provider.
TASK_SENSITIVITY: dict[str, str] = {
    "draft_reply": SENSITIVE,       # A9 — a client reads the output
    "oa_report_section": SENSITIVE,  # A11
    "plan_project": SENSITIVE,       # 5.6 — orchestrator drafts a project plan from a description
    "draft_rule": SENSITIVE,         # 6.6 — A2 drafts a docket rule; a wrong rule mis-dates matters
    "quote_intent": BULK,            # A10 — gate a detector, discard the text
    "classify_intent": BULK,         # A8
    "classify_matter": BULK,         # M5 auto-filing suggestions
    "triage": BULK,
    "eval": BULK,
}


def sensitivity_of(task: str) -> str:
    return TASK_SENSITIVITY.get(task, SENSITIVE)


class LlmError(RuntimeError):
    """The provider call failed. Callers treat this as 'no draft', never as 'empty draft'."""


class LlmClient(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def complete(self, prompt: str, system: str = "") -> str: ...


@dataclass
class BedrockClient:
    """AWS Bedrock (Claude). boto3 is imported lazily so the module stays importable — and the
    routing logic stays unit-testable — without AWS credentials present."""

    model: str = "us.anthropic.claude-sonnet-4-6"
    region: str = "us-east-1"
    max_tokens: int = 2000

    @property
    def provider(self) -> str:
        return PROVIDER_BEDROCK

    def complete(self, prompt: str, system: str = "") -> str:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover — exercised only with boto3 absent
            raise LlmError("boto3 not installed; cannot reach Bedrock") from exc
        import json

        client = boto3.client("bedrock-runtime", region_name=self.region)
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        try:
            response = client.invoke_model(modelId=self.model, body=json.dumps(body))
            payload = json.loads(response["body"].read())
            return "".join(block.get("text", "") for block in payload.get("content", []))
        except Exception as exc:  # noqa: BLE001 — every boto failure is one failure to the caller
            raise LlmError(f"Bedrock call failed: {exc}") from exc


@dataclass
class FireworksClient:
    """Fireworks.ai (open models on US infrastructure). Weights of Chinese origin (DeepSeek/Qwen)
    run on US-hosted infrastructure — model provenance and data location are separate questions,
    and D45 turns on the latter."""

    model: str = "accounts/fireworks/models/deepseek-v3"
    api_key: str = ""
    max_tokens: int = 2000

    @property
    def provider(self) -> str:
        return PROVIDER_FIREWORKS

    def complete(self, prompt: str, system: str = "") -> str:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise LlmError("httpx not installed; cannot reach Fireworks") from exc

        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        try:
            response = httpx.post(
                "https://api.fireworks.ai/inference/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "max_tokens": self.max_tokens, "messages": messages},
                timeout=60.0,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except Exception as exc:  # noqa: BLE001
            raise LlmError(f"Fireworks call failed: {exc}") from exc


# Injectable factories, so tests (and a future provider swap) never patch module internals.
ClientFactory = Callable[[], LlmClient]

_FACTORIES: dict[str, ClientFactory] = {
    PROVIDER_BEDROCK: lambda: BedrockClient(
        model=settings.llm_bedrock_model, region=settings.llm_bedrock_region
    ),
    PROVIDER_FIREWORKS: lambda: FireworksClient(model=settings.llm_fireworks_model),
}


def register_client_factory(provider: str, factory: ClientFactory) -> None:
    """Override a provider's construction (tests, or a credential-broker-backed factory)."""
    _FACTORIES[provider] = factory


def provider_for(task: str) -> str:
    """Which provider a task routes to, honouring the D45 Fireworks contract gate."""
    if sensitivity_of(task) == BULK and settings.llm_fireworks_enabled:
        return PROVIDER_FIREWORKS
    return PROVIDER_BEDROCK


def get_llm_client(task: str) -> LlmClient:
    """The client for ``task``. Routing is a property of the task, never of the call site — so a
    caller cannot quietly send client-facing prose to the cheap provider by passing a flag."""
    return _FACTORIES[provider_for(task)]()


def log_egress(
    conn: psycopg.Connection,
    agent_name: str,
    task: str,
    client: LlmClient,
    redaction_ref: str,
    prompt_chars: int,
    status: str = "sent",
    detail: str | None = None,
) -> None:
    """Record one external provider call against its redaction reference (D45 audit spine).

    The foreign key to `ops.redaction_events` is the enforcement: an egress row cannot exist
    without a redaction row to point at, so 'we sent it unredacted' is not a representable state.
    """
    conn.execute(
        """
        insert into ops.llm_egress_log
          (agent_name, task, sensitivity, provider, model, redaction_ref, prompt_chars,
           status, detail)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (agent_name, task, sensitivity_of(task), client.provider, client.model, redaction_ref,
         prompt_chars, status, detail),
    )
