"""R19 — durable store for the guided evolution search.

Everything the planner, the judge and the UI need to remember lives under the
tenant's ``state/evolution/`` (Tier-3 durable data; schemas are additive):

    settings.json            {"mode": "manual" | "automatic"}
    goals/<bid>.json         goal ledger for one problem brief (sequential goals)
    proposals/<pid>.json     proposal nodes hanging off version nodes
    decisions.jsonl          the "eval folder": every human / judge decision
    adoption.json            per-version tool-use counts from later sessions
    runs/<run_id>.json       planner runs (exact inputs + parsed output)
    events.jsonl             goal.* / planner.* / proposal.* / judge.* events

Platform code only (``api/``); tenant runtimes never write here, so frozen
per-user forks are not an issue. See docs/plans/R19-guided-evolution-search.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from scaffold._atomic import append_jsonl, read_json, write_json

SCHEMA_VERSION = 1
MODES = ("manual", "automatic")

PROPOSAL_STATUSES = (
    "proposed",      # planner emitted it; awaiting a decision (human or judge)
    "declined",      # human or judge declined at gate 1
    "queued",        # approved (automatic mode) and waiting for a free evolution slot
    "implementing",  # an evolution session is running for it
    "merged",        # became a version node (version_id set)
    "rejected",      # the merge was rejected at gate 2
    "ended",         # the evolution ended without a merge
)

_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


# ---------- paths ----------

def root(state: Path) -> Path:
    return state / "evolution"


def goals_dir(state: Path) -> Path:
    return root(state) / "goals"


def proposals_dir(state: Path) -> Path:
    return root(state) / "proposals"


def runs_dir(state: Path) -> Path:
    return root(state) / "runs"


def events_path(state: Path) -> Path:
    return root(state) / "events.jsonl"


def decisions_path(state: Path) -> Path:
    return root(state) / "decisions.jsonl"


def settings_path(state: Path) -> Path:
    return root(state) / "settings.json"


def adoption_path(state: Path) -> Path:
    return root(state) / "adoption.json"


def platform_cutover_dir(state: Path) -> Path:
    """Cutover requests for platform-scope evolutions (read by the host-side
    ``deploy/platform_cutover.py``). Lives under the PLATFORM state, not a tenant's."""
    return state / "platform" / "cutover"


# ---------- helpers ----------

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def safe_id(raw: str | None) -> str | None:
    if not raw or not _ID_RE.fullmatch(raw):
        return None
    return raw


def event(state: Path, kind: str, **fields: Any) -> str:
    eid = uuid.uuid4().hex[:12]
    append_jsonl(events_path(state), {"id": eid, "ts": now_str(), "kind": kind, **fields})
    return eid


def read_events(state: Path, limit: int = 200) -> list[dict]:
    p = events_path(state)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:]


# ---------- per-tenant settings (mode) ----------

def load_settings(state: Path) -> dict:
    rec = read_json(settings_path(state), default=None)
    if not isinstance(rec, dict):
        rec = {}
    mode = rec.get("mode") if rec.get("mode") in MODES else "manual"
    return {"schema_version": SCHEMA_VERSION, "mode": mode, "updated": rec.get("updated")}


def set_mode(state: Path, mode: str) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    rec = load_settings(state)
    rec["mode"] = mode
    rec["updated"] = now_str()
    write_json(settings_path(state), rec)
    event(state, "mode.changed", mode=mode)
    return rec


# ---------- goal ledgers ----------

def load_goals(state: Path, bid: str) -> dict | None:
    bid = safe_id(bid)
    if not bid:
        return None
    rec = read_json(goals_dir(state) / f"{bid}.json", default=None)
    return rec if isinstance(rec, dict) else None


def save_goals(state: Path, ledger: dict) -> dict:
    bid = safe_id(ledger.get("brief_id"))
    if not bid:
        raise ValueError("ledger has no valid brief_id")
    ledger["schema_version"] = SCHEMA_VERSION
    ledger["updated"] = now_str()
    write_json(goals_dir(state) / f"{bid}.json", ledger)
    return ledger


def list_goals(state: Path) -> list[dict]:
    d = goals_dir(state)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.glob("*.json")):
        rec = read_json(p, default=None)
        if isinstance(rec, dict):
            out.append(rec)
    out.sort(key=lambda r: r.get("updated") or "", reverse=True)
    return out


# ---------- proposals ----------

def proposal_fingerprint(title: str, command: str) -> str:
    norm = re.sub(r"\W+", " ", f"{title} {command}".lower()).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def new_proposal(
    *,
    brief_id: str,
    run_id: str,
    parent_version: str | None,
    title: str,
    command: str,
    scope: str = "harness",
    direction: str = "",
    rationale: str = "",
    goal_ids: list[str] | None = None,
    expected_gain: str = "",
    cost: str = "",
    why_now: str = "",
    provenance: list[str] | None = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": new_id("p"),
        "brief_id": brief_id,
        "run_id": run_id,
        "parent_version": parent_version,
        "title": title,
        "scope": scope if scope in ("harness", "platform") else "harness",
        "direction": direction,
        "goal_ids": list(goal_ids or []),
        "rationale": rationale,
        "command": command,
        "expected_gain": expected_gain,
        "cost": cost,
        "why_now": why_now,
        "provenance": list(provenance or []),
        "fingerprint": proposal_fingerprint(title, command),
        "status": "proposed",
        "judge": None,          # {verdict, score, why, risks[], recommendation, served_by, at}
        "human": None,          # {decision, note, at}
        "session_id": None,     # the evolution session once launched
        "version_id": None,     # the version node once merged
        "created": now_str(),
        "updated": now_str(),
    }


def load_proposal(state: Path, pid: str) -> dict | None:
    pid = safe_id(pid)
    if not pid:
        return None
    rec = read_json(proposals_dir(state) / f"{pid}.json", default=None)
    return rec if isinstance(rec, dict) else None


def save_proposal(state: Path, rec: dict) -> dict:
    pid = safe_id(rec.get("id"))
    if not pid:
        raise ValueError("proposal has no valid id")
    rec["updated"] = now_str()
    write_json(proposals_dir(state) / f"{pid}.json", rec)
    return rec


def set_status(state: Path, rec: dict, status: str, **fields: Any) -> dict:
    if status not in PROPOSAL_STATUSES:
        raise ValueError(f"unknown proposal status {status!r}")
    rec["status"] = status
    rec.update(fields)
    save_proposal(state, rec)
    event(state, "proposal.status", proposal_id=rec["id"], status=status, brief_id=rec.get("brief_id"))
    return rec


def list_proposals(
    state: Path,
    *,
    brief_id: str | None = None,
    statuses: tuple[str, ...] | list[str] | None = None,
    run_id: str | None = None,
) -> list[dict]:
    d = proposals_dir(state)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in d.glob("*.json"):
        rec = read_json(p, default=None)
        if not isinstance(rec, dict):
            continue
        if brief_id and rec.get("brief_id") != brief_id:
            continue
        if statuses and rec.get("status") not in statuses:
            continue
        if run_id and rec.get("run_id") != run_id:
            continue
        out.append(rec)
    out.sort(key=lambda r: (r.get("created") or "", r.get("id") or ""))
    return out


def proposal_for_session(state: Path, sid: str) -> dict | None:
    for rec in list_proposals(state):
        if rec.get("session_id") == sid:
            return rec
    return None


# ---------- decisions: the "eval folder" the next planner run reads ----------

def record_decision(
    state: Path,
    *,
    proposal_id: str,
    actor: str,
    stage: str,
    decision: str,
    note: str = "",
    **extra: Any,
) -> dict:
    """``actor`` is "human" or "judge"; ``stage`` is "proposal" (gate 1) or
    "merge" (gate 2); ``decision`` is pick|both|decline (human, gate 1),
    approve|decline (judge, gate 1), approve|reject (either, gate 2)."""
    rec = {
        "id": uuid.uuid4().hex[:12],
        "ts": now_str(),
        "proposal_id": proposal_id,
        "actor": actor,
        "stage": stage,
        "decision": decision,
        "note": note or "",
        **extra,
    }
    append_jsonl(decisions_path(state), rec)
    return rec


def decisions(state: Path, *, proposal_id: str | None = None, limit: int | None = None) -> list[dict]:
    p = decisions_path(state)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if proposal_id and rec.get("proposal_id") != proposal_id:
            continue
        out.append(rec)
    if limit:
        out = out[-limit:]
    return out


# ---------- adoption: did later sessions actually use what a version added? ----------

def load_adoption(state: Path) -> dict:
    rec = read_json(adoption_path(state), default=None)
    return rec if isinstance(rec, dict) else {"schema_version": SCHEMA_VERSION, "versions": {}}


def record_session_adoption(state: Path, version_id: str, tool_counts: dict[str, int], session_id: str) -> dict:
    rec = load_adoption(state)
    v = rec.setdefault("versions", {}).setdefault(version_id, {"sessions": 0, "tool_uses": {}, "session_ids": []})
    if session_id in v["session_ids"]:
        return rec
    v["sessions"] += 1
    v["session_ids"].append(session_id)
    for tool, n in tool_counts.items():
        v["tool_uses"][tool] = int(v["tool_uses"].get(tool, 0)) + int(n)
    rec["updated"] = now_str()
    write_json(adoption_path(state), rec)
    return rec


# ---------- planner runs ----------

def save_run(state: Path, run: dict) -> dict:
    rid = safe_id(run.get("id"))
    if not rid:
        raise ValueError("run has no valid id")
    write_json(runs_dir(state) / f"{rid}.json", run)
    return run


def load_run(state: Path, run_id: str) -> dict | None:
    rid = safe_id(run_id)
    if not rid:
        return None
    rec = read_json(runs_dir(state) / f"{rid}.json", default=None)
    return rec if isinstance(rec, dict) else None


def latest_run(state: Path, brief_id: str) -> dict | None:
    d = runs_dir(state)
    if not d.exists():
        return None
    best: dict | None = None
    for p in d.glob("run_*.json"):
        rec = read_json(p, default=None)
        if isinstance(rec, dict) and rec.get("brief_id") == brief_id:
            key = (float(rec.get("created_ts") or 0.0), rec.get("created") or "", rec.get("id") or "")
            if best is None or key > (float(best.get("created_ts") or 0.0), best.get("created") or "", best.get("id") or ""):
                best = rec
    return best
