"""The background project-tree advisor (O2).

Runs as a short-lived subprocess kicked by ``api.server`` when a problem is
registered or its statement changes (``python -m api.advisor --node <pid>
--user <uid> --trigger <t>``) — the R16 reflection-runner pattern moved into
the platform. It reads the registry (api/projects.py), builds a bounded
snapshot (the node, the owner's inventory, ≤8 candidate neighbours, the
importable catalogue), makes ONE model call through api/llm.py (Fable → Opus),
validates every id the model returned against the registry, and writes:

    state/projects/recommendations/<pid>/<rec_id>.json   the record
    state/projects/recommendations/<pid>/latest.json     copy of the newest
    state/projects/recommendations/<pid>/snapshot-<rec_id>.json  exact model inputs
    state/projects/recommendations/<pid>/advisor.log     subprocess log

plus ``advisor.*`` events and typed edges (source=advisor) on the tree. Never
blocks a request; every failure lands in the record as ``status: failed`` so
the UI can offer Retry. The prompt is api/prompts/advisor.md (human-editable);
what counts as adjacent lives in api/projects.py::recompute_edges.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from scaffold import settings
from scaffold._atomic import append_jsonl, read_json, write_json

from . import llm, projects
from .tenancy import user_root


ADVISOR_MAX_TOKENS = int(os.environ.get("COSCIENTIST_ADVISOR_MAX_TOKENS", "3500"))
DAILY_CAP_USER = int(os.environ.get("COSCIENTIST_ADVISOR_DAILY_CAP_USER", "40"))
DAILY_CAP_GLOBAL = int(os.environ.get("COSCIENTIST_ADVISOR_DAILY_CAP_GLOBAL", "400"))
CANDIDATE_LIMIT = 8
MAX_TOOL_IMPORTS = 5
STALE_LOCK_S = 15 * 60

CompleteFn = Callable[..., Awaitable[llm.LLMResult]]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_rec_id() -> str:
    return "rec-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _prompt_path() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "advisor.md"


# ---------- lock / status ----------


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


def lock_path(pid: str) -> Path:
    return projects.recs_dir(pid) / "running.lock"


def is_running(pid: str) -> bool:
    p = lock_path(pid)
    if not p.exists():
        return False
    try:
        raw = p.read_text(encoding="utf-8").strip().split()
        holder = int(raw[0]) if raw else 0
        started = float(raw[1]) if len(raw) > 1 else 0.0
    except (ValueError, OSError):
        p.unlink(missing_ok=True)
        return False
    if holder > 0 and _pid_alive(holder) and time.time() - started < STALE_LOCK_S:
        return True
    p.unlink(missing_ok=True)  # stale (dead pid or hung too long)
    return False


def _lock_holder(pid: str) -> int:
    try:
        raw = lock_path(pid).read_text(encoding="utf-8").strip().split()
        return int(raw[0]) if raw else 0
    except (ValueError, OSError):
        return 0


def hold_lock_for(pid: str, child_pid: int) -> None:
    """Server-side: mark the node as running on behalf of a just-spawned child
    (which will recognise its own pid in the lock and take it over)."""
    try:
        lock_path(pid).write_text(f"{child_pid} {time.time():.0f}\n", encoding="utf-8")
    except OSError:
        pass


def acquire_lock(pid: str) -> bool:
    """Atomic create (O_EXCL). A lock already held by OUR pid (written by the
    server when it spawned us) counts as acquired; a stale one is replaced."""
    path = lock_path(pid)
    content = f"{os.getpid()} {time.time():.0f}\n"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _lock_holder(pid) == os.getpid():
                return True
            if is_running(pid):
                return False
            continue  # stale lock was removed by is_running(); retry the create
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    return False


def release_lock(pid: str) -> None:
    # Never release a lock another process holds.
    if _lock_holder(pid) in (0, os.getpid()):
        lock_path(pid).unlink(missing_ok=True)


def rerun_flag(pid: str) -> Path:
    return projects.recs_dir(pid) / "rerun.flag"


def request_rerun(pid: str) -> None:
    """Called when a statement changes while a run is in flight: the running
    pass re-reads the node and goes again when it finishes."""
    try:
        rerun_flag(pid).write_text(now_str(), encoding="utf-8")
    except OSError:
        pass


def latest(pid: str) -> dict | None:
    try:
        rec = read_json(projects.recs_dir(pid) / "latest.json", None)
    except Exception:
        return None  # a half-written record must never take the wizard down
    return rec if isinstance(rec, dict) else None


def status(pid: str) -> dict:
    return {"running": is_running(pid), "latest": latest(pid)}


def records_today() -> list[dict]:
    day = time.strftime("%Y-%m-%d")
    out: list[dict] = []
    root = projects.projects_root() / "recommendations"
    if not root.is_dir():
        return out
    for p in root.glob("*/rec-*.json"):
        rec = read_json(p, None)
        if isinstance(rec, dict) and str(rec.get("created", "")).startswith(day) and rec.get("status") in ("ready", "failed"):
            out.append(rec)
    return out


# ---------- snapshot ----------


def _candidate_block(node: dict, nb: dict) -> dict:
    n = nb["node"]
    other = projects.load_node(n["id"]) or {}
    st = other.get("statement") or {}
    links = other.get("links") or {}
    outs = links.get("outcomes") or []
    return {
        "node_id": n["id"],
        "owner": (n.get("owner") or {}).get("user_id"),
        "owner_name": (n.get("owner") or {}).get("display_name"),
        "status": n.get("status"),
        "edge": {"type": nb["edge"].get("type"), "source": nb["edge"].get("source"), "status": nb["edge"].get("status"), "weight": nb["edge"].get("weight"), "rationale": nb["edge"].get("rationale")},
        "statement": {
            "title": st.get("title", ""),
            "domain": st.get("domain", ""),
            "research_question": st.get("research_question", ""),
            "significance": projects._one_line(st.get("significance", ""), 500),
            "open_gap": projects._one_line(st.get("open_gap", ""), 500),
            "evaluation_protocol": projects._one_line(st.get("evaluation_protocol", ""), 500),
            "task_definition": projects._one_line(st.get("task_definition", ""), 300),
            "keywords": list(st.get("keywords") or []),
            "data": [d.get("path") for d in (st.get("data") or [])][:10],
        },
        "outcomes": [{"summary": projects._one_line(o.get("summary", ""), 400), "version": o.get("version")} for o in outs[-3:]],
        "harness_versions": [{"id": v.get("id"), "summary": v.get("summary"), "active": v.get("active"), "imported_from": v.get("imported_from")} for v in (links.get("harness_versions") or [])[:10]],
        "custom_tools": list(links.get("custom_tools") or [])[:20],
    }


def build_snapshot(pid: str, uid: str) -> dict | None:
    node = projects.load_node(pid)
    if not node or not projects.is_owner(node, uid):
        return None
    root = user_root(uid)
    inv = projects.harness_inventory(root)
    nbrs = projects.neighbours(pid, uid, limit=CANDIDATE_LIMIT)
    catalogue = projects.importable_catalogue(uid)
    st = node.get("statement") or {}
    return {
        "node": {"id": pid, "owner": uid, "status": node.get("status"), "visibility": node.get("visibility"), "parent_node": node.get("parent_node"), "statement": st, "completeness": _completeness(st)},
        "owner_inventory": {
            "tools": inv["tools"],
            "custom_tools": inv["custom_tools"],
            "roles": inv["roles"],
            "role_tools": inv["role_tools"],
            "versions": [{"id": v["id"], "summary": v["summary"], "active": v["active"], "imported_from": v.get("imported_from")} for v in inv["versions"]],
            "has_own_versions": any(not v.get("imported_from") for v in inv["versions"]),
        },
        "candidates": [_candidate_block(node, nb) for nb in nbrs],
        "catalogue": {
            "versions": [
                {k: v.get(k) for k in ("owner", "owner_name", "version_id", "sha", "summary", "rationale", "from_node", "from_node_title", "custom_tools", "roles", "imported_from")}
                for v in catalogue["versions"]
            ],
            "tools": [{k: t.get(k) for k in ("name", "owner", "owner_name", "version_id", "description", "from_node")} for t in catalogue["tools"]],
        },
    }


def _completeness(st: dict) -> list[dict]:
    from . import onboarding

    rec = dict(onboarding.empty_brief())
    rec.update({k: v for k, v in st.items() if k in rec})
    return onboarding.completeness(rec)


def render_prompt(snapshot: dict) -> tuple[str, str]:
    system = _prompt_path().read_text(encoding="utf-8")
    user = "\n\n".join(
        [
            "NODE:\n" + json.dumps(snapshot["node"], ensure_ascii=False, indent=1),
            "OWNER_INVENTORY:\n" + json.dumps(snapshot["owner_inventory"], ensure_ascii=False, indent=1),
            "CANDIDATES:\n" + json.dumps(snapshot["candidates"], ensure_ascii=False, indent=1),
            "CATALOGUE:\n" + json.dumps(snapshot["catalogue"], ensure_ascii=False, indent=1),
            "Respond with the JSON object now.",
        ]
    )
    return system, user


# ---------- validation ----------


def _conf(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


def _text(x: Any, n: int = 600) -> str:
    return projects._one_line(str(x or ""), n)


def validate(data: dict, snapshot: dict) -> tuple[dict, list[str]]:
    notes: list[str] = []
    cand_ids = {c["node_id"] for c in snapshot["candidates"]}
    versions = {(v["owner"], v["version_id"]): v for v in snapshot["catalogue"]["versions"]}
    tools = {(t["owner"], t["name"]): t for t in snapshot["catalogue"]["tools"]}
    own_tools = set(snapshot["owner_inventory"]["tools"])
    own_roles = set(snapshot["owner_inventory"]["roles"])

    related: list[dict] = []
    for r in data.get("related_problems") or []:
        if not isinstance(r, dict):
            continue
        nid = str(r.get("node_id") or "")
        if nid not in cand_ids:
            notes.append(f"dropped related_problems entry with unknown node {nid!r}")
            continue
        rel = str(r.get("relation") or "adjacent")
        if rel not in projects.EDGE_TYPES:
            notes.append(f"relation {rel!r} for {nid} coerced to adjacent")
            rel = "adjacent"
        related.append({"node_id": nid, "relation": rel, "confidence": _conf(r.get("confidence")), "rationale": _text(r.get("rationale"))})

    harness = None
    h = data.get("harness_import")
    if isinstance(h, dict) and h.get("recommended"):
        key = (str(h.get("owner") or ""), str(h.get("version_id") or ""))
        v = versions.get(key)
        if not v:
            notes.append(f"dropped harness_import: unknown version {key[1]!r} of {key[0]!r}")
        else:
            alts: list[dict] = []
            for a in h.get("alternatives") or []:
                if isinstance(a, dict) and (str(a.get("owner") or ""), str(a.get("version_id") or "")) in versions:
                    alts.append({"owner": a["owner"], "version_id": a["version_id"], "why": _text(a.get("why"), 300)})
            mode = str(h.get("mode_hint") or "").lower()
            if mode not in ("adopt", "merge"):
                mode = "merge" if snapshot["owner_inventory"].get("has_own_versions") else "adopt"
            harness = {
                "recommended": True,
                "owner": v["owner"],
                "owner_name": v.get("owner_name"),
                "version_id": v["version_id"],
                "sha": v.get("sha"),
                "from_node": v.get("from_node"),
                "from_node_title": v.get("from_node_title"),
                "summary": v.get("summary"),
                "rationale": _text(h.get("rationale")),
                "confidence": _conf(h.get("confidence")),
                "mode_hint": mode,
                "risks": [],  # filled deterministically by the caller
                "alternatives": alts[:3],
                "custom_tools": list(v.get("custom_tools") or []),
            }

    tool_imports: list[dict] = []
    for t in data.get("tool_imports") or []:
        if not isinstance(t, dict):
            continue
        key = (str(t.get("owner") or ""), str(t.get("name") or ""))
        cat = tools.get(key)
        if not cat:
            notes.append(f"dropped tool_import {key[1]!r} from {key[0]!r}: not in the catalogue")
            continue
        if key[1] in own_tools:
            notes.append(f"dropped tool_import {key[1]!r}: already in the researcher's harness")
            continue
        proposed_roles = [r for r in (t.get("role_wiring") or []) if isinstance(r, str)]
        wiring = [r for r in proposed_roles if r in own_roles]
        bad_roles = [r for r in proposed_roles if r not in own_roles]
        if bad_roles:
            notes.append(f"unknown role(s) {bad_roles} dropped from role_wiring of {key[1]!r}")
        if not wiring:
            wiring = [r for r in ("data_analyst", "generalist_researcher") if r in own_roles][:1] or sorted(own_roles - {"supervisor"})[:1]
            notes.append(f"role_wiring for {key[1]!r} replaced with {wiring}")
        tool_imports.append(
            {
                "name": key[1],
                "owner": key[0],
                "owner_name": cat.get("owner_name"),
                "version_id": str(t.get("version_id") or cat.get("version_id")),
                "description": cat.get("description", ""),
                "from_node": cat.get("from_node"),
                "rationale": _text(t.get("rationale")),
                "confidence": _conf(t.get("confidence")),
                "role_wiring": wiring,
            }
        )
        if len(tool_imports) >= MAX_TOOL_IMPORTS:
            break
    # A tool import must reference a version we can fetch; fall back to the catalogue's.
    for ti in tool_imports:
        if (ti["owner"], ti["version_id"]) not in versions:
            ti["version_id"] = tools[(ti["owner"], ti["name"])]["version_id"]

    fb = data.get("statement_feedback") if isinstance(data.get("statement_feedback"), dict) else {}
    missing: list[dict] = []
    for m in fb.get("missing") or []:
        if isinstance(m, dict):
            try:
                q = int(m.get("q"))
            except (TypeError, ValueError):
                continue
            if 0 <= q <= 4:
                missing.append({"q": q, "issue": _text(m.get("issue"), 400)})
        elif isinstance(m, str):
            missing.append({"q": -1, "issue": _text(m, 400)})
    feedback = {
        "missing": missing[:10],
        "notes": [_text(x, 400) for x in (fb.get("notes") or []) if isinstance(x, str)][:8],
        "suggested_keywords": [_text(x, 60) for x in (fb.get("suggested_keywords") or []) if isinstance(x, str)][:12],
    }
    hs = data.get("hypothesis_seed") if isinstance(data.get("hypothesis_seed"), dict) else {}
    seed = {"suggested": bool(hs.get("suggested")), "why": _text(hs.get("why"), 400)}
    out = {
        "summary": _text(data.get("summary"), 900),
        "related_problems": related,
        "harness_import": harness,
        "tool_imports": tool_imports,
        "statement_feedback": feedback,
        "hypothesis_seed": seed,
        "start_from_scratch_rationale": _text(data.get("start_from_scratch_rationale"), 600),
    }
    return out, notes


def harness_risks(uid: str, owner: str, sha: str) -> list[str]:
    """Deterministic risks of adopting a donor version: what the importer's
    current tree has that the donor tree lacks (tools, roles, the compat gate)."""
    from . import imports as _imports

    try:
        return _imports.risks_for(user_root(uid), user_root(owner), sha)
    except Exception as e:  # noqa: BLE001
        return [f"could not compute risks: {e.__class__.__name__}"]


# ---------- the run ----------


def _write_record(pid: str, rec: dict) -> None:
    d = projects.recs_dir(pid)
    write_json(d / f"{rec['rec_id']}.json", rec)
    write_json(d / "latest.json", rec)


def _mirror_to_session(uid: str, node: dict, rec: dict) -> None:
    """A slim `advisor.ready` card in the owner's newest session for this node
    (like R16's reflection nudge), so a researcher already in a session sees it."""
    sessions = (node.get("links") or {}).get("sessions") or []
    if not sessions:
        return
    sid = sessions[-1]
    sdir = user_root(uid) / "state" / "sessions" / sid
    if not sdir.is_dir():
        return
    append_jsonl(
        sdir / "events.jsonl",
        {
            "id": uuid.uuid4().hex[:12],
            "ts": now_str(),
            "session": sid,
            "actor": "advisor",
            "kind": "advisor.ready",
            "node": node["id"],
            "rec_id": rec["rec_id"],
            "summary": rec.get("summary", ""),
            "harness_import": bool((rec.get("harness_import") or {}).get("recommended")),
            "tool_imports": len(rec.get("tool_imports") or []),
        },
    )


async def run(pid: str, uid: str, trigger: str = "manual", *, complete: CompleteFn | None = None, api_key: str | None = None) -> dict:
    """An advisor pass, repeated while the statement changed underneath it
    (rerun.flag written by the server when a register found a run in flight).
    Returns the last record written (status ready | skipped | failed)."""
    rec = await _run_once(pid, uid, trigger, complete=complete, api_key=api_key)
    for _ in range(3):
        flag = rerun_flag(pid)
        if not flag.exists() or rec.get("status") == "skipped" and rec.get("reason") == "already running":
            break
        flag.unlink(missing_ok=True)
        rec = await _run_once(pid, uid, "rerun", complete=complete, api_key=api_key)
    return rec


async def _run_once(pid: str, uid: str, trigger: str = "manual", *, complete: CompleteFn | None = None, api_key: str | None = None) -> dict:
    """One advisor pass. Returns the record written (status ready | skipped | failed)."""
    complete = complete or llm.complete
    node = projects.load_node(pid)
    if not node or not projects.is_owner(node, uid):
        return {"status": "failed", "error": "unknown node or not the owner"}
    if not acquire_lock(pid):
        return {"status": "skipped", "reason": "already running"}
    rec_id = new_rec_id()
    base = {
        "rec_id": rec_id,
        "node_id": pid,
        "user_id": uid,
        "trigger": trigger,
        "statement_sha256": node.get("statement_sha256"),
        "created": now_str(),
        "served_by": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "validation_notes": [],
    }
    try:
        prev = latest(pid)
        # Automatic triggers dedupe on the statement hash; a manual run or a
        # queued re-run (the statement changed under a live pass) always runs.
        if trigger in ("register", "launch") and prev and prev.get("status") == "ready" and prev.get("statement_sha256") == node.get("statement_sha256"):
            projects.event("advisor.skipped", node=pid, reason="unchanged statement", rec_id=prev.get("rec_id"))
            return {**prev, "skipped_reason": "unchanged statement"}
        snapshot = build_snapshot(pid, uid)
        if snapshot is None:
            rec = {**base, "status": "failed", "error": "could not build the snapshot"}
            _write_record(pid, rec)
            return rec
        if not snapshot["candidates"] and not snapshot["catalogue"]["versions"]:
            rec = {**base, "status": "skipped", "reason": "empty tree", "summary": "No other problems or shareable harness versions are on the tree yet — nothing to recommend. Start from the base harness.", "related_problems": [], "harness_import": None, "tool_imports": [], "statement_feedback": {"missing": [{"q": q["q"], "issue": "not answered: " + ", ".join(q["missing"])} for q in snapshot["node"]["completeness"] if not q["filled"]], "notes": [], "suggested_keywords": []}, "hypothesis_seed": {"suggested": False, "why": ""}, "start_from_scratch_rationale": "The project tree is empty apart from this problem."}
            _write_record(pid, rec)
            projects.event("advisor.skipped", node=pid, reason="empty tree", rec_id=rec_id)
            return rec
        today = records_today()
        if len([r for r in today if r.get("user_id") == uid]) >= DAILY_CAP_USER or len(today) >= DAILY_CAP_GLOBAL:
            rec = {**base, "status": "failed", "error": "daily advisor cap reached; try again tomorrow or raise COSCIENTIST_ADVISOR_DAILY_CAP_*"}
            _write_record(pid, rec)
            projects.event("advisor.failed", node=pid, reason="cap", rec_id=rec_id)
            return rec
        write_json(projects.recs_dir(pid) / f"snapshot-{rec_id}.json", snapshot)
        projects.event("advisor.started", node=pid, rec_id=rec_id, trigger=trigger, candidates=len(snapshot["candidates"]), catalogue=len(snapshot["catalogue"]["versions"]))
        system, user = render_prompt(snapshot)
        res = await complete(system, user, max_tokens=ADVISOR_MAX_TOKENS, api_key=api_key)
        base["served_by"] = res.served_by
        base["usage"] = res.usage
        base["attempts"] = res.attempts
        if not res.ok:
            rec = {**base, "status": "failed", "error": res.error or "empty model response"}
            _write_record(pid, rec)
            projects.event("advisor.failed", node=pid, rec_id=rec_id, error=rec["error"])
            return rec
        data = llm.parse_json_object(res.text)
        if not data:
            rec = {**base, "status": "failed", "error": "model returned no JSON object", "raw": res.text[:2000]}
            _write_record(pid, rec)
            projects.event("advisor.failed", node=pid, rec_id=rec_id, error=rec["error"])
            return rec
        body, notes = validate(data, snapshot)
        if body["harness_import"]:
            body["harness_import"]["risks"] = harness_risks(uid, body["harness_import"]["owner"], body["harness_import"]["sha"] or "")
        rec = {**base, "status": "ready", "validation_notes": notes, **body}
        _write_record(pid, rec)
        # Typed, rationalised edges (proposed; either owner confirms/rejects).
        live = projects.current_edges()
        for r in body["related_problems"]:
            s, d = projects._canon(pid, r["node_id"], r["relation"])
            cur = live.get(projects._edge_id(s, d, r["relation"]))
            if cur and cur.get("status") in ("confirmed", "rejected"):
                continue
            e = projects._write_edge(pid, r["node_id"], r["relation"], weight=r["confidence"], source="advisor", status="proposed", rationale=r["rationale"], rec_id=rec_id)
            projects.event("edge.proposed", edge=e["id"], src=e["src"], dst=e["dst"], type=e["type"], source="advisor", rec_id=rec_id)
        projects.event("advisor.ready", node=pid, rec_id=rec_id, served_by=res.served_by, harness=bool(body["harness_import"]), tools=len(body["tool_imports"]), related=len(body["related_problems"]))
        try:
            _mirror_to_session(uid, node, rec)
        except Exception:
            pass
        return rec
    except Exception as e:  # noqa: BLE001 — a failed advisor must never crash or block anything
        rec = {**base, "status": "failed", "error": f"{e.__class__.__name__}: {str(e)[:500]}"}
        try:
            _write_record(pid, rec)
            projects.event("advisor.failed", node=pid, rec_id=rec_id, error=rec["error"])
        except Exception:
            pass
        return rec
    finally:
        release_lock(pid)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--node", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--trigger", default="manual")
    args = p.parse_args()
    rec = asyncio.run(run(args.node, args.user, args.trigger))
    print(json.dumps({"status": rec.get("status"), "rec_id": rec.get("rec_id"), "error": rec.get("error")}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
