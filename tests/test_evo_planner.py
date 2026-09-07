"""R19 guided evolution search — offline suite.

Drives the real FastAPI routes through TestClient with the model calls (planner
+ goal derivation on Claude, judge on OpenAI) and the evolution subprocess
faked, so the whole loop — goals → plan → judge → decide → launch → merge gate
→ outcome → re-plan — runs in seconds without spend.
See docs/plans/R19-guided-evolution-search.md.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def _seed_base(root: Path) -> None:
    for name in ("scaffold", "research", "evolution", "tools", "roles", "prompts", "tests"):
        target = root / name
        if not target.exists():
            shutil.copytree(ROOT / name, target)
    for name in ("pyproject.toml", "Dockerfile", "README.md", "CLAUDE.md", "ROADMAP.md", ".gitignore"):
        src = ROOT / name
        if src.exists():
            (root / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


GOALS_JSON = {
    "subgoals": [
        {"text": "Clean and characterise the growth-curve CSVs", "acceptance": ["all strains parsed; QC table in results/"], "capabilities_needed": ["py_exec"]},
        {"text": "Predict 3D structure of the candidate protein", "acceptance": ["a PDB file per candidate in results/"], "capabilities_needed": ["structure predictor", "3D structure viewer"]},
        {"text": "Write the report with figures", "acceptance": ["results/report.pdf compiles"], "capabilities_needed": ["latex_compile"]},
    ],
    "capability_wishlist": [
        {"name": "3D structure viewer", "why": "deliverable D2 is a structure; nothing renders it", "candidates": ["py3Dmol", "NGL viewer"]},
        {"name": "latex_compile", "why": "the report must compile", "candidates": []},
    ],
}


def _plan_json(titles, *, phase=None):
    props = []
    for t in titles:
        scope = "platform" if "viewer" in t.lower() and "ui" in t.lower() else "harness"
        props.append({
            "title": t,
            "scope": scope,
            "direction": "ui" if scope == "platform" else "tools",
            "goal_ids": ["g2"],
            "rationale": f"needed because {t.lower()} serves subgoal g2",
            "command": f"Implement '{t}': add tools/{t.lower().replace(' ', '_')[:20]}/server.py with a health check and register it in roles/subagents/data_analyst.yaml; add a test.",
            "expected_gain": "high",
            "cost": "medium",
            "why_now": "g2 is current",
        })
    return {"phase": phase or {"inferred_current": "g1", "confidence": 0.6, "evidence": "sessions parse CSVs", "plan_changed": False, "why": ""}, "proposals": props}


class FakeModel:
    """Stands in for api.llm.complete: goals prompt → GOALS_JSON, planner prompt → next_plan."""

    def __init__(self):
        self.calls: list[str] = []
        self.next_plan = _plan_json(["Add a structure predictor tool", "Add a 3D structure viewer UI panel", "Weaken the merge gate to skip smoke"])
        self.fail = False

    async def __call__(self, system, user, *, max_tokens=4000, api_key=None, **kw):
        from api import llm
        if self.fail:
            return llm.LLMResult(text="", served_by="fake", error="boom")
        if "Goal ledger derivation" in system:
            self.calls.append("goals")
            return llm.LLMResult(text=json.dumps(GOALS_JSON), served_by="fake-claude")
        self.calls.append("plan")
        return llm.LLMResult(text=json.dumps(self.next_plan), served_by="fake-claude")


class FakeJudgeCall:
    """Stands in for the OpenAI call: declines anything that weakens a gate or
    duplicates an existing tool, approves the rest; records what it saw."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, preference, system, user):
        self.calls.append({"system": system, "user": user})
        subject = system.split("## What you are judging", 1)[-1].split("## Hard rules", 1)[0].lower()
        bad = "weaken" in subject or "skip smoke" in subject
        dup = "latex_compile" in subject and "duplicate" in subject
        verdict = "decline" if (bad or dup) else "approve"
        score = 0.15 if verdict == "decline" else (0.9 if "structure predictor" in subject else 0.7)
        return json.dumps({
            "verdict": verdict, "score": score,
            "why": ("Weakens the merge gate." if bad else "Serves subgoal g2; low blast radius."),
            "risks": ["none"] if verdict == "approve" else ["safety"],
            "recommendation": ("Decline — security" if bad else "Approve — serves g2"),
        }), "fake-gpt", {"input_tokens": 10, "output_tokens": 5}


@pytest.fixture
def api(tmp_path, monkeypatch):
    _seed_base(tmp_path)
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    monkeypatch.setenv("COSCIENTIST_EVO_PLAN_DELAY_S", "0")
    monkeypatch.setenv("COSCIENTIST_JUDGE_MERGE_POLL_S", "0.05")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("COSCIENTIST_JUDGE_OPENAI_API_KEY", raising=False)
    from scaffold import settings
    importlib.reload(settings)
    import api.auth as auth
    import api.tenancy as tenancy
    import api.onboarding as onboarding
    import api.evo_store as evo_store
    import api.goals as goals
    import api.judge as judge
    import api.planner as planner
    import api.server as server
    for m in (auth, tenancy, onboarding, evo_store, goals, judge, planner, server):
        importlib.reload(m)

    model = FakeModel()
    jcall = FakeJudgeCall()
    monkeypatch.setattr(server, "_LLM_COMPLETE", model)
    monkeypatch.setattr(server, "_judge_for", lambda ctx: judge.Judge(call=jcall))

    spawned: list[dict] = []
    research_spawned: list[dict] = []

    async def fake_spawn_evo(ctx, sid, command, base, *, scope="harness"):
        spawned.append({"sid": sid, "command": command, "base": base, "scope": scope})
        server._RUNNING[server._monitor_key(ctx, sid)] = object()

    async def fake_spawn_research(ctx, sid, message, resume_uuid=None):
        research_spawned.append({"sid": sid, "message": message})

    monkeypatch.setattr(server, "_spawn_evolution", fake_spawn_evo)
    monkeypatch.setattr(server, "_spawn_research", fake_spawn_research)

    user, key = auth.create_user("alice")
    ctx = tenancy.context_for(user)
    headers = {"Authorization": f"Bearer {key}"}
    with TestClient(server.app) as client:
        yield {
            "client": client, "headers": headers, "ctx": ctx, "server": server, "model": model, "judge": jcall,
            "spawned": spawned, "research_spawned": research_spawned, "evo_store": evo_store, "goals": goals,
            "judge_mod": judge, "planner": planner, "settings": settings,
        }
    server._RUNNING.clear()


BRIEF = {
    "title": "Predict the structure of the LTEM stress protein",
    "domain": "structural biology",
    "background": "Growth curves suggest a stress protein; its structure is unknown.",
    "research_question": "What is the 3D structure of the LTEM stress protein and which pocket binds ATP?",
    "objectives": ["Parse growth curves", "Predict structure", "Report"],
    "success_criteria": "A PDB model with pLDDT > 70 and a figure of the pocket.",
    "deliverables": ["Report (PDF)", "PDB model"],
}


def _make_brief(api):
    r = api["client"].post("/onboarding/briefs", json=BRIEF, headers=api["headers"])
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _wait(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.03)
    raise AssertionError("timed out waiting")


def _proposals(api, bid):
    r = api["client"].get(f"/evolution/proposals?brief_id={bid}", headers=api["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _plan_and_wait(api, bid, expect_min=1):
    r = api["client"].post("/evolution/plan", json={"brief_id": bid, "trigger": "manual"}, headers=api["headers"])
    assert r.status_code == 200, r.text
    runs_before = api["evo_store"].latest_run(api["ctx"].state, bid)
    return _wait(lambda: (lambda lr: lr if lr and (runs_before is None or lr["id"] != runs_before["id"]) else None)(api["evo_store"].latest_run(api["ctx"].state, bid)))


def _finish_evo(api, sid, *, merged=True, version_id="20260906-000000__v"):
    server, ctx = api["server"], api["ctx"]
    if merged:
        server._append_event(ctx, sid, actor="evolution", kind="version.recorded", version=version_id, tag=f"ver/{version_id}")
        server._append_event(ctx, sid, actor="evolution", kind="evolution.merged", ref="x")
    else:
        server._append_event(ctx, sid, actor="evolution", kind="evolution.rejected", ref="x")
    server._append_event(ctx, sid, actor="evolution", kind="evolution.end")
    server._RUNNING.pop(server._monitor_key(ctx, sid), None)
    # Drive the monitor's tail exactly as the real subprocess exit would.
    api["client"].portal.call(server._after_evolution, ctx, sid)


# ---------- store + pure logic ----------


def test_store_roundtrip(api):
    es, state = api["evo_store"], api["ctx"].state
    assert es.load_settings(state)["mode"] == "manual"
    es.set_mode(state, "automatic")
    assert es.load_settings(state)["mode"] == "automatic"
    with pytest.raises(ValueError):
        es.set_mode(state, "yolo")
    p = es.new_proposal(brief_id="b1", run_id="run_1", parent_version=None, title="T", command="do the thing in tools/x")
    es.save_proposal(state, p)
    assert es.load_proposal(state, p["id"])["title"] == "T"
    es.set_status(state, p, "implementing", session_id="evo-1")
    assert es.proposal_for_session(state, "evo-1")["id"] == p["id"]
    es.record_decision(state, proposal_id=p["id"], actor="human", stage="proposal", decision="pick")
    assert es.decisions(state)[-1]["decision"] == "pick"
    es.record_session_adoption(state, "ver1", {"py_exec": 3}, "s1")
    es.record_session_adoption(state, "ver1", {"py_exec": 2, "ocr": 1}, "s2")
    es.record_session_adoption(state, "ver1", {"py_exec": 9}, "s2")  # idempotent per session
    ad = es.load_adoption(state)["versions"]["ver1"]
    assert ad["sessions"] == 2 and ad["tool_uses"] == {"py_exec": 5, "ocr": 1}
    assert any(e["kind"] == "mode.changed" for e in es.read_events(state))


def test_goals_patch_current_and_drift(api):
    g = api["goals"]
    ledger = g.empty_ledger("b1", {"title": "T", "research_question": "Q"})
    ledger = g.apply_patch(ledger, {"subgoals": [{"text": "one"}, {"text": "two"}, {"text": "three"}, {"text": "four"}]})
    assert [s["id"] for s in ledger["subgoals"]] == ["g1", "g2", "g3", "g4"]
    assert ledger["current"] == "g1" and ledger["subgoals"][0]["status"] == "current"
    ledger = g.apply_patch(ledger, {"order": ["g2", "g1"]})
    assert [s["id"] for s in ledger["subgoals"]] == ["g2", "g1", "g3", "g4"]
    ledger = g.apply_patch(ledger, {"current": "g3"})
    assert [s["status"] for s in ledger["subgoals"]] == ["done", "done", "current", "pending"]
    # One step away → no question; two steps → a drift question.
    ledger, opened = g.apply_phase_inference(ledger, {"inferred_current": "g4", "confidence": 0.8, "evidence": "e"})
    assert not opened and ledger["inferred_current"] == "g4"
    ledger, opened = g.apply_phase_inference(ledger, {"inferred_current": "g2", "confidence": 0.9, "evidence": "sessions redo two", "plan_changed": False})
    assert opened and ledger["drift"]["inferred_current"] == "g2" and "Has the plan changed?" in ledger["drift"]["question"]
    # Same open question is not re-asked.
    ledger, opened = g.apply_phase_inference(ledger, {"inferred_current": "g2", "confidence": 0.9, "evidence": "e"})
    assert not opened
    ledger = g.answer_drift(ledger, False, "no, we are just re-checking")
    assert ledger["drift"] is None and ledger["current"] == "g3"
    # A 'no' suppresses the identical question next time; 'plan_changed' forces it anyway for a new target.
    ledger, opened = g.apply_phase_inference(ledger, {"inferred_current": "g2", "confidence": 0.9, "evidence": "e"})
    assert not opened
    ledger, opened = g.apply_phase_inference(ledger, {"inferred_current": "g4", "confidence": 0.9, "evidence": "e", "plan_changed": True, "why": "the target moved"})
    assert opened
    ledger = g.answer_drift(ledger, True, "yes")
    assert ledger["current"] == "g4" and ledger["subgoals"][2]["status"] == "done"
    assert "Sequential subgoals" in g.render_for_prompt(ledger)


def test_judge_verdict_parsing_and_calibration(api):
    jm = api["judge_mod"]
    j = jm.Judge(call=api["judge"])
    v = api["client"].portal.call(lambda: j.judge_proposal({"title": "Add a structure predictor tool", "command": "x" * 30, "scope": "harness"}, goals="g", inventory="i", tree="t", history="h", mode="manual"))
    assert v.ok and v.approved and 0 < v.score <= 1 and v.served_by == "fake-gpt" and v.recommendation
    rec = v.to_record("proposal")
    assert rec["verdict"] == "approve" and rec["stage"] == "proposal"
    # unavailable judge degrades, never raises
    off = jm.Judge()
    assert not off.available()
    v2 = api["client"].portal.call(lambda: off.judge_proposal({"title": "t", "command": "c" * 30}, goals="", inventory="", tree="", history="", mode="manual"))
    assert v2.verdict == "unavailable" and not v2.ok
    decs = [
        {"proposal_id": "p1", "actor": "judge", "stage": "proposal", "decision": "approve"},
        {"proposal_id": "p1", "actor": "human", "stage": "proposal", "decision": "pick"},
        {"proposal_id": "p2", "actor": "judge", "stage": "proposal", "decision": "approve"},
        {"proposal_id": "p2", "actor": "human", "stage": "proposal", "decision": "decline"},
        {"proposal_id": "p3", "actor": "judge", "stage": "merge", "decision": "approve"},
        {"proposal_id": "p3", "actor": "human", "stage": "merge", "decision": "approve"},
    ]
    cal = jm.calibration(decs)
    assert cal["gate1"]["pairs"] == 2 and cal["gate1"]["agreement"] == 0.5 and cal["gate1"]["disagreements"][0]["proposal_id"] == "p2"
    assert cal["gate2"]["pairs"] == 1 and cal["gate2"]["agreement"] == 1.0
    hb = jm.history_block(decs, {"p2": {"title": "Second", "scope": "harness", "direction": "tools"}})
    assert "Second" in hb and "human DECLINE" in hb


# ---------- routes ----------


def test_goals_derive_route_marks_present_and_missing(api):
    bid = _make_brief(api)
    r = api["client"].get(f"/evolution/goals/{bid}", headers=api["headers"])
    assert r.status_code == 200 and r.json()["subgoals"] == []
    r = api["client"].post(f"/evolution/goals/{bid}/derive", json={"approach_hints": "use AlphaFold-style prediction then dock ATP"}, headers=api["headers"])
    assert r.status_code == 200, r.text
    ledger = r.json()
    assert [s["id"] for s in ledger["subgoals"]] == ["g1", "g2", "g3"] and ledger["current"] == "g1"
    assert ledger["approach_hints"].startswith("use AlphaFold")
    statuses = {w["name"]: w["status"] for w in ledger["capability_wishlist"]}
    assert statuses["latex_compile"] == "present:latex_compile"   # already in tools/
    assert statuses["3D structure viewer"] == "missing"
    assert ledger["derived"]["served_by"] == "fake-claude"
    # Researcher reorders + marks current; a re-derive keeps their plan.
    r = api["client"].put(f"/evolution/goals/{bid}", json={"order": ["g2", "g1", "g3"], "current": "g2"}, headers=api["headers"])
    assert r.status_code == 200 and [s["id"] for s in r.json()["subgoals"]] == ["g2", "g1", "g3"]
    r = api["client"].post(f"/evolution/goals/{bid}/derive", json={}, headers=api["headers"])
    assert [s["id"] for s in r.json()["subgoals"]] == ["g2", "g1", "g3"] and r.json()["researcher_ordered"]
    assert any(l["brief_id"] == bid for l in api["client"].get("/evolution/goals", headers=api["headers"]).json())
    assert api["client"].get("/evolution/goals/nope", headers=api["headers"]).status_code == 404


def test_plan_creates_judged_proposals_and_dedupes(api):
    bid = _make_brief(api)
    run = _plan_and_wait(api, bid)
    assert run["served_by"] == "fake-claude" and run["error"] is None
    assert "goals" in api["model"].calls  # first contact derives the ledger
    data = _proposals(api, bid)
    props = data["proposals"]
    assert len(props) == 3 and data["latest_run"]["id"] == run["id"] and data["mode"] == "manual"
    by_title = {p["title"]: p for p in props}
    assert by_title["Weaken the merge gate to skip smoke"]["judge"]["verdict"] == "decline"
    assert by_title["Add a structure predictor tool"]["judge"]["verdict"] == "approve"
    assert by_title["Add a 3D structure viewer UI panel"]["scope"] == "platform"
    assert all(p["status"] == "proposed" and p["goal_ids"] == ["g2"] for p in props)
    # the judge saw the goal ledger and the harness inventory
    assert "Sequential subgoals" in api["judge"].calls[0]["system"] and "latex_compile" in api["judge"].calls[0]["system"]
    decs = api["evo_store"].decisions(api["ctx"].state)
    assert sum(1 for d in decs if d["actor"] == "judge" and d["stage"] == "proposal") == 3
    ledger = api["client"].get(f"/evolution/goals/{bid}", headers=api["headers"]).json()
    assert ledger["inferred_current"] == "g1" and ledger["phase"]["confidence"] == 0.6
    kinds = [e["kind"] for e in api["evo_store"].read_events(api["ctx"].state)]
    assert "planner.ready" in kinds and "proposal.created" in kinds and "judge.verdict" in kinds
    # A second run proposing the same things adds nothing.
    run2 = _plan_and_wait(api, bid)
    assert run2["proposal_ids"] == [] and len(_proposals(api, bid)["proposals"]) == 3
    # busy lock returns 409 while a run holds it
    api["planner"].acquire(api["ctx"].state, bid, "tok-a")
    assert api["client"].post("/evolution/plan", json={"brief_id": bid}, headers=api["headers"]).status_code == 409
    # A run that lost its lock to a stale takeover must not unlock the new holder.
    api["planner"].release(api["ctx"].state, bid, "tok-someone-else")
    assert api["planner"].is_running(api["ctx"].state, bid)
    api["planner"].release(api["ctx"].state, bid, "tok-a")
    assert not api["planner"].is_running(api["ctx"].state, bid)
    assert api["client"].post("/evolution/plan", json={"brief_id": "nope"}, headers=api["headers"]).status_code == 404


def test_plan_survives_model_failure(api):
    bid = _make_brief(api)
    api["model"].fail = True
    run = _plan_and_wait(api, bid)
    assert run["error"] and not run.get("proposal_ids")
    assert _proposals(api, bid)["proposals"] == []
    assert any(e["kind"] == "planner.failed" for e in api["evo_store"].read_events(api["ctx"].state))


def test_decide_pick_launches_links_and_replans_on_merge(api):
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    props = _proposals(api, bid)["proposals"]
    target = next(p for p in props if p["title"] == "Add a structure predictor tool")
    r = api["client"].post(f"/evolution/proposals/{target['id']}/decide", json={"decision": "pick", "note": "yes please"}, headers=api["headers"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "launched" and len(body["launched"]) == 1
    sid = body["launched"][0]["session_id"]
    assert api["spawned"][-1]["command"] == target["command"] and api["spawned"][-1]["scope"] == "harness"
    p = api["evo_store"].load_proposal(api["ctx"].state, target["id"])
    assert p["status"] == "implementing" and p["session_id"] == sid and p["human"]["decision"] == "pick"
    ev = api["client"].get(f"/sessions/{sid}/events", headers=api["headers"]).json()
    assert ev[0]["kind"] == "evolution.requested" and ev[0]["proposal_id"] == target["id"]
    # deciding again is refused; the evolution slot is taken
    assert api["client"].post(f"/evolution/proposals/{target['id']}/decide", json={"decision": "pick"}, headers=api["headers"]).status_code == 409
    assert api["client"].post("/evolution/commands", json={"command": "another"}, headers=api["headers"]).status_code == 409
    runs_before = api["evo_store"].latest_run(api["ctx"].state, bid)["id"]
    _finish_evo(api, sid, merged=True, version_id="v_pred")
    p = api["evo_store"].load_proposal(api["ctx"].state, target["id"])
    assert p["status"] == "merged" and p["version_id"] == "v_pred"
    # a merge re-plans for the brief
    _wait(lambda: api["evo_store"].latest_run(api["ctx"].state, bid)["id"] != runs_before)
    assert api["evo_store"].latest_run(api["ctx"].state, bid)["trigger"] == "evolution.merged"


def test_decide_both_queues_second_then_drains(api):
    """Two harness proposals: the first launches, the second queues, and it
    launches by itself once the slot frees."""
    bid = _make_brief(api)
    api["model"].next_plan = _plan_json(["Add a structure predictor tool", "Add a pocket finder tool"])
    _plan_and_wait(api, bid)
    props = {p["title"]: p for p in _proposals(api, bid)["proposals"]}
    a, b = props["Add a structure predictor tool"], props["Add a pocket finder tool"]
    r = api["client"].post(f"/evolution/proposals/{a['id']}/decide", json={"decision": "both", "with_id": b["id"]}, headers=api["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["launched"][0]["proposal_id"] == a["id"] and r.json()["queued"] == [b["id"]]
    sid_a = r.json()["launched"][0]["session_id"]
    assert api["evo_store"].load_proposal(api["ctx"].state, b["id"])["status"] == "queued"
    _finish_evo(api, sid_a, merged=False)
    assert api["evo_store"].load_proposal(api["ctx"].state, a["id"])["status"] == "rejected"
    # A HUMAN queued the second one, so it drains even in manual mode.
    pb = _wait(lambda: (lambda x: x if x["status"] == "implementing" else None)(api["evo_store"].load_proposal(api["ctx"].state, b["id"])))
    assert pb["session_id"] and pb["human"]["decision"] == "both"


def test_platform_proposal_is_refused_before_any_decision_is_recorded(api):
    """A proposal this deployment cannot run must not burn a decision: picking it
    returns 501 and leaves the eval folder / calibration untouched."""
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    plat = next(p for p in _proposals(api, bid)["proposals"] if p["scope"] == "platform")
    r = api["client"].post(f"/evolution/proposals/{plat['id']}/decide", json={"decision": "pick"}, headers=api["headers"])
    assert r.status_code == 501
    after = api["evo_store"].load_proposal(api["ctx"].state, plat["id"])
    assert after["status"] == "proposed" and after["human"] is None
    assert [d for d in api["evo_store"].decisions(api["ctx"].state, proposal_id=plat["id"]) if d["actor"] == "human"] == []
    # ...and through the edit-and-run route, which must use the PROPOSAL's scope
    # rather than the request's default "harness".
    r = api["client"].post("/evolution/commands", json={"command": plat["command"], "proposal_id": plat["id"]}, headers=api["headers"])
    assert r.status_code == 501
    assert api["evo_store"].load_proposal(api["ctx"].state, plat["id"])["human"] is None


def test_decline_is_remembered_and_not_reproposed(api):
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    props = {p["title"]: p for p in _proposals(api, bid)["proposals"]}
    t = props["Add a structure predictor tool"]
    r = api["client"].post(f"/evolution/proposals/{t['id']}/decide", json={"decision": "decline", "note": "we already have a predictor offline"}, headers=api["headers"])
    assert r.status_code == 200 and r.json()["status"] == "declined"
    assert api["evo_store"].load_proposal(api["ctx"].state, t["id"])["status"] == "declined"
    # the eval folder now carries the note, and the planner sees it
    run = _plan_and_wait(api, bid)
    assert run["proposal_ids"] == []
    assert "we already have a predictor offline" in run["prompt"]
    # the judge's next prompt carries the human's taste
    api["model"].next_plan = _plan_json(["Add a docking tool"])
    _plan_and_wait(api, bid)
    assert "human DECLINE" in api["judge"].calls[-1]["system"]
    cal = api["client"].get("/evolution/judge", headers=api["headers"]).json()["calibration"]
    assert cal["gate1"]["pairs"] == 1 and cal["gate1"]["disagreements"][0]["proposal_id"] == t["id"]


def test_automatic_mode_requires_judge_then_auto_launches(api, monkeypatch):
    server = api["server"]
    # judge unavailable → automatic mode refused
    monkeypatch.setattr(server, "_judge_for", lambda ctx: api["judge_mod"].Judge())
    r = api["client"].put("/evolution/mode", json={"mode": "automatic"}, headers=api["headers"])
    assert r.status_code == 409
    monkeypatch.setattr(server, "_judge_for", lambda ctx: api["judge_mod"].Judge(call=api["judge"]))
    r = api["client"].put("/evolution/mode", json={"mode": "automatic"}, headers=api["headers"])
    assert r.status_code == 200 and r.json()["mode"] == "automatic" and r.json()["judge"]["available"]
    bid = _make_brief(api)
    api["model"].next_plan = _plan_json(["Add a structure predictor tool", "Add a pocket finder tool", "Weaken the merge gate to skip smoke"])
    _plan_and_wait(api, bid)
    props = _wait(lambda: (lambda ps: ps if any(p["status"] == "implementing" for p in ps) else None)(_proposals(api, bid)["proposals"]))
    by = {p["title"]: p for p in props}
    assert by["Add a structure predictor tool"]["status"] == "implementing"   # highest judge score launches first
    assert by["Add a pocket finder tool"]["status"] == "queued"
    # In automatic mode the judge decides gate 1, so its decline is recorded as
    # one — the proposal leaves the open list instead of keeping a live Run button.
    assert by["Weaken the merge gate to skip smoke"]["status"] == "declined"
    assert by["Add a structure predictor tool"]["launched_by"] == "judge"
    sid = by["Add a structure predictor tool"]["session_id"]
    ev = api["client"].get(f"/sessions/{sid}/events", headers=api["headers"]).json()
    assert any(e["kind"] == "evolution.judge_approved" and e["why"] for e in ev)   # the human is told why
    kinds = [e["kind"] for e in api["evo_store"].read_events(api["ctx"].state)]
    assert "proposal.auto_approved" in kinds and "proposal.launched" in kinds
    # Switching back to manual stops the JUDGE-queued backlog from draining:
    # in manual mode the human decides both gates (Decision 2).
    api["client"].put("/evolution/mode", json={"mode": "manual"}, headers=api["headers"])
    _finish_evo(api, sid, merged=True, version_id="v1")
    time.sleep(0.4)
    still = api["evo_store"].load_proposal(api["ctx"].state, by["Add a pocket finder tool"]["id"])
    assert still["status"] == "queued" and not still.get("session_id"), "manual mode must not launch a judge-queued proposal"
    # Flipping back to automatic does not fire the stale backlog either — those
    # verdicts were formed under manual framing and may be days old.
    api["client"].put("/evolution/mode", json={"mode": "automatic"}, headers=api["headers"])
    time.sleep(0.3)
    assert api["evo_store"].load_proposal(api["ctx"].state, by["Add a pocket finder tool"]["id"])["status"] == "queued"


def test_merge_gate_judge_review_manual_and_automatic(api):
    server, ctx, es = api["server"], api["ctx"], api["evo_store"]
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    t = next(p for p in _proposals(api, bid)["proposals"] if p["title"] == "Add a structure predictor tool")
    sid = api["client"].post(f"/evolution/proposals/{t['id']}/decide", json={"decision": "pick"}, headers=api["headers"]).json()["launched"][0]["session_id"]
    payload = {"branch": "evo/x", "diff_preview": "+def predict(): ...", "rationale": "adds predictor", "smoke": {"ran": True, "ok": True}, "strict": False, "summary": "Add predictor"}
    rid = "req1"
    server.write_json(server._pending_dir(ctx, sid) / f"{rid}.json", {"id": rid, "ts": "t", "kind": "evolution_merge", "summary": "Merge proposal: Add predictor", "payload": payload, "decision": None})
    # manual mode: the watcher writes a recommendation next to the pending card
    rec = _wait(lambda: (lambda pend: pend[0] if pend and pend[0].get("judge") else None)(api["client"].get(f"/hitl/{sid}/pending", headers=api["headers"]).json()))
    assert rec["judge"]["verdict"] == "approve" and rec["judge"]["recommendation"] and rec["judge"]["mode"] == "manual"
    assert (server._pending_dir(ctx, sid) / f"{rid}.json").exists()   # NOT answered for the human
    assert any(e["kind"] == "judge.review" and e["ref"] == rid for e in api["client"].get(f"/sessions/{sid}/events", headers=api["headers"]).json())
    # the human answers → recorded as a gate-2 decision for this proposal
    r = api["client"].post(f"/hitl/{sid}/{rid}/answer", json={"decision": "approve", "note": "looks right"}, headers=api["headers"])
    assert r.status_code == 200
    decs = es.decisions(ctx.state, proposal_id=t["id"])
    assert [d["decision"] for d in decs if d["stage"] == "merge"] == ["approve", "approve"] and decs[-1]["actor"] == "human"
    # automatic mode: the judge answers the gate itself and says why
    api["client"].put("/evolution/mode", json={"mode": "automatic"}, headers=api["headers"])
    rid2 = "req2"
    server.write_json(server._pending_dir(ctx, sid) / f"{rid2}.json", {"id": rid2, "ts": "t", "kind": "evolution_merge", "summary": "Merge proposal: Add predictor v2", "payload": payload, "decision": None})
    ans = _wait(lambda: server.read_json(server._answered_dir(ctx, sid) / f"{rid2}.json", None))
    assert ans["decision"] == "approve" and ans["answered_by"] == "judge" and ans["note"].startswith("Judge approved:")
    ev = api["client"].get(f"/sessions/{sid}/events", headers=api["headers"]).json()
    assert any(e["kind"] == "hitl.auto_answer" and e["actor"] == "judge" and e["ref"] == rid2 for e in ev)
    # a rejected merge in automatic mode sends the agent back with the reason, at most N times
    bad_payload = dict(payload, rationale="weaken the gate and skip smoke", diff_preview="-run_smoke()")
    for i in range(api["settings"].JUDGE_MAX_AUTO_REJECTS + 1):
        rid_i = f"bad{i}"
        server.write_json(server._pending_dir(ctx, sid) / f"{rid_i}.json", {"id": rid_i, "ts": "t", "kind": "evolution_merge", "summary": "Merge proposal: bad", "payload": bad_payload, "decision": None})
        if i < api["settings"].JUDGE_MAX_AUTO_REJECTS:
            ans = _wait(lambda: server.read_json(server._answered_dir(ctx, sid) / f"{rid_i}.json", None))
            assert ans["decision"] == "reject" and ans["note"].startswith("Judge requests changes:")
        else:
            _wait(lambda: any(e["kind"] == "judge.deferred_to_human" and e["ref"] == rid_i for e in api["client"].get(f"/sessions/{sid}/events", headers=api["headers"]).json()))
            assert (server._pending_dir(ctx, sid) / f"{rid_i}.json").exists()   # left for the human


def test_launch_brief_plans_proactively_and_completion_records_adoption(api):
    server, ctx, es = api["server"], api["ctx"], api["evo_store"]
    bid = _make_brief(api)
    r = api["client"].post(f"/onboarding/briefs/{bid}/launch", json={}, headers=api["headers"])
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    run = _wait(lambda: es.latest_run(ctx.state, bid))
    assert run["trigger"] == "launch" and len(run["proposal_ids"]) == 3   # proposals exist before the session did any work
    # the research turn completes cleanly → adoption is recorded and a re-plan fires
    server._append_event(ctx, sid, actor="supervisor", kind="tool.use", tool="mcp__py_exec__run")
    server._append_event(ctx, sid, actor="supervisor", kind="tool.use", tool="mcp__py_exec__run")
    server._append_event(ctx, sid, actor="system", kind="research.complete", reason="finished")
    api["client"].portal.call(server._record_adoption, ctx, sid)
    ad = es.load_adoption(ctx.state)["versions"]["bootstrap"]
    assert ad["sessions"] == 1 and ad["tool_uses"] == {"mcp__py_exec__run": 2}
    api["client"].portal.call(lambda: server._schedule_plan_for_session(ctx, sid, trigger="research.complete"))
    _wait(lambda: es.latest_run(ctx.state, bid)["trigger"] == "research.complete")
    # the planner saw the session in its status block
    assert sid in es.latest_run(ctx.state, bid)["prompt"]


def test_platform_scope_blocked_until_enabled(api):
    r = api["client"].post("/evolution/commands", json={"command": "restyle the evolution tab", "scope": "platform"}, headers=api["headers"])
    assert r.status_code == 501
    r = api["client"].post("/evolution/commands", json={"command": "x", "scope": "bogus"}, headers=api["headers"])
    assert r.status_code == 400


def test_tenant_prompt_override_is_used(api):
    bid = _make_brief(api)
    (api["ctx"].root / "prompts" / "evo_plan.md").write_text("MY OVERRIDE {{MISSION}} {{STATUS}} {{EVAL_FOLDER}} {{HARNESS_INVENTORY}} {{PLATFORM_INVENTORY}} {{TRIGGER}} {{MODE}}", encoding="utf-8")
    run = _plan_and_wait(api, bid)
    assert run["prompt"].startswith("MY OVERRIDE") and run["prompt_path"].endswith("evo_plan.md")


# ---------- regressions from the adversarial review (see tests/test_evo_fixes.py) ----------


def test_strict_path_merge_is_never_auto_answered_by_the_judge(api):
    """CLAUDE.md mandates a stricter human gate for scaffold/, evolution/,
    pyproject.toml and Dockerfile — the gate machinery itself. Automatic mode
    must review those and hand them to the human, never answer them."""
    server, ctx = api["server"], api["ctx"]
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    t = next(p for p in _proposals(api, bid)["proposals"] if p["title"] == "Add a structure predictor tool")
    sid = api["client"].post(f"/evolution/proposals/{t['id']}/decide", json={"decision": "pick"}, headers=api["headers"]).json()["launched"][0]["session_id"]
    api["client"].put("/evolution/mode", json={"mode": "automatic"}, headers=api["headers"])
    rid = "strictreq"
    server.write_json(server._pending_dir(ctx, sid) / f"{rid}.json", {
        "id": rid, "ts": "t", "kind": "evolution_merge", "summary": "Merge: touch the gate",
        "payload": {"branch": "evo/x", "summary": "s", "rationale": "r", "strict": True,
                    "smoke": {"ran": True, "ok": True}, "diff_preview": "+ scaffold/hitl.py change"},
        "decision": None,
    })
    _wait(lambda: any(e["kind"] == "judge.deferred_to_human" and e.get("ref") == rid
                      for e in api["client"].get(f"/sessions/{sid}/events", headers=api["headers"]).json()))
    assert (server._pending_dir(ctx, sid) / f"{rid}.json").exists(), "a strict merge must stay pending for the human"
    assert not (server._answered_dir(ctx, sid) / f"{rid}.json").exists()
    # The judge still REVIEWED it — the recommendation is on the card.
    pend = api["client"].get(f"/hitl/{sid}/pending", headers=api["headers"]).json()
    assert pend and pend[0]["judge"]["verdict"] in ("approve", "decline")


def test_watcher_keys_requests_by_filename_not_by_tenant_written_id(api):
    """The request id is used to build platform-side file paths, and the record's
    own `id` field is written by (evolvable) tenant code."""
    server, ctx = api["server"], api["ctx"]
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    t = next(p for p in _proposals(api, bid)["proposals"] if p["title"] == "Add a structure predictor tool")
    sid = api["client"].post(f"/evolution/proposals/{t['id']}/decide", json={"decision": "pick"}, headers=api["headers"]).json()["launched"][0]["session_id"]
    rid = "safe-id"
    server.write_json(server._pending_dir(ctx, sid) / f"{rid}.json", {
        "id": "../../../../escape", "ts": "t", "kind": "evolution_merge", "summary": "s",
        "payload": {"branch": "b", "summary": "s", "rationale": "r", "smoke": {"ran": False}, "diff_preview": "+x"},
        "decision": None,
    })
    _wait(lambda: (server._judge_dir(ctx, sid) / f"{rid}.json").exists())
    assert not (ctx.state / "escape.json").exists() and not (ctx.root.parent / "escape.json").exists()


def test_judge_key_is_not_passed_into_tenant_subprocesses(api, monkeypatch):
    """The tenant's evolution agent can run `echo $VAR` through bash_ro."""
    monkeypatch.setenv("COSCIENTIST_JUDGE_OPENAI_API_KEY", "sk-judge-secret-value-123456")
    env = api["server"]._runtime_env(api["ctx"])
    assert "COSCIENTIST_JUDGE_OPENAI_API_KEY" not in env
    assert not any("sk-judge-secret" in str(v) for v in env.values())


def test_adoption_is_recorded_per_turn_not_per_session(api):
    """R10 sessions have many turns; deduping on the session id alone hid every
    turn after the first from the planner's adoption signal."""
    server, ctx, es = api["server"], api["ctx"], api["evo_store"]
    sid = "s-multi"
    server._ensure_session_dirs(ctx, sid)
    server._append_event(ctx, sid, actor="supervisor", kind="tool.use", tool="mcp__py_exec__run")
    server._append_event(ctx, sid, actor="system", kind="research.complete", reason="finished")
    api["client"].portal.call(server._record_adoption, ctx, sid)
    server._append_event(ctx, sid, actor="supervisor", kind="tool.use", tool="mcp__ocr__read")
    server._append_event(ctx, sid, actor="system", kind="research.complete", reason="finished")
    api["client"].portal.call(server._record_adoption, ctx, sid)
    ad = es.load_adoption(ctx.state)["versions"]["bootstrap"]
    assert ad["sessions"] == 2 and ad["tool_uses"]["mcp__ocr__read"] == 1


def test_monitor_hooks_actually_run_the_r19_loop(api):
    """The R19 hooks in _monitor_runtime were never exercised: the suite called
    _after_evolution / _record_adoption directly, so deleting the hooks left
    every test green. Drive the real monitor instead."""
    server, ctx, es = api["server"], api["ctx"], api["evo_store"]
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    t = next(p for p in _proposals(api, bid)["proposals"] if p["title"] == "Add a structure predictor tool")
    sid = api["client"].post(f"/evolution/proposals/{t['id']}/decide", json={"decision": "pick"}, headers=api["headers"]).json()["launched"][0]["session_id"]
    server._append_event(ctx, sid, actor="evolution", kind="version.recorded", version="v_hook", tag="ver/v_hook")
    server._append_event(ctx, sid, actor="evolution", kind="evolution.merged", ref="x")

    class _Proc:
        async def wait(self):
            return 0

    class _FH:
        def close(self):
            pass

    server._RUNNING[server._monitor_key(ctx, sid)] = _Proc()
    api["client"].portal.call(lambda: server._monitor_runtime(ctx, sid, _Proc(), _FH()))
    assert es.load_proposal(ctx.state, t["id"])["status"] == "merged"


def test_restart_reconciliation_unsticks_implementing_proposals(api):
    """Outcomes are driven by the in-memory _RUNNING table, so an API restart
    used to leave proposals pinned at 'implementing' and the queue stalled."""
    server, ctx, es = api["server"], api["ctx"], api["evo_store"]
    bid = _make_brief(api)
    _plan_and_wait(api, bid)
    t = next(p for p in _proposals(api, bid)["proposals"] if p["title"] == "Add a structure predictor tool")
    sid = api["client"].post(f"/evolution/proposals/{t['id']}/decide", json={"decision": "pick"}, headers=api["headers"]).json()["launched"][0]["session_id"]
    server._append_event(ctx, sid, actor="evolution", kind="evolution.merged", ref="x")
    server._append_event(ctx, sid, actor="evolution", kind="version.recorded", version="v_restart")
    # Simulate the restart: the process table is empty and nothing reconciled yet.
    server._RUNNING.clear()
    server._RECONCILED.clear()
    assert es.load_proposal(ctx.state, t["id"])["status"] == "implementing"
    api["client"].get(f"/evolution/proposals?brief_id={bid}", headers=api["headers"])
    _wait(lambda: es.load_proposal(ctx.state, t["id"])["status"] == "merged")


def test_gate2_backfills_smoke_from_the_archive_for_an_older_tenant(api):
    """Tenant roots are frozen forks, so a tenant on the pre-R19 merge tool sends
    no `smoke` key. The archive directory it points at holds the evidence; the
    API must read it rather than leaving the judge with UNKNOWN."""
    server, ctx = api["server"], api["ctx"]
    arch = ctx.root / "state" / "archive" / "evolutions" / "20260101-000000__x"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / "smoke.log").write_text("......\n12 passed, 1 skipped in 3.2s\n", encoding="utf-8")
    (arch / "rationale.md").write_text("# Add struct_view tool\n\nbecause…\n", encoding="utf-8")
    out = server._backfill_merge_payload(ctx, {"archive": str(arch), "strict": True, "diff_preview": "+x"})
    assert out["smoke"] == {"ran": True, "ok": True, "source": "archive/smoke.log"}
    assert out["summary"] == "Add struct_view tool"
    # a failing run is reported as failing
    (arch / "smoke.log").write_text("F...\n1 failed, 11 passed in 3.2s\n", encoding="utf-8")
    assert server._backfill_merge_payload(ctx, {"archive": str(arch), "strict": True})["smoke"]["ok"] is False
    # an archive outside this tenant's root is never read
    outside = api["ctx"].root.parent / "elsewhere"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "smoke.log").write_text("99 passed\n", encoding="utf-8")
    assert server._backfill_merge_payload(ctx, {"archive": str(outside), "strict": True}).get("smoke") is None
    # a payload that already carries a smoke result is left alone
    keep = {"archive": str(arch), "smoke": {"ran": False}}
    assert server._backfill_merge_payload(ctx, keep)["smoke"] == {"ran": False}


def test_wishlist_status_is_recomputed_on_read(api):
    """Live-run regression: statuses are a view of the CURRENT harness, so a
    capability that was merged since the last planner run must stop reading
    "missing" immediately, not at the next run."""
    bid = _make_brief(api)
    api["client"].post(f"/evolution/goals/{bid}/derive", json={}, headers=api["headers"])
    ledger = api["client"].get(f"/evolution/goals/{bid}", headers=api["headers"]).json()
    assert {w["name"]: w["status"] for w in ledger["capability_wishlist"]}["3D structure viewer"] == "missing"
    # The evolution lands a tool in the tenant's harness…
    (api["ctx"].root / "tools" / "struct_view").mkdir(parents=True, exist_ok=True)
    ledger = api["client"].get(f"/evolution/goals/{bid}", headers=api["headers"]).json()
    assert {w["name"]: w["status"] for w in ledger["capability_wishlist"]}["3D structure viewer"] == "present:struct_view"
    # …and the change is persisted, so the planner's next prompt sees it too.
    stored = api["evo_store"].load_goals(api["ctx"].state, bid)
    assert {w["name"]: w["status"] for w in stored["capability_wishlist"]}["3D structure viewer"] == "present:struct_view"
