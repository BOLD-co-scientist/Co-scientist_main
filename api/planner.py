"""R19 — the planner: HyperAgents' meta step ("propose the next modification")
for a research harness with no benchmark.

One run = compose the meta agent's instruction (mission + status + eval
folder, as HyperAgents' kickoff preamble does), one model call, validation and
dedupe of the proposals, one judge call per proposal, durable records + events.
The planner never edits code and never launches anything itself: launching is
``api.server``'s job (human pick, or the judge in automatic mode).

Platform code. The prompt is ``api/prompts/planner.md``; a tenant may override
it with ``<root>/prompts/evo_plan.md`` (the evolution agent can evolve *how*
its own planning works — the self-referential part that is safe to expose).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from scaffold import archive, config_loader
from scaffold._atomic import read_json, write_json

from . import evo_store, goals as _goals, judge as _judge, llm
from . import onboarding as _onboarding

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "planner.md"
TENANT_OVERRIDE = Path("prompts") / "evo_plan.md"
MAX_PROPOSALS = 5
LOCK_STALE_S = 900
SESSIONS_MAX = 8
DIRECTIONS = ("agents", "workflow", "tools", "methodology", "memory", "ui", "import")
LEVELS = ("low", "medium", "high")
_PLATFORM_HINT = re.compile(r"\b(ui3/|api/server\.py|api/[a-z_]+\.py|React|\.tsx)\b")


@dataclass
class PlanResult:
    brief_id: str
    status: str                    # ok | busy | error | skipped
    run_id: str | None = None
    proposals: list[dict] = field(default_factory=list)
    phase: dict | None = None
    drift_opened: bool = False
    served_by: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "brief_id": self.brief_id, "status": self.status, "run_id": self.run_id,
            "proposal_count": len(self.proposals), "proposal_ids": [p.get("id") for p in self.proposals],
            "approved": sum(1 for p in self.proposals if (p.get("judge") or {}).get("verdict") == "approve"),
            "phase": self.phase, "drift_opened": self.drift_opened, "served_by": self.served_by, "error": self.error,
        }


# ---------- lock ----------

def _lock_path(state: Path, bid: str) -> Path:
    return evo_store.runs_dir(state) / f"{bid}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def acquire(state: Path, bid: str, token: str) -> bool:
    """Take the per-brief planner lock, stamped with this run's unique token.

    The holder is another asyncio task in THIS process, so a pid check says
    nothing (os.getpid() is always alive) — the token is what makes release
    safe: a run that lost the lock to a stale takeover must not delete the new
    holder's lock on its way out.
    """
    p = _lock_path(state, bid)
    rec = read_json(p, default=None)
    if isinstance(rec, dict):
        held_here = int(rec.get("pid", 0) or 0) == os.getpid()
        fresh = (time.time() - float(rec.get("ts", 0))) < LOCK_STALE_S
        if fresh and (held_here or _pid_alive(int(rec.get("pid", 0) or 0))):
            return False
    write_json(p, {"pid": os.getpid(), "ts": time.time(), "token": token})
    return True


def release(state: Path, bid: str, token: str | None = None) -> None:
    """Release only if we still hold it (token match), so a run whose lock was
    taken over after going stale cannot unlock the run that replaced it."""
    p = _lock_path(state, bid)
    if token is not None:
        rec = read_json(p, default=None)
        if isinstance(rec, dict) and rec.get("token") not in (None, token):
            return
    p.unlink(missing_ok=True)


def is_running(state: Path, bid: str) -> bool:
    rec = read_json(_lock_path(state, bid), default=None)
    if not isinstance(rec, dict):
        return False
    if (time.time() - float(rec.get("ts", 0))) >= LOCK_STALE_S:
        return False
    pid = int(rec.get("pid", 0) or 0)
    return pid == os.getpid() or _pid_alive(pid)


# ---------- inputs ----------

def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _brief_of_session(sd: Path) -> dict | None:
    try:
        rec = read_json(sd / _onboarding.BRIEF_FILENAME, default=None)
    except Exception:  # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) else None


def sessions_for_brief(sessions_dir: Path, bid: str) -> list[dict]:
    """Digest of every research session launched from ``bid``: task, outcome,
    tool-use counts, and the R16 reflection (if any). Newest last."""
    out: list[dict] = []
    if not sessions_dir.exists():
        return out
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir() or sd.name.startswith("evo-"):
            continue
        brief = _brief_of_session(sd)
        if not brief or (brief.get("id") != bid and brief.get("brief_id") != bid):
            continue
        out.append(session_digest(sd))
    return out[-SESSIONS_MAX:]


def session_digest(sd: Path) -> dict:
    events = _read_events(sd / "events.jsonl")
    task = ""
    outcome = ""
    reason = ""
    tool_counts: dict[str, int] = {}
    last_ts = ""
    for e in events:
        if not task and e.get("task"):
            task = str(e["task"])
        k = e.get("kind")
        if k == "tool.use":
            t = str(e.get("tool") or "?")
            tool_counts[t] = tool_counts.get(t, 0) + 1
        elif k == "research.complete":
            reason = str(e.get("reason") or "")
            if e.get("summary"):
                outcome = str(e["summary"])
        elif k in ("report", "bus.send"):
            payload = e.get("payload")
            text = e.get("text") or (payload.get("text") if isinstance(payload, dict) else None)
            if text and (k == "report" or e.get("target") == "human"):
                outcome = str(text)
        last_ts = str(e.get("ts") or last_ts)
    refl = read_json(sd / "reflection.json", default=None)
    reflection = None
    if isinstance(refl, dict) and (refl.get("reflection") or refl.get("proposals")):
        reflection = {
            "text": str(refl.get("reflection", ""))[:600],
            "proposals": [str(p.get("title", "")) for p in (refl.get("proposals") or []) if isinstance(p, dict)][:5],
        }
    return {
        "session_id": sd.name,
        "task": task[:400],
        "reason": reason,
        "outcome": outcome[:700],
        "events": len(events),
        "tool_counts": tool_counts,
        "reflection": reflection,
        "last_ts": last_ts,
    }


def tree_text(root: Path, state: Path) -> str:
    try:
        vl = archive.list_versions(root)
    except Exception as e:  # noqa: BLE001
        return f"(version tree unavailable: {e})"
    adoption = evo_store.load_adoption(state).get("versions", {})
    lines: list[str] = []
    versions = vl.get("versions") or []
    if not versions:
        head = (vl.get("head") or "")[:7]
        boot = adoption.get("bootstrap", {})
        lines.append(f"root {head or '(bootstrap)'} — tenant bootstrap (active). Sessions run on it: {boot.get('sessions', 0)}.")
        return "\n".join(lines)
    for v in versions:
        ad = adoption.get(v.get("id"), {})
        used = ", ".join(f"{k}×{n}" for k, n in sorted((ad.get("tool_uses") or {}).items(), key=lambda kv: -kv[1])[:6])
        lines.append(
            f"- {v.get('id')}{' [ACTIVE]' if v.get('active') else ''} — {v.get('summary') or '(no summary)'} "
            f"(status {v.get('status', '?')}, smoke {'ok' if (v.get('smoke') or {}).get('ok') else ('failed' if (v.get('smoke') or {}).get('ran') else 'n/a')}; "
            f"later sessions: {ad.get('sessions', 0)}{'; tools used: ' + used if used else ''})"
        )
    return "\n".join(lines)


def platform_inventory(platform_root: Path | None) -> str:
    """What the planner may propose changing in the SHARED platform (ui3/ + api/).

    ``None`` means platform evolution is disabled on this deployment; say so in
    the strongest terms, because a platform proposal would be judged, picked and
    only THEN refused with a 501 — burning a judge call and polluting the eval
    folder with a decision the researcher could not act on.
    """
    if not platform_root:
        return (
            "PLATFORM EVOLUTION IS DISABLED on this deployment: the shared UI and API cannot be "
            "changed by an evolution right now. Do NOT emit any proposal with scope \"platform\". "
            "If the researcher needs a visual capability, propose a HARNESS tool that writes a "
            "self-contained artifact (an HTML or PNG file) into results/ instead."
        )
    comps = platform_root / "ui3" / "src" / "components"
    api_dir = platform_root / "api"
    parts: list[str] = []
    if comps.exists():
        parts.append("ui3 React components: " + ", ".join(p.stem for p in sorted(comps.glob("*.tsx"))))
    if api_dir.exists():
        parts.append("api modules: " + ", ".join(p.stem for p in sorted(api_dir.glob("*.py")) if p.stem != "__init__"))
    return "\n".join(parts) or "(none found)"


def status_text(sessions: list[dict], tree: str) -> str:
    lines = ["Version tree:", tree, ""]
    if not sessions:
        lines.append("Sessions for this problem: none yet — the researcher is about to start (propose what the first subgoals will need).")
    else:
        lines.append(f"Recent sessions for this problem ({len(sessions)}):")
        for s in sessions:
            tools = ", ".join(f"{k}×{n}" for k, n in sorted(s["tool_counts"].items(), key=lambda kv: -kv[1])[:6])
            lines.append(f"- {s['session_id']} ({s['events']} events; ended: {s['reason'] or 'running/unknown'})")
            if s["task"]:
                lines.append(f"    task: {s['task'][:220]}")
            if s["outcome"]:
                lines.append(f"    outcome: {s['outcome'][:400]}")
            if tools:
                lines.append(f"    tools used: {tools}")
            if s.get("reflection"):
                lines.append(f"    reflection: {s['reflection']['text'][:300]}" + (f" | suggested: {'; '.join(s['reflection']['proposals'])}" if s["reflection"]["proposals"] else ""))
    return "\n".join(lines)


def eval_folder_text(state: Path, bid: str) -> str:
    decs = evo_store.decisions(state, limit=200)
    props = {p["id"]: p for p in evo_store.list_proposals(state)}
    hist = _judge.history_block(decs, props)
    open_ = [p for p in evo_store.list_proposals(state, brief_id=bid, statuses=("proposed", "queued", "implementing"))]
    lines = ["Decision history (all problems of this researcher):", hist]
    # What each attempt actually PRODUCED — HyperAgents' eval folder is the record
    # of outcomes, not only of choices. Without this an evolution that ended
    # without a merge looks identical to one never tried, and gets re-proposed.
    outcomes = [p for p in evo_store.list_proposals(state, brief_id=bid) if p.get("status") in ("merged", "rejected", "ended", "declined")]
    if outcomes:
        lines.append("")
        lines.append("Outcomes of past attempts on this problem (do not repeat a failed one without saying what changed):")
        for p in outcomes[-20:]:
            bits = [f"- [{p.get('status')}] {p.get('title')} ({p.get('scope')}/{p.get('direction')})"]
            if p.get("version_id"):
                bits.append(f"became version {p['version_id']}")
            if p.get("error"):
                bits.append(f"failed: {p['error']}")
            if (p.get("human") or {}).get("note"):
                bits.append(f"researcher said: {p['human']['note']}")
            lines.append(" — ".join(bits))
    if open_:
        lines.append("")
        lines.append("Open proposals for this problem (do not duplicate):")
        for p in open_:
            j = p.get("judge") or {}
            lines.append(f"- [{p.get('status')}] {p.get('title')} ({p.get('scope')}/{p.get('direction')})" + (f" — judge {j.get('verdict')} {j.get('score', '')}" if j.get("verdict") else ""))
    return "\n".join(lines)


def prompt_template(root: Path | None) -> tuple[str, str]:
    """Tenant override wins (so the harness can evolve its own planning)."""
    if root:
        p = root / TENANT_OVERRIDE
        if p.exists():
            return p.read_text(encoding="utf-8"), str(p)
    return PROMPT_PATH.read_text(encoding="utf-8"), str(PROMPT_PATH)


# ---------- validation ----------

def _level(v: Any) -> str:
    v = str(v or "").strip().lower()
    return v if v in LEVELS else "medium"


def normalize_proposal(raw: dict, ledger: dict) -> dict | None:
    title = str(raw.get("title", "")).strip()
    command = str(raw.get("command", "")).strip()
    if len(title) < 4 or len(command) < 20:
        return None
    scope = str(raw.get("scope", "")).strip().lower()
    # A command that names ui3/ or api/ IS a platform change, whatever the model
    # labelled it: launching it as a harness evolution would run the agent in a
    # tenant worktree where those paths do not exist and the guard blocks them.
    if _PLATFORM_HINT.search(command):
        scope = "platform"
    elif scope not in ("harness", "platform"):
        scope = "harness"
    direction = str(raw.get("direction", "")).strip().lower()
    if direction not in DIRECTIONS:
        direction = "ui" if scope == "platform" else "tools"
    ids = {s["id"] for s in ledger.get("subgoals", [])}
    goal_ids = [g for g in (raw.get("goal_ids") or []) if isinstance(g, str) and g in ids]
    return {
        "title": title[:200],
        "scope": scope,
        "direction": direction,
        "goal_ids": goal_ids,
        "rationale": str(raw.get("rationale", "")).strip()[:1500],
        "command": command[:6000],
        "expected_gain": _level(raw.get("expected_gain")),
        "cost": _level(raw.get("cost")),
        "why_now": str(raw.get("why_now", "")).strip()[:500],
    }


# ---------- the run ----------

async def run(
    ctx: Any,
    bid: str,
    *,
    trigger: str = "manual",
    mode: str | None = None,
    complete: Callable[..., Awaitable[llm.LLMResult]] | None = None,
    judge: _judge.Judge | None = None,
    api_key: str | None = None,
    platform_root: Path | None = None,
) -> PlanResult:
    """``ctx`` is a UserContext-like object (``root``, ``state``, ``sessions``,
    ``library``). Never raises; failures come back as ``PlanResult(status="error")``."""
    state: Path = ctx.state
    bid = evo_store.safe_id(bid) or ""
    if not bid:
        return PlanResult(brief_id=bid, status="error", error="invalid brief id")
    run_id = evo_store.new_id("run")
    if not acquire(state, bid, run_id):
        # A background trigger that loses the race used to vanish without a
        # trace; record it so the human can see why no new plan appeared.
        evo_store.event(state, "planner.skipped", brief_id=bid, trigger=trigger, reason="a planner run is already in progress for this brief")
        return PlanResult(brief_id=bid, status="busy", error="a planner run is already in progress for this brief")
    complete = complete or llm.complete
    judge = judge or _judge.Judge()
    mode = mode or evo_store.load_settings(state)["mode"]
    evo_store.event(state, "planner.started", run_id=run_id, brief_id=bid, trigger=trigger)
    try:
        return await _run_locked(ctx, bid, run_id, trigger=trigger, mode=mode, complete=complete, judge=judge, api_key=api_key, platform_root=platform_root)
    except Exception as e:  # noqa: BLE001 — never let a planner failure escape into the API
        evo_store.event(state, "planner.failed", run_id=run_id, brief_id=bid, error=f"{e.__class__.__name__}: {str(e)[:300]}")
        evo_store.save_run(state, {"id": run_id, "brief_id": bid, "trigger": trigger, "created": evo_store.now_str(), "created_ts": time.time(), "error": f"{e.__class__.__name__}: {e}"})
        return PlanResult(brief_id=bid, status="error", run_id=run_id, error=f"{e.__class__.__name__}: {str(e)[:300]}")
    finally:
        release(state, bid, run_id)


async def _run_locked(ctx: Any, bid: str, run_id: str, *, trigger, mode, complete, judge, api_key, platform_root) -> PlanResult:
    state: Path = ctx.state
    root: Path = ctx.root
    brief = _onboarding.load_brief(state, bid)
    inv = _goals.harness_inventory(root)
    ledger = evo_store.load_goals(state, bid)
    if ledger is None:
        ledger = _goals.empty_ledger(bid, brief)
    if not ledger.get("subgoals") and brief:
        # First contact with this problem: derive the sequential plan first.
        ledger = await _goals.derive(ledger, _onboarding.render_brief(brief, ctx.library), inv, complete=complete, api_key=api_key)
        evo_store.save_goals(state, ledger)
        evo_store.event(state, "goal.derived", brief_id=bid, subgoals=len(ledger.get("subgoals", [])), served_by=(ledger.get("derived") or {}).get("served_by"))
    existing = evo_store.list_proposals(state, brief_id=bid)
    _goals.refresh_wishlist_status(ledger, inv, existing)

    sessions = sessions_for_brief(ctx.sessions, bid)
    tree = tree_text(root, state)
    template, template_path = prompt_template(root)
    prompt = config_loader.render(
        template,
        TRIGGER=trigger,
        MODE=mode,
        MISSION=_goals.render_for_prompt(ledger),
        STATUS=status_text(sessions, tree),
        EVAL_FOLDER=eval_folder_text(state, bid),
        HARNESS_INVENTORY=_goals.render_inventory(inv),
        PLATFORM_INVENTORY=platform_inventory(platform_root),
    )
    res = await complete(prompt, "Plan now. Output only the JSON object.", max_tokens=4000, api_key=api_key)
    run_rec: dict = {
        "id": run_id, "brief_id": bid, "trigger": trigger, "mode": mode, "created": evo_store.now_str(), "created_ts": time.time(),
        "prompt_path": template_path, "prompt": prompt[:60_000], "served_by": res.served_by, "usage": res.usage,
        "raw_output": res.text[:20_000], "error": res.error, "session_ids": [s["session_id"] for s in sessions],
    }
    if not res.ok:
        run_rec["error"] = res.error or "empty model output"
        evo_store.save_run(state, run_rec)
        evo_store.event(state, "planner.failed", run_id=run_id, brief_id=bid, error=run_rec["error"])
        return PlanResult(brief_id=bid, status="error", run_id=run_id, served_by=res.served_by, error=run_rec["error"])

    data = llm.parse_json_object(res.text)
    if not data:
        # Prose or malformed JSON. Distinguish it from a legitimate "nothing to
        # propose" (which is a valid, meaningful planner answer).
        run_rec["error"] = "planner reply was not JSON"
        evo_store.save_run(state, run_rec)
        evo_store.event(state, "planner.failed", run_id=run_id, brief_id=bid, error=run_rec["error"])
        return PlanResult(brief_id=bid, status="error", run_id=run_id, served_by=res.served_by, error=run_rec["error"])
    phase = data.get("phase") if isinstance(data.get("phase"), dict) else {}
    drift_opened = False
    if phase:
        ledger, drift_opened = _goals.apply_phase_inference(ledger, phase)
        evo_store.event(state, "goal.phase", brief_id=bid, inferred_current=ledger.get("inferred_current"), confidence=(ledger.get("phase") or {}).get("confidence"))
        if drift_opened:
            evo_store.event(state, "goal.drift", brief_id=bid, drift_id=(ledger.get("drift") or {}).get("id"), question=(ledger.get("drift") or {}).get("question"))

    # Validate + dedupe against everything already recorded for this brief.
    active_fps = {p.get("fingerprint") for p in existing if p.get("status") in ("proposed", "queued", "implementing", "merged")}
    declined_fps = {p.get("fingerprint") for p in existing if p.get("status") in ("declined", "rejected")}
    # Titles of DECLINED proposals count too: an exact-fingerprint check alone
    # let a re-worded command re-propose what the human already turned down.
    seen_titles = {
        _norm_title(p.get("title", ""))
        for p in existing
        if p.get("status") in ("proposed", "queued", "implementing", "declined", "rejected", "merged")
    }
    parent_version = None
    try:
        parent_version = archive.list_versions(root).get("active")
    except Exception:  # noqa: BLE001
        pass
    new: list[dict] = []
    raw_list = data.get("proposals") if isinstance(data.get("proposals"), list) else []
    for raw in raw_list:
        if not isinstance(raw, dict) or len(new) >= MAX_PROPOSALS:
            continue
        norm = normalize_proposal(raw, ledger)
        if not norm:
            continue
        fp = evo_store.proposal_fingerprint(norm["title"], norm["command"])
        if fp in active_fps or fp in declined_fps or _norm_title(norm["title"]) in seen_titles:
            continue
        rec = evo_store.new_proposal(brief_id=bid, run_id=run_id, parent_version=parent_version, provenance=[f"run:{run_id}", f"trigger:{trigger}"], **norm)
        new.append(rec)
        active_fps.add(fp)
        seen_titles.add(_norm_title(norm["title"]))

    # Gate 1 judge pass — one call per proposal; recorded into the eval folder.
    goals_text = _goals.render_for_prompt(ledger)
    inv_text = _goals.render_inventory(inv)
    history = _judge.history_block(evo_store.decisions(state, limit=200), {p["id"]: p for p in evo_store.list_proposals(state)})
    for rec in new:
        verdict = await judge.judge_proposal(rec, goals=goals_text, inventory=inv_text, tree=tree, history=history, mode=mode)
        rec["judge"] = verdict.to_record("proposal")
        evo_store.save_proposal(state, rec)
        evo_store.event(state, "proposal.created", proposal_id=rec["id"], brief_id=bid, run_id=run_id, title=rec["title"], scope=rec["scope"], judge=verdict.verdict)
        if verdict.ok:
            evo_store.record_decision(state, proposal_id=rec["id"], actor="judge", stage="proposal", decision=verdict.verdict, note=verdict.why, score=verdict.score, served_by=verdict.served_by)
            evo_store.event(state, "judge.verdict", proposal_id=rec["id"], stage="proposal", verdict=verdict.verdict, score=verdict.score, why=verdict.why, recommendation=verdict.recommendation)

    # The run held only an in-memory copy of the ledger across two slow model
    # calls. Re-read it and merge ONLY the planner-owned fields, so a researcher
    # edit that landed meanwhile (reorder, current, hints, drift answer) survives.
    fresh = evo_store.load_goals(state, bid)
    if isinstance(fresh, dict) and fresh.get("subgoals") is not None:
        fresh["inferred_current"] = ledger.get("inferred_current")
        fresh["phase"] = ledger.get("phase")
        if drift_opened and ledger.get("drift"):
            q = ledger["drift"]
            live_ids = {sg.get("id") for sg in fresh.get("subgoals", [])}
            if q.get("declared_current") == fresh.get("current") and (q.get("inferred_current") in live_ids or q.get("inferred_current") is None):
                fresh["drift"] = q
            else:
                drift_opened = False  # the plan moved under the question; drop it
        ledger = fresh
    _goals.refresh_wishlist_status(ledger, inv, existing + new)
    evo_store.save_goals(state, ledger)
    run_rec.update({"proposal_ids": [p["id"] for p in new], "phase": ledger.get("phase"), "drift_opened": drift_opened})
    evo_store.save_run(state, run_rec)
    evo_store.event(
        state, "planner.ready", run_id=run_id, brief_id=bid, trigger=trigger, proposal_count=len(new),
        approved=sum(1 for p in new if (p.get("judge") or {}).get("verdict") == "approve"), served_by=res.served_by,
    )
    return PlanResult(brief_id=bid, status="ok", run_id=run_id, proposals=new, phase=ledger.get("phase"), drift_opened=drift_opened, served_by=res.served_by)


def _norm_title(t: str) -> str:
    return re.sub(r"\W+", " ", (t or "").lower()).strip()
