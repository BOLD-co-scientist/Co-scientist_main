"""R19 — LLM-as-judge on a *different* provider (OpenAI).

Two jobs, both HyperAgents' "evaluation" step adapted to a setting with no
benchmark:

* **Gate 1 (proposal):** when the planner proposes a change, the judge rates it
  and writes a one-line recommendation. In *manual* mode the human decides with
  that recommendation in view; in *automatic* mode the judge's verdict is the
  decision and the human is told why.
* **Gate 2 (merge):** when the evolution agent asks to merge, the judge reviews
  the diff preview, rationale and smoke result. Manual mode → recommendation
  beside the approval card; automatic mode → the judge answers the gate.

The judge learns the researcher's taste **in context**: every prompt carries the
decision history (what the human accepted/declined and why, and what the judge
said at the time). ``calibration()`` measures judge/human agreement from the
same records so drift is visible.

Key: ``COSCIENTIST_JUDGE_OPENAI_API_KEY`` → ``OPENAI_API_KEY`` → the file
``<platform state>/secrets/judge_openai_key``. Model: ``COSCIENTIST_JUDGE_MODEL``
(default the user's ask, ``gpt-6-extra``), resolved against the account's model
list with a preference fallback, so an unavailable id degrades instead of failing.
The model call is injectable (``Judge(call=...)``) for offline tests.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from scaffold import config_loader, settings

from . import llm

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "judge.md"

JUDGE_MODEL = os.environ.get("COSCIENTIST_JUDGE_MODEL", "gpt-6-extra")
MODEL_PREFERENCE = [JUDGE_MODEL, "gpt-6-extra", "gpt-6", "gpt-5.5", "gpt-5.2", "gpt-5.1", "gpt-5", "o3", "gpt-4.1", "gpt-4o"]
HISTORY_LIMIT = 24
DIFF_PREVIEW_CHARS = 9000

_PLACEHOLDER_KEYS = {"", "sk-...", "sk-xxx", "changeme"}


def _key_file() -> Path:
    return settings.STATE / "secrets" / "judge_openai_key"


def api_key() -> str | None:
    for name in ("COSCIENTIST_JUDGE_OPENAI_API_KEY", "OPENAI_API_KEY"):
        v = (os.environ.get(name) or "").strip()
        if v and v not in _PLACEHOLDER_KEYS and len(v) > 20:
            return v
    try:
        v = _key_file().read_text(encoding="utf-8").strip()
        if v and len(v) > 20:
            return v
    except OSError:
        pass
    return None


def available() -> bool:
    return api_key() is not None


CallFn = Callable[[list[str], str, str], Awaitable[tuple[str, str, dict]]]


@dataclass
class Verdict:
    verdict: str                     # "approve" | "decline" | "unavailable"
    score: float = 0.0
    why: str = ""
    risks: list[str] = field(default_factory=list)
    recommendation: str = ""
    served_by: str = ""
    usage: dict = field(default_factory=dict)
    error: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.verdict in ("approve", "decline")

    @property
    def approved(self) -> bool:
        return self.ok and self.verdict == "approve"

    def to_record(self, stage: str) -> dict:
        return {
            "stage": stage,
            "verdict": self.verdict,
            "score": round(float(self.score), 3),
            "why": self.why,
            "risks": list(self.risks),
            "recommendation": self.recommendation,
            "served_by": self.served_by,
            "usage": dict(self.usage),
            "error": self.error,
            "at": _now(),
        }


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_RESOLVED_MODEL: str | None = None


async def _resolve_model(client: Any, preference: list[str]) -> str:
    global _RESOLVED_MODEL
    if _RESOLVED_MODEL:
        return _RESOLVED_MODEL
    try:
        page = await client.models.list()
        ids = {m.id for m in getattr(page, "data", []) or []}
    except Exception:  # noqa: BLE001 — listing is a convenience; fall back to the first preference
        ids = set()
    chosen = next((m for m in preference if m and m in ids), None) or next((m for m in preference if m), "gpt-4.1")
    _RESOLVED_MODEL = chosen
    return chosen


async def openai_call(preference: list[str], system: str, user: str) -> tuple[str, str, dict]:
    """Default call: OpenAI chat completions, JSON mode, model resolved against
    the account's model list. Returns (text, model, usage)."""
    key = api_key()
    if not key:
        raise RuntimeError("judge unavailable: no OpenAI API key configured")
    from openai import AsyncOpenAI

    base = (os.environ.get("COSCIENTIST_JUDGE_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "").strip() or None
    client = AsyncOpenAI(api_key=key, base_url=base)
    try:
        model = await _resolve_model(client, preference)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            resp = await client.chat.completions.create(model=model, messages=messages, response_format={"type": "json_object"})
        except Exception as e:  # noqa: BLE001 — some models reject response_format; retry plain
            if "response_format" not in str(e) and "json_object" not in str(e):
                raise
            resp = await client.chat.completions.create(model=model, messages=messages)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        u = getattr(resp, "usage", None)
        usage = {
            "input_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(u, "completion_tokens", 0) or 0),
        } if u is not None else {}
        return text, model, usage
    finally:
        await client.close()


def _render_proposal(p: dict) -> str:
    lines = [
        f"Title: {p.get('title', '')}",
        f"Scope: {p.get('scope', 'harness')}   Direction: {p.get('direction', '')}   Serves goals: {', '.join(p.get('goal_ids') or []) or '(none stated)'}",
        f"Expected gain: {p.get('expected_gain', '?')}   Cost: {p.get('cost', '?')}",
        f"Why now: {p.get('why_now', '')}",
        f"Rationale: {p.get('rationale', '')}",
        "Command the evolution agent would receive:",
        (p.get("command") or "").strip(),
    ]
    if p.get("provenance"):
        lines.append("Provenance: " + ", ".join(str(x) for x in p["provenance"][:8]))
    return "\n".join(lines)


def _render_merge(payload: dict, proposal: dict | None) -> str:
    diff = str(payload.get("diff_preview") or "")
    if len(diff) > DIFF_PREVIEW_CHARS:
        diff = diff[:DIFF_PREVIEW_CHARS] + "\n… (diff truncated)"
    smoke = payload.get("smoke") or {}
    lines = []
    if proposal:
        lines.append("The proposal this evolution implements:\n" + _render_proposal(proposal) + "\n")
    lines += [
        f"Merge summary: {payload.get('summary', '')}",
        f"Agent's rationale: {payload.get('rationale', '')}",
        f"Branch: {payload.get('branch', '')}   Strict path (scaffold/evolution/Dockerfile touched): {bool(payload.get('strict'))}",
        f"Smoke gate: {'ran, ' + ('passed' if smoke.get('ok') else 'FAILED') if smoke.get('ran') else 'not required for this path'}",
        "Diff preview:",
        diff or "(no diff preview)",
    ]
    return "\n".join(lines)


def history_block(decisions: list[dict], proposals_by_id: dict[str, dict], *, limit: int = HISTORY_LIMIT) -> str:
    """Render the decision history for the prompt: per proposal, what the judge
    said and what the human decided, newest last."""
    if not decisions:
        return "(no decisions yet — this researcher has not accepted or declined anything so far)"
    per: dict[str, dict] = {}
    order: list[str] = []
    for d in decisions:
        pid = d.get("proposal_id")
        if not pid:
            continue
        if pid not in per:
            per[pid] = {"judge": None, "human": None, "merge_judge": None, "merge_human": None}
            order.append(pid)
        slot = ("merge_" if d.get("stage") == "merge" else "") + ("judge" if d.get("actor") == "judge" else "human")
        per[pid][slot] = d
    lines: list[str] = []
    for pid in order[-limit:]:
        p = proposals_by_id.get(pid) or {}
        rec = per[pid]
        head = f"- {p.get('title') or pid} [{p.get('scope', 'harness')}/{p.get('direction', '?')}]"
        parts = []
        if rec["judge"]:
            parts.append(f"judge said {str(rec['judge'].get('decision', '')).upper()} ({rec['judge'].get('score', '?')}): {rec['judge'].get('note', '')}")
        if rec["human"]:
            parts.append(f"human {str(rec['human'].get('decision', '')).upper()}" + (f" — note: {rec['human'].get('note')}" if rec["human"].get("note") else ""))
        if rec["merge_judge"]:
            parts.append(f"merge judge {str(rec['merge_judge'].get('decision', '')).upper()}: {rec['merge_judge'].get('note', '')}")
        if rec["merge_human"]:
            parts.append(f"merge human {str(rec['merge_human'].get('decision', '')).upper()}" + (f" — {rec['merge_human'].get('note')}" if rec["merge_human"].get("note") else ""))
        lines.append(head + "\n    " + "; ".join(parts))
    text = "\n".join(lines)
    return text[-6000:]


def calibration(decisions: list[dict]) -> dict:
    """Judge-vs-human agreement on gate 1 (and gate 2), from the decision log."""
    per: dict[str, dict] = {}
    for d in decisions:
        pid = d.get("proposal_id")
        if not pid:
            continue
        slot = ("merge_" if d.get("stage") == "merge" else "") + ("judge" if d.get("actor") == "judge" else "human")
        per.setdefault(pid, {})[slot] = d.get("decision")
    out = {"gate1": _agreement(per, "judge", "human", accept={"pick", "both", "approve"}), "gate2": _agreement(per, "merge_judge", "merge_human", accept={"approve"})}
    out["n_proposals"] = len(per)
    return out


def _agreement(per: dict[str, dict], jslot: str, hslot: str, *, accept: set[str]) -> dict:
    n = agree = j_yes = h_yes = 0
    disagreements: list[dict] = []
    for pid, rec in per.items():
        j, h = rec.get(jslot), rec.get(hslot)
        if not j or not h:
            continue
        n += 1
        jy, hy = (j in accept), (h in accept)
        j_yes += jy
        h_yes += hy
        if jy == hy:
            agree += 1
        else:
            disagreements.append({"proposal_id": pid, "judge": j, "human": h})
    return {
        "pairs": n,
        "agreement": round(agree / n, 3) if n else None,
        "judge_accept_rate": round(j_yes / n, 3) if n else None,
        "human_accept_rate": round(h_yes / n, 3) if n else None,
        "disagreements": disagreements[-10:],
    }


class Judge:
    def __init__(self, *, call: CallFn | None = None, preference: list[str] | None = None):
        self._call = call or openai_call
        self._preference = preference or MODEL_PREFERENCE
        self._injected = call is not None

    def available(self) -> bool:
        return self._injected or available()

    @property
    def model(self) -> str:
        return _RESOLVED_MODEL or self._preference[0]

    async def _ask(self, *, stage: str, mode: str, goals: str, inventory: str, tree: str, history: str, subject: str) -> Verdict:
        if not self.available():
            return Verdict(verdict="unavailable", error="judge unavailable: no OpenAI API key configured")
        system = config_loader.render(
            PROMPT_PATH.read_text(encoding="utf-8"),
            STAGE=stage,
            MODE=mode,
            HISTORY=history or "(none)",
            GOALS=goals or "(no goal ledger)",
            HARNESS_INVENTORY=inventory or "(unknown)",
            TREE=tree or "(no versions yet)",
            SUBJECT=subject,
        )
        try:
            text, served_by, usage = await self._call(self._preference, system, "Judge now. Output only the JSON object.")
        except Exception as e:  # noqa: BLE001 — a judge failure must never break the flow
            return Verdict(verdict="unavailable", error=f"{e.__class__.__name__}: {str(e)[:300]}")
        data = llm.parse_json_object(text)
        if not data:
            return Verdict(verdict="unavailable", served_by=served_by, usage=usage, error="judge returned no JSON", raw={"text": text[:2000]})
        verdict_raw = str(data.get("verdict", "")).strip().lower()
        verdict = "approve" if verdict_raw.startswith("approv") else "decline"
        try:
            score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        risks = data.get("risks") if isinstance(data.get("risks"), list) else []
        return Verdict(
            verdict=verdict,
            score=score,
            why=str(data.get("why", "")).strip()[:1200],
            risks=[str(r).strip()[:300] for r in risks][:8],
            recommendation=str(data.get("recommendation", "")).strip()[:400],
            served_by=served_by,
            usage=usage,
            raw=data,
        )

    async def judge_proposal(self, proposal: dict, *, goals: str, inventory: str, tree: str, history: str, mode: str) -> Verdict:
        return await self._ask(
            stage="gate 1 — should this proposed change be built?",
            mode=mode, goals=goals, inventory=inventory, tree=tree, history=history,
            subject="A proposed change (not yet implemented):\n\n" + _render_proposal(proposal),
        )

    async def judge_merge(self, payload: dict, *, proposal: dict | None, goals: str, inventory: str, tree: str, history: str, mode: str) -> Verdict:
        return await self._ask(
            stage="gate 2 — is this implemented change correct and safe to merge?",
            mode=mode, goals=goals, inventory=inventory, tree=tree, history=history,
            subject="A merge request from the evolution agent:\n\n" + _render_merge(payload, proposal),
        )


def status() -> dict:
    return {"available": available(), "model": _RESOLVED_MODEL or JUDGE_MODEL, "configured_model": JUDGE_MODEL}
