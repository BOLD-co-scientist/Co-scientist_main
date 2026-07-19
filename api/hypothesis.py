"""Parallel-hypothesis generation via a direct Fable API call.

INTERIM IMPLEMENTATION. The human enters a research goal; we ask the model for
a few *parallel* competing hypotheses. The human either selects one or picks one
and says what to do differently, and we generate a fresh parallel set derived
from that choice. No tree, no tournament — just rounds of parallel candidates
the human steers.

TODO(H1): swap this out for the full Google AI co-scientist protocol
(generate → reflect → rank via Elo tournament → evolve → meta-review). The REST
+ event contract and data model for that are pinned in
docs/plans/H1-hypothesis-coscientist.md; this module is the placeholder that
lets the UI ship against real generations now.

Model note: the user asked for Fable. Fable's safety classifiers frequently
refuse benign life-sciences / chemistry content (`stop_reason == "refusal"`),
which a science co-scientist hits constantly — so we call Fable first and, on a
refusal or error, fall back to Opus 4.8 (client-side, since this anthropic SDK
predates the server-side `fallbacks` param). The response records which model
actually served the set.
"""

from __future__ import annotations

import json
import os
import re
import uuid

from anthropic import AsyncAnthropic


HYPOTHESIS_MODEL = os.environ.get("COSCIENTIST_HYPOTHESIS_MODEL", "claude-fable-5")
HYPOTHESIS_FALLBACK_MODEL = os.environ.get("COSCIENTIST_HYPOTHESIS_FALLBACK", "claude-opus-4-8")
_MAX_TOKENS = 2000


def _system() -> str:
    return (
        "You are a scientific hypothesis generator for a human researcher. Given a "
        "research goal, propose DISTINCT, competing hypotheses that attack the goal "
        "from genuinely different angles (different mechanisms, assumptions, or "
        "levels of analysis) — not variations on one idea. Each hypothesis must be "
        "specific and testable.\n\n"
        "Respond with ONLY a JSON array (no prose, no markdown fences). Each element "
        'is an object with exactly these keys: "statement" (one clear sentence stating '
        'the hypothesis) and "rationale" (1-2 sentences on the reasoning and how it '
        "could be tested)."
    )


def _user(goal: str, parent: dict | None, feedback: str | None, n: int) -> str:
    if parent is None:
        return f"Research goal: {goal}\n\nGenerate {n} distinct, competing hypotheses."
    lines = [
        f"Research goal: {goal}",
        "",
        "The researcher chose to build on this hypothesis:",
        f"- {parent.get('statement', '')}",
    ]
    if parent.get("rationale"):
        lines.append(f"  (rationale: {parent['rationale']})")
    if feedback and feedback.strip():
        lines += ["", f"They want the next set to account for this direction: {feedback.strip()}"]
    lines += [
        "",
        f"Generate {n} new distinct hypotheses that descend from the chosen one — "
        "refine it, combine it with a complementary idea, or push it further — while "
        "staying genuinely different from each other.",
    ]
    return "\n".join(lines)


def _parse(text: str, n: int) -> list[dict]:
    """Extract a JSON array of {statement, rationale} from the model text,
    tolerating stray prose or code fences."""
    raw = text.strip()
    # Strip markdown fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    # Fall back to the first bracketed array in the text.
    if not raw.startswith("["):
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            raw = m.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        if not statement:
            continue
        out.append({"statement": statement, "rationale": str(item.get("rationale", "")).strip()})
    return out[:n]


async def _call(client: AsyncAnthropic, model: str, system: str, user: str) -> tuple[str, str | None]:
    """Return (text, refusal) — refusal is a reason string if the model declined."""
    resp = await client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        return "", "refusal"
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return text, (None if text.strip() else "empty")


async def generate(
    goal: str, parent: dict | None = None, feedback: str | None = None, n: int = 4
) -> tuple[list[dict], str]:
    """Generate `n` parallel hypotheses. Returns (hypotheses, served_by_model).

    Tries the Fable model first; on a refusal/empty/error, retries on the
    fallback model so science content (which Fable often declines) still works.
    """
    client = AsyncAnthropic()
    system, user = _system(), _user(goal, parent, feedback, n)

    served = HYPOTHESIS_MODEL
    text = ""
    try:
        text, refusal = await _call(client, HYPOTHESIS_MODEL, system, user)
        if refusal:
            served = HYPOTHESIS_FALLBACK_MODEL
            text, _ = await _call(client, HYPOTHESIS_FALLBACK_MODEL, system, user)
    except Exception:
        # Any API error on the primary model → try the fallback once.
        served = HYPOTHESIS_FALLBACK_MODEL
        text, _ = await _call(client, HYPOTHESIS_FALLBACK_MODEL, system, user)

    hyps = _parse(text, n)
    return hyps, served
