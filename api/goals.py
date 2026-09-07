"""R19 — the goal ledger (sequential goal setting).

Slide item 1: the onboarding brief answers what the problem is, what the
literature says, what methods to hold in the arsenal, what the researcher's
intuition is, and what the deliverables and their verification are. This module
turns a brief into an ORDERED list of subgoals with acceptance criteria plus a
capability wishlist (what the harness lacks), lets the researcher reorder them
and mark the current one, and lets the planner record which subgoal the
sessions are actually pursuing — asking the human when the two disagree.

Pure functions over the ledger dict + one model call (``derive``). Storage is
``api/evo_store.py``. Platform code; the prompt is ``api/prompts/goals.md``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

from scaffold import config_loader

from . import evo_store, llm

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "goals.md"
MAX_SUBGOALS = 12
MAX_WISHLIST = 12
# A drift question is asked when the inferred subgoal is this many steps away
# from the one the researcher declared current (or when the model says the plan
# changed outright).
DRIFT_STEPS = 2
# Below this confidence a mere guess must not interrupt the researcher.
DRIFT_MIN_CONFIDENCE = float(os.environ.get("COSCIENTIST_DRIFT_MIN_CONFIDENCE", "0.5"))

SUBGOAL_STATUSES = ("pending", "current", "done", "skipped")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def harness_inventory(root: Path) -> dict:
    """Roles, tools and prompts present in a tenant root (or the platform root)."""
    roles: list[str] = []
    if (root / "roles" / "supervisor.yaml").exists():
        roles.append("supervisor")
    sub = root / "roles" / "subagents"
    if sub.exists():
        roles += [p.stem for p in sorted(sub.glob("*.yaml"))]
    tdir = root / "tools"
    tools = [p.name for p in sorted(tdir.iterdir()) if p.is_dir() and not p.name.startswith("__")] if tdir.exists() else []
    pdir = root / "prompts"
    prompts = [p.name for p in sorted(pdir.glob("*.md"))] if pdir.exists() else []
    sdir = root / ".claude" / "skills"
    skills = [p.name for p in sorted(sdir.iterdir()) if p.is_dir()] if sdir.exists() else []
    return {"roles": roles, "tools": tools, "prompts": prompts, "skills": skills}


def render_inventory(inv: dict) -> str:
    return "\n".join(
        [
            "Roles: " + (", ".join(inv.get("roles") or []) or "(none)"),
            "Tools: " + (", ".join(inv.get("tools") or []) or "(none)"),
            "Prompts: " + (", ".join(inv.get("prompts") or []) or "(none)"),
            "Skills: " + (", ".join(inv.get("skills") or []) or "(none)"),
        ]
    )


def empty_ledger(bid: str, brief: dict | None = None) -> dict:
    brief = brief or {}
    return {
        "schema_version": evo_store.SCHEMA_VERSION,
        "brief_id": bid,
        "title": brief.get("title") or "",
        "research_question": brief.get("research_question") or "",
        "problem_node": None,
        "approach_hints": "",
        "subgoals": [],
        "capability_wishlist": [],
        "current": None,
        "inferred_current": None,
        "phase": None,
        "drift": None,
        "drift_history": [],
        "researcher_ordered": False,
        "derived": None,
        "created": evo_store.now_str(),
        "updated": evo_store.now_str(),
    }


def _next_gid(ledger: dict) -> str:
    used = {s.get("id") for s in ledger.get("subgoals", [])}
    n = 1
    while f"g{n}" in used:
        n += 1
    return f"g{n}"


# Words too generic to identify a capability. A shared "tool"/"pipeline" must
# never make a wishlist item look satisfied.
_GENERIC_TOKENS = frozenset({
    "tool", "tools", "the", "for", "and", "with", "new", "add", "support",
    "integration", "runner", "service", "server", "api", "pipeline", "module",
    "helper", "system", "data", "file", "files", "based", "using", "into",
})


def _distinctive(text: str) -> set[str]:
    """Identifying tokens of a capability name (generic words and 1-2 char
    fragments dropped)."""
    return {t for t in _norm(text).split() if len(t) > 2 and t not in _GENERIC_TOKENS}


def _wishlist_status(name: str, inv: dict, proposals: list[dict] | None = None) -> str:
    """Is this wanted capability already present, already proposed, or missing?

    Matching is by DISTINCTIVE-TOKEN SUBSET, never by substring or a single
    shared token: "ocr" is a substring of "micr-ocr-edit", and one shared word
    ("structure") made "3D structure viewer" look satisfied by "Add a structure
    predictor tool". A false "present"/"proposed" silently suppresses the
    proactive proposal the wishlist exists to trigger, so this errs toward
    "missing".
    """
    want = _distinctive(name)
    if not want:
        return "missing"
    for tool in inv.get("tools") or []:
        have = _distinctive(tool)
        if have and (have <= want or want <= have):
            return f"present:{tool}"
    for skill in inv.get("skills") or []:
        have = _distinctive(skill.replace("-", " "))
        if have and (have <= want or want <= have):
            return f"present:skill:{skill}"
    for p in proposals or []:
        have = _distinctive(p.get("title", ""))
        if not have or not want <= have:
            continue
        if p.get("status") == "merged" and p.get("version_id"):
            return f"version:{p['version_id']}"
        if p.get("status") in ("proposed", "queued", "implementing"):
            return f"proposed:{p['id']}"
    return "missing"


def refresh_wishlist_status(ledger: dict, inv: dict, proposals: list[dict] | None = None) -> dict:
    for item in ledger.get("capability_wishlist", []):
        item["status"] = _wishlist_status(item.get("name", ""), inv, proposals)
    return ledger


async def derive(
    ledger: dict,
    brief_text: str,
    inv: dict,
    *,
    complete: Callable[..., Awaitable[llm.LLMResult]] | None = None,
    api_key: str | None = None,
    keep_subgoals: bool | None = None,
) -> dict:
    """One model call → subgoals (unless the researcher already ordered their own
    and ``keep_subgoals`` is true/None) + capability wishlist. Never raises: on a
    model failure the ledger records the error under ``derived`` and is returned
    unchanged otherwise."""
    complete = complete or llm.complete
    system = config_loader.render(
        PROMPT_PATH.read_text(encoding="utf-8"),
        BRIEF=brief_text.strip() or "(empty brief)",
        APPROACH_HINTS=(ledger.get("approach_hints") or "").strip() or "(none given)",
        HARNESS_INVENTORY=render_inventory(inv),
    )
    res = await complete(system, "Derive the goal ledger now. Output only the JSON object.", max_tokens=3000, api_key=api_key)
    if keep_subgoals is None:
        keep_subgoals = bool(ledger.get("researcher_ordered"))
    if not res.ok:
        ledger["derived"] = {"served_by": res.served_by, "usage": res.usage, "at": evo_store.now_str(), "error": res.error or "empty"}
        return ledger
    data = llm.parse_json_object(res.text)
    if not data:
        # Prose / malformed JSON. Record it as a failure and change NOTHING —
        # previously this silently wiped the capability wishlist and reported
        # derived.error = None, so the UI showed a successful empty derivation.
        ledger["derived"] = {"served_by": res.served_by, "usage": res.usage, "at": evo_store.now_str(), "error": "model reply was not JSON"}
        return ledger
    subgoals_raw = data.get("subgoals") if isinstance(data.get("subgoals"), list) else []
    wishlist_raw = data.get("capability_wishlist") if isinstance(data.get("capability_wishlist"), list) else []

    if not keep_subgoals:
        new_subgoals: list[dict] = []
        for i, s in enumerate(subgoals_raw[:MAX_SUBGOALS]):
            if not isinstance(s, dict) or not str(s.get("text", "")).strip():
                continue
            new_subgoals.append({
                "id": f"g{len(new_subgoals) + 1}",
                "order": len(new_subgoals) + 1,
                "text": str(s.get("text")).strip(),
                "acceptance": [str(a).strip() for a in (s.get("acceptance") or []) if str(a).strip()][:8],
                "capabilities_needed": [str(c).strip() for c in (s.get("capabilities_needed") or []) if str(c).strip()][:8],
                "status": "pending",
            })
        if new_subgoals:
            new_subgoals[0]["status"] = "current"
            ledger["subgoals"] = new_subgoals
            ledger["current"] = new_subgoals[0]["id"]

    wishlist: list[dict] = []
    seen: set[str] = set()
    for w in wishlist_raw[:MAX_WISHLIST * 2]:
        if not isinstance(w, dict):
            continue
        name = str(w.get("name", "")).strip()
        if not name or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        wishlist.append({
            "name": name,
            "why": str(w.get("why", "")).strip(),
            "candidates": [str(c).strip() for c in (w.get("candidates") or []) if str(c).strip()][:5],
            "status": "missing",
        })
        if len(wishlist) >= MAX_WISHLIST:
            break
    if wishlist or not keep_subgoals:
        ledger["capability_wishlist"] = wishlist
    refresh_wishlist_status(ledger, inv)
    ledger["derived"] = {"served_by": res.served_by, "usage": res.usage, "at": evo_store.now_str(), "error": None}
    return ledger


def apply_patch(ledger: dict, patch: dict) -> dict:
    """Researcher edits: ``approach_hints``, ``subgoals`` (full replacement list of
    ``{id?, text, acceptance?, status?}``), ``order`` (list of ids), ``current``
    (id). Any subgoal edit marks the ledger researcher-ordered so a later derive
    keeps their plan."""
    if "approach_hints" in patch and patch["approach_hints"] is not None:
        ledger["approach_hints"] = str(patch["approach_hints"])[:4000]
    if isinstance(patch.get("subgoals"), list):
        prior = {s["id"]: s for s in ledger.get("subgoals", []) if isinstance(s, dict) and s.get("id")}
        new_list: list[dict] = []
        used: set[str] = set()
        explicit_current: str | None = None
        for s in patch["subgoals"][:MAX_SUBGOALS]:
            if not isinstance(s, dict):
                continue
            text = str(s.get("text", "")).strip()
            if not text:
                continue
            gid = s.get("id") if isinstance(s.get("id"), str) and re.fullmatch(r"g\d{1,3}", s.get("id")) else None
            if gid and gid in used:  # a duplicate id would break every by-id lookup
                gid = None
            # An omitted status keeps what the subgoal already had, so a plain
            # text edit cannot silently erase "done" across the whole plan.
            explicit = s.get("status") in SUBGOAL_STATUSES
            if explicit:
                status = s["status"]
            elif gid and gid in prior:
                status = prior[gid].get("status", "pending")
            else:
                status = "pending"
            # Only a status the caller actually SENT moves `current`; an
            # inherited "current" must not outvote an explicit one elsewhere.
            if explicit and status == "current":
                explicit_current = gid or ""
            entry = {
                "id": gid or "",
                "order": len(new_list) + 1,
                "text": text[:600],
                "acceptance": [str(a).strip() for a in (s.get("acceptance") or []) if str(a).strip()][:8],
                "capabilities_needed": [str(c).strip() for c in (s.get("capabilities_needed") or []) if str(c).strip()][:8],
                "status": status,
            }
            if gid:
                used.add(gid)
            new_list.append(entry)
        n = 1
        for s in new_list:
            if not s["id"]:
                while f"g{n}" in used:
                    n += 1
                s["id"] = f"g{n}"
                used.add(s["id"])
                if explicit_current == "":
                    explicit_current = s["id"]
        ledger["subgoals"] = new_list
        ledger["researcher_ordered"] = True
        if explicit_current:
            ledger["current"] = explicit_current
        _invalidate_stale_drift(ledger)
    if isinstance(patch.get("order"), list) and patch["order"]:
        by_id = {s["id"]: s for s in ledger.get("subgoals", [])}
        seen_ids: set[str] = set()
        ordered = [by_id[g] for g in patch["order"] if g in by_id and not (g in seen_ids or seen_ids.add(g))]
        ordered += [s for s in ledger.get("subgoals", []) if s["id"] not in set(patch["order"])]
        for i, s in enumerate(ordered):
            s["order"] = i + 1
        ledger["subgoals"] = ordered
        ledger["researcher_ordered"] = True
    if patch.get("current"):
        set_current(ledger, str(patch["current"]))
        ledger["researcher_ordered"] = True
    # Keep exactly one "current"; if none, the first pending becomes current.
    _normalize_current(ledger)
    return ledger


def _normalize_current(ledger: dict) -> None:
    subs = ledger.get("subgoals", [])
    cur = ledger.get("current")
    ids = [s["id"] for s in subs]
    if cur not in ids:
        cur = next((s["id"] for s in subs if s.get("status") == "current"), None)
        if cur is None:
            cur = next((s["id"] for s in subs if s.get("status") == "pending"), ids[0] if ids else None)
    ledger["current"] = cur
    for s in subs:
        if s["id"] == cur:
            s["status"] = "current"
        elif s.get("status") == "current":
            s["status"] = "pending"


def set_current(ledger: dict, gid: str) -> dict:
    subs = ledger.get("subgoals", [])
    if gid not in {s["id"] for s in subs}:
        raise ValueError(f"unknown subgoal {gid!r}")
    ledger["current"] = gid
    # Everything before the new current is done (sequential plan), after it pending.
    passed = True
    for s in sorted(subs, key=lambda x: x.get("order", 0)):
        if s["id"] == gid:
            s["status"] = "current"
            passed = False
        elif passed:
            if s.get("status") != "skipped":
                s["status"] = "done"
        else:
            if s.get("status") == "current":
                s["status"] = "pending"
    return ledger


def current_subgoal(ledger: dict) -> dict | None:
    cur = ledger.get("current")
    return next((s for s in ledger.get("subgoals", []) if s.get("id") == cur), None)


def _step_distance(ledger: dict, a: str | None, b: str | None) -> int | None:
    order = {s["id"]: int(s.get("order", 0)) for s in ledger.get("subgoals", [])}
    if a not in order or b not in order:
        return None
    return abs(order[a] - order[b])


def _invalidate_stale_drift(ledger: dict) -> None:
    """Drop an open drift question whose subgoals no longer exist (the plan was
    re-derived or edited under it) — answering it would raise from set_current
    after the ledger had already been mutated."""
    q = ledger.get("drift")
    if not q or q.get("answered"):
        return
    ids = {s.get("id") for s in ledger.get("subgoals", [])}
    if q.get("inferred_current") not in ids or q.get("declared_current") not in ids:
        ledger["drift"] = None


def apply_phase_inference(ledger: dict, inferred: dict) -> tuple[dict, bool]:
    """Record what the planner inferred from the sessions. Returns (ledger,
    drift_question_opened). A question opens when the model says the plan
    changed, or the inferred subgoal is >= DRIFT_STEPS away from the declared
    one — unless the same question is already open or was answered 'no' before."""
    ids = {s["id"] for s in ledger.get("subgoals", []) if isinstance(s.get("id"), str)}
    raw_gid = inferred.get("inferred_current")
    gid = raw_gid if isinstance(raw_gid, str) and raw_gid in ids else None
    try:
        confidence = float(inferred.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0  # a malformed field must not abort the whole planner run
    plan_changed = bool(inferred.get("plan_changed"))
    ledger["inferred_current"] = gid
    ledger["phase"] = {
        "inferred_current": gid,
        "confidence": confidence,
        "evidence": str(inferred.get("evidence") or "")[:1000],
        "plan_changed": plan_changed,
        "why": str(inferred.get("why") or "")[:1000],
        "at": evo_store.now_str(),
    }
    declared = ledger.get("current")
    dist = _step_distance(ledger, declared, gid)
    drifted = dist is not None and dist >= DRIFT_STEPS and confidence >= DRIFT_MIN_CONFIDENCE
    # `plan_changed` is the model saying the plan ITSELF moved — the most drastic
    # misalignment, and the case where the work is often outside the ledger
    # entirely (so inferred_current is null or still the declared subgoal). It
    # must open a question on its own, not be dropped for lack of a target.
    if not (drifted or plan_changed):
        return ledger, False
    if plan_changed and not drifted and confidence < DRIFT_MIN_CONFIDENCE:
        return ledger, False
    target = gid if gid and gid != declared else None
    if not target and not plan_changed:
        return ledger, False
    open_q = ledger.get("drift")
    if open_q and not open_q.get("answered") and open_q.get("inferred_current") == target:
        return ledger, False
    for past in ledger.get("drift_history", []):
        if past.get("inferred_current") == target and past.get("declared_current") == declared and past.get("changed") is False:
            return ledger, False  # the human already said "no" to this exact question
    by_id = {s["id"]: s for s in ledger.get("subgoals", [])}
    declared_text = by_id.get(declared, {}).get("text", declared)
    if target:
        question = (
            f"Your plan says the current subgoal is “{declared_text}”, "
            f"but recent sessions look like they are pursuing “{by_id.get(target, {}).get('text', target)}”. Has the plan changed?"
        )
    else:
        question = (
            f"Your plan says the current subgoal is “{declared_text}”, but recent sessions look like "
            "they are pursuing something that is not on the plan at all. Has the plan changed?"
        )
    ledger["drift"] = {
        "id": "d_" + evo_store.uuid.uuid4().hex[:8],
        "inferred_current": target,
        "declared_current": declared,
        "question": question,
        "why": ledger["phase"]["why"] or ledger["phase"]["evidence"],
        "asked_at": evo_store.now_str(),
        "answered": None,
    }
    return ledger, True


def answer_drift(ledger: dict, changed: bool, note: str = "") -> dict:
    q = ledger.get("drift")
    if not q:
        raise ValueError("no open drift question")
    q["answered"] = {"changed": bool(changed), "note": note or "", "at": evo_store.now_str()}
    ledger.setdefault("drift_history", []).append({
        "id": q.get("id"),
        "inferred_current": q.get("inferred_current"),
        "declared_current": q.get("declared_current"),
        "changed": bool(changed),
        "note": note or "",
        "at": evo_store.now_str(),
    })
    if changed and q.get("inferred_current"):
        try:
            set_current(ledger, q["inferred_current"])
            ledger["researcher_ordered"] = True
        except ValueError:
            pass  # the subgoal was edited away while the question was open
    ledger["drift"] = None
    return ledger


def render_for_prompt(ledger: dict, *, max_chars: int = 6000) -> str:
    """Compact text block for the planner / judge prompts."""
    lines: list[str] = []
    lines.append(f"Problem: {ledger.get('title') or '(untitled)'}")
    if ledger.get("research_question"):
        lines.append(f"Question: {ledger['research_question']}")
    if ledger.get("approach_hints"):
        lines.append(f"Researcher's intuition: {ledger['approach_hints']}")
    wl = ledger.get("capability_wishlist", [])
    if wl:
        # Rendered EARLY: "missing" items are what trigger proactive proposals,
        # so they must survive the truncation below on a long ledger.
        lines.append("Capability wishlist:")
        for w in wl:
            cands = ", ".join(w.get("candidates") or [])
            lines.append(f" - {w.get('name')} [{w.get('status', 'missing')}] — {w.get('why', '')}" + (f" (candidates: {cands})" if cands else ""))
    subs = sorted(ledger.get("subgoals", []), key=lambda s: s.get("order", 0))
    if subs:
        lines.append("Sequential subgoals (declared order; * = current):")
        for s in subs:
            mark = "*" if s.get("id") == ledger.get("current") else " "
            acc = "; ".join(s.get("acceptance") or [])
            caps = ", ".join(s.get("capabilities_needed") or [])
            lines.append(f" {mark} {s['id']} [{s.get('status')}] {s.get('text')}" + (f" — accept: {acc}" if acc else "") + (f" — needs: {caps}" if caps else ""))
    else:
        lines.append("Sequential subgoals: (none derived yet)")
    if ledger.get("inferred_current"):
        ph = ledger.get("phase") or {}
        lines.append(f"Inferred from sessions: current = {ledger['inferred_current']} (confidence {ph.get('confidence', 0):.2f}) — {ph.get('evidence', '')}")
    for past in (ledger.get("drift_history") or [])[-3:]:
        verdict = "confirmed the plan changed" if past.get("changed") else "said the plan had NOT changed"
        lines.append(
            f"Earlier you asked whether the plan had moved to {past.get('inferred_current')}; the researcher {verdict}."
            + (f" Their note: {past.get('note')}" if past.get("note") else "")
        )
    if (ledger.get("drift") or {}).get("question") and not (ledger.get("drift") or {}).get("answered"):
        lines.append("A drift question is already open and unanswered; do not raise the same one again.")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def summary(ledger: dict) -> dict:
    cur = current_subgoal(ledger)
    return {
        "brief_id": ledger.get("brief_id"),
        "title": ledger.get("title"),
        "subgoal_count": len(ledger.get("subgoals", [])),
        "current": ledger.get("current"),
        "current_text": cur.get("text") if cur else None,
        "inferred_current": ledger.get("inferred_current"),
        "drift_open": bool(ledger.get("drift") and not (ledger.get("drift") or {}).get("answered")),
        "wishlist_missing": sum(1 for w in ledger.get("capability_wishlist", []) if str(w.get("status", "")).startswith("missing")),
        "updated": ledger.get("updated"),
    }
