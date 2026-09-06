"""One place for the platform's direct model calls (not the agent runtimes).

Fable is the model the product asks for, and its safety classifiers refuse a
lot of benign life-science / chemistry text (`stop_reason == "refusal"`). Every
platform-side call therefore goes Fable first and falls back to Opus on a
refusal, empty answer or API error — client-side, since this anthropic SDK
predates server-side fallbacks. The result records which model actually served
and the token usage so the caller can persist both (R5's ledger sums them).

Used by api/hypothesis.py (parallel hypothesis sets) and api/advisor.py (the
background project-tree advisor). Pure I/O helper + tolerant JSON parsing; no
prompt content lives here.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic


PRIMARY_MODEL = os.environ.get("COSCIENTIST_PLATFORM_MODEL", os.environ.get("COSCIENTIST_HYPOTHESIS_MODEL", "claude-fable-5"))
FALLBACK_MODEL = os.environ.get("COSCIENTIST_PLATFORM_FALLBACK", os.environ.get("COSCIENTIST_HYPOTHESIS_FALLBACK", "claude-opus-4-8"))


@dataclass
class LLMResult:
    text: str
    served_by: str
    usage: dict[str, int] = field(default_factory=dict)  # {input_tokens, output_tokens}
    attempts: list[dict[str, Any]] = field(default_factory=list)  # [{model, outcome}]
    error: str | None = None  # set when BOTH models failed

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


async def _call(client: AsyncAnthropic, model: str, system: str, user: str, max_tokens: int) -> tuple[str, str | None, dict[str, int]]:
    """Return (text, failure_reason, usage). failure_reason is None on success."""
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    usage: dict[str, int] = {}
    u = getattr(resp, "usage", None)
    if u is not None:
        usage = {
            "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
        }
    if getattr(resp, "stop_reason", None) == "refusal":
        return "", "refusal", usage
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return text, (None if text.strip() else "empty"), usage


async def complete(
    system: str,
    user: str,
    *,
    max_tokens: int = 4000,
    primary: str | None = None,
    fallback: str | None = None,
    api_key: str | None = None,
) -> LLMResult:
    """Fable first; Opus on refusal / empty / error. Never raises: a double
    failure comes back as ``LLMResult(error=...)`` so callers degrade cleanly."""
    primary = primary or PRIMARY_MODEL
    fallback = fallback or FALLBACK_MODEL
    client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()
    attempts: list[dict[str, Any]] = []
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    try:
        for model in (primary, fallback):
            if not model:
                continue
            try:
                text, why, usage = await _call(client, model, system, user, max_tokens)
            except Exception as e:  # noqa: BLE001 — any API error → next model
                attempts.append({"model": model, "outcome": f"error: {e.__class__.__name__}: {str(e)[:200]}"})
                continue
            for k in usage_total:
                usage_total[k] += usage.get(k, 0)
            if why:
                attempts.append({"model": model, "outcome": why})
                continue
            attempts.append({"model": model, "outcome": "ok"})
            return LLMResult(text=text, served_by=model, usage=usage_total, attempts=attempts)
    finally:
        await client.close()
    return LLMResult(
        text="",
        served_by=fallback or primary,
        usage=usage_total,
        attempts=attempts,
        error="; ".join(f"{a['model']}: {a['outcome']}" for a in attempts) or "no model configured",
    )


def _strip_fences(raw: str) -> str:
    t = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    return fence.group(1).strip() if fence else t


def parse_json_array(text: str) -> list:
    """Extract a JSON array from model text, tolerating prose or code fences."""
    raw = _strip_fences(text)
    if not raw.startswith("["):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def parse_json_object(text: str) -> dict:
    """Extract the outermost JSON object from model text (fences / prose tolerated)."""
    raw = _strip_fences(text)
    a, b = raw.find("{"), raw.rfind("}")
    if a != -1 and b > a:
        raw = raw[a : b + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
