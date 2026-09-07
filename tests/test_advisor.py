"""O2 background advisor with a fake model: snapshot bounds, validation of
every returned id, edges written, refusal → fallback, dedupe, empty tree,
caps, lock. Offline."""

from __future__ import annotations

import asyncio
import json
import types

from tests.conftest import ADJACENT, COMPLETE, UNRELATED, create_brief, launch, register, seed_version


def _fake_complete(answer: dict, served="claude-fable-5"):
    calls: list[dict] = []

    async def complete(system, user, *, max_tokens=0, api_key=None, **_):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return types.SimpleNamespace(text=json.dumps(answer), served_by=served, usage={"input_tokens": 1200, "output_tokens": 300}, attempts=[{"model": served, "outcome": "ok"}], error=None, ok=True)

    complete.calls = calls
    return complete


def _setup_donor(platform):
    """Alice: an evolved version (fancy_tool) linked to a launched, shared problem."""
    c, a = platform.client, platform.a
    ver = seed_version(platform.mods, a.ctx.root, a.uid)
    bid = create_brief(c, a.headers, ADJACENT)["id"]
    na = register(c, a.headers, bid)["node"]
    launch(c, a.headers, bid)
    return na, ver


def test_advisor_validates_ids_writes_edges_and_records_usage(platform):
    c, a, b = platform.client, platform.a, platform.b
    na, ver = _setup_donor(platform)
    nu = register(c, a.headers, create_brief(c, a.headers, UNRELATED)["id"])["node"]
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    answer = {
        "summary": "Bob's problem sits next to Alice's charge-noise spectroscopy; reuse her fitting tool.",
        "related_problems": [
            {"node_id": na["id"], "relation": "shares_method", "confidence": 0.8, "rationale": "Both fit CPMG decay curves on the same qubit type."},
            {"node_id": "p-20260101-000000", "relation": "adjacent", "confidence": 0.9, "rationale": "made up"},
            {"node_id": nu["id"], "relation": "adjacent", "confidence": 0.3, "rationale": "not a candidate"},
        ],
        "harness_import": {"recommended": True, "owner": a.uid, "version_id": ver["id"], "mode_hint": "adopt", "confidence": 0.7, "rationale": "fancy_tool fits spectral peaks, exactly the T2 fits Bob needs.", "alternatives": [{"owner": a.uid, "version_id": "nope", "why": "x"}]},
        "tool_imports": [
            {"name": "fancy_tool", "owner": a.uid, "version_id": ver["id"], "role_wiring": ["nonexistent_role"], "confidence": 0.9, "rationale": "peak fitting"},
            {"name": "py_exec", "owner": a.uid, "version_id": ver["id"], "role_wiring": ["data_analyst"], "confidence": 0.9, "rationale": "already there"},
            {"name": "ghost_tool", "owner": a.uid, "version_id": ver["id"], "role_wiring": ["data_analyst"], "confidence": 0.9, "rationale": "not in catalogue"},
        ],
        "statement_feedback": {"missing": [{"q": 4, "issue": "Row schema lacks the drive detuning column."}, "free text"], "notes": ["Baseline T2 should be restated per qubit."], "suggested_keywords": ["CPMG"]},
        "hypothesis_seed": {"suggested": True, "why": "mechanistic gap"},
        "start_from_scratch_rationale": "",
    }
    fake = _fake_complete(answer)
    adv = platform.mods.advisor
    rec = asyncio.run(adv.run(nb["id"], b.uid, "register", complete=fake))
    assert rec["status"] == "ready", rec
    assert rec["served_by"] == "claude-fable-5" and rec["usage"] == {"input_tokens": 1200, "output_tokens": 300}
    # Bounded snapshot: the prompt carried the candidate + catalogue, never tenant files.
    user_msg = fake.calls[0]["user"]
    assert na["id"] in user_msg and ver["id"] in user_msg and "fancy_tool" in user_msg
    assert "events.jsonl" not in user_msg
    snap = json.loads((adv.projects.recs_dir(nb["id"]) / f"snapshot-{rec['rec_id']}.json").read_text())
    assert [cnd["node_id"] for cnd in snap["candidates"]] == [na["id"]]
    assert snap["catalogue"]["versions"][0]["version_id"] == ver["id"] and snap["catalogue"]["tools"][0]["name"] == "fancy_tool"
    assert "fits spectral peaks" in snap["catalogue"]["tools"][0]["description"]
    # Validation: unknown/uncandidate nodes dropped, bogus alternative dropped,
    # own tool dropped, unknown tool dropped, bad role wiring replaced.
    assert [r["node_id"] for r in rec["related_problems"]] == [na["id"]]
    assert rec["harness_import"]["version_id"] == ver["id"] and rec["harness_import"]["alternatives"] == []
    assert rec["harness_import"]["owner_name"] == "alice" and rec["harness_import"]["from_node"] == na["id"]
    # Deterministic risks compare the donor tree at its sha with the importer's:
    # the donor (base tools + fancy_tool) lacks nothing the importer has.
    assert rec["harness_import"]["sha"] == ver["sha"]
    assert not [r for r in rec["harness_import"]["risks"] if "py_exec" in r or "supervisor" in r], rec["harness_import"]["risks"]
    assert [t["name"] for t in rec["tool_imports"]] == ["fancy_tool"] and rec["tool_imports"][0]["role_wiring"] == ["data_analyst"]
    notes = " ".join(rec["validation_notes"])
    assert "p-20260101-000000" in notes and "py_exec" in notes and "ghost_tool" in notes and "nonexistent_role" in notes
    assert rec["statement_feedback"]["missing"][0]["q"] == 4 and rec["statement_feedback"]["missing"][1]["q"] == -1
    assert rec["hypothesis_seed"]["suggested"] is True
    # A typed advisor edge landed (proposed) and the API surfaces the record.
    edges = c.get(f"/projects/{nb['id']}", headers=b.headers).json()["edges"]
    adv_edge = [e for e in edges if e["source"] == "advisor"]
    assert len(adv_edge) == 1 and adv_edge[0]["type"] == "shares_method" and adv_edge[0]["rec_id"] == rec["rec_id"]
    got = c.get(f"/projects/{nb['id']}/recommendations", headers=b.headers).json()
    assert got["running"] is False and got["latest"]["rec_id"] == rec["rec_id"]
    assert c.get(f"/projects/{nb['id']}/recommendations", headers=a.headers).status_code == 403
    assert c.get(f"/projects/{nb['id']}", headers=b.headers).json()["recommendation"]["harness_import"] is True
    ev = [json.loads(l) for l in (adv.projects.events_path()).read_text().splitlines()]
    assert [e["kind"] for e in ev if e["kind"].startswith("advisor.")] == ["advisor.started", "advisor.ready"]


def test_unchanged_statement_skips_and_manual_reruns(platform):
    c, b = platform.client, platform.b
    _setup_donor(platform)
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    fake = _fake_complete({"summary": "ok", "related_problems": [], "harness_import": None, "tool_imports": [], "statement_feedback": {"missing": [], "notes": [], "suggested_keywords": []}, "hypothesis_seed": {"suggested": False, "why": ""}, "start_from_scratch_rationale": "nothing fits"})
    adv = platform.mods.advisor
    r1 = asyncio.run(adv.run(nb["id"], b.uid, "register", complete=fake))
    assert r1["status"] == "ready" and r1["harness_import"] is None and r1["start_from_scratch_rationale"] == "nothing fits"
    r2 = asyncio.run(adv.run(nb["id"], b.uid, "register", complete=fake))
    assert r2["rec_id"] == r1["rec_id"] and r2["skipped_reason"] == "unchanged statement" and len(fake.calls) == 1
    r3 = asyncio.run(adv.run(nb["id"], b.uid, "manual", complete=fake))
    assert r3["rec_id"] != r1["rec_id"] and len(fake.calls) == 2


def test_empty_tree_skips_without_a_model_call_and_names_gaps(platform):
    c, b = platform.client, platform.b
    nb = register(c, b.headers, create_brief(c, b.headers, {k: v for k, v in COMPLETE.items() if k not in ("task_definition", "existing_results", "data_access")})["id"])["node"]
    fake = _fake_complete({})
    rec = asyncio.run(platform.mods.advisor.run(nb["id"], b.uid, "register", complete=fake))
    assert rec["status"] == "skipped" and rec["reason"] == "empty tree" and fake.calls == []
    assert rec["statement_feedback"]["missing"] == [{"q": 4, "issue": "not answered: task_definition, existing_results, data_access"}]
    got = c.get(f"/projects/{nb['id']}/recommendations", headers=b.headers).json()
    assert got["latest"]["status"] == "skipped"


def test_model_failure_and_bad_json_land_as_failed_records(platform):
    c, b = platform.client, platform.b
    _setup_donor(platform)
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    adv = platform.mods.advisor

    async def broken(system, user, **_):
        return types.SimpleNamespace(text="", served_by="claude-opus-4-8", usage={"input_tokens": 0, "output_tokens": 0}, attempts=[{"model": "claude-fable-5", "outcome": "refusal"}, {"model": "claude-opus-4-8", "outcome": "error: boom"}], error="claude-fable-5: refusal; claude-opus-4-8: error: boom", ok=False)

    rec = asyncio.run(adv.run(nb["id"], b.uid, "manual", complete=broken))
    assert rec["status"] == "failed" and "refusal" in rec["error"] and rec["served_by"] == "claude-opus-4-8"

    async def prose(system, user, **_):
        return types.SimpleNamespace(text="I cannot produce JSON today.", served_by="claude-fable-5", usage={}, attempts=[], error=None, ok=True)

    rec = asyncio.run(adv.run(nb["id"], b.uid, "manual", complete=prose))
    assert rec["status"] == "failed" and "no JSON" in rec["error"]
    assert c.get(f"/projects/{nb['id']}/recommendations", headers=b.headers).json()["latest"]["status"] == "failed"


def test_lock_and_daily_cap(platform, monkeypatch):
    c, b = platform.client, platform.b
    _setup_donor(platform)
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    adv = platform.mods.advisor
    import os

    # Another live process (our parent, here) holds the lock → this pass is skipped.
    adv.hold_lock_for(nb["id"], os.getppid())
    rec = asyncio.run(adv.run(nb["id"], b.uid, "manual", complete=_fake_complete({})))
    assert rec["status"] == "skipped" and rec["reason"] == "already running"
    assert adv.is_running(nb["id"]) is True
    adv.lock_path(nb["id"]).unlink()
    # A stale lock (dead pid) is cleaned.
    adv.lock_path(nb["id"]).write_text("999999 1\n")
    assert adv.is_running(nb["id"]) is False
    monkeypatch.setattr(adv, "DAILY_CAP_USER", 1)
    ok = _fake_complete({"summary": "s", "related_problems": [], "harness_import": None, "tool_imports": [], "statement_feedback": {"missing": [], "notes": [], "suggested_keywords": []}, "hypothesis_seed": {"suggested": False, "why": ""}, "start_from_scratch_rationale": ""})
    assert asyncio.run(adv.run(nb["id"], b.uid, "manual", complete=ok))["status"] == "ready"
    capped = asyncio.run(adv.run(nb["id"], b.uid, "manual", complete=ok))
    assert capped["status"] == "failed" and "cap" in capped["error"]


def test_llm_falls_back_from_fable_refusal_to_opus(platform, monkeypatch):
    llm = platform.mods.llm

    class _Resp:
        def __init__(self, text, stop):
            self.stop_reason = stop
            self.content = [types.SimpleNamespace(type="text", text=text)]
            self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)

    class _Messages:
        async def create(self, *, model, **kw):
            if model.startswith("claude-fable"):
                return _Resp("", "refusal")
            return _Resp('{"summary": "from opus"}', "end_turn")

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

        async def close(self):
            pass

    monkeypatch.setattr(llm, "AsyncAnthropic", _Client)
    res = asyncio.run(llm.complete("sys", "user", max_tokens=100))
    assert res.ok and res.served_by == llm.FALLBACK_MODEL and res.usage == {"input_tokens": 20, "output_tokens": 10}
    assert [a["outcome"] for a in res.attempts] == ["refusal", "ok"]
    assert llm.parse_json_object(res.text) == {"summary": "from opus"}
    assert llm.parse_json_array("here: [1, 2]") == [1, 2] and llm.parse_json_array("```json\n[3]\n```") == [3]


def test_advisor_ready_is_mirrored_into_the_owners_session(platform):
    c, b = platform.client, platform.b
    _setup_donor(platform)
    bid = create_brief(c, b.headers, COMPLETE)["id"]
    nb = register(c, b.headers, bid)["node"]
    sid = launch(c, b.headers, bid)["session_id"]
    fake = _fake_complete({"summary": "mirrored", "related_problems": [], "harness_import": None, "tool_imports": [], "statement_feedback": {"missing": [], "notes": [], "suggested_keywords": []}, "hypothesis_seed": {"suggested": False, "why": ""}, "start_from_scratch_rationale": ""})
    rec = asyncio.run(platform.mods.advisor.run(nb["id"], b.uid, "manual", complete=fake))
    ev = c.get(f"/sessions/{sid}/events", headers=b.headers).json()
    card = [e for e in ev if e["kind"] == "advisor.ready"]
    assert len(card) == 1 and card[0]["rec_id"] == rec["rec_id"] and card[0]["actor"] == "advisor"


def test_server_held_lock_is_taken_over_and_rerun_flag_runs_again(platform):
    c, b = platform.client, platform.b
    _setup_donor(platform)
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    adv = platform.mods.advisor
    import os

    # The server wrote the lock on the child's behalf (our pid here) → the run proceeds.
    adv.hold_lock_for(nb["id"], os.getpid())
    assert adv.is_running(nb["id"]) is True
    ok = _fake_complete({"summary": "s", "related_problems": [], "harness_import": None, "tool_imports": [], "statement_feedback": {"missing": [], "notes": [], "suggested_keywords": []}, "hypothesis_seed": {"suggested": False, "why": ""}, "start_from_scratch_rationale": ""})
    # A statement change arrives while "running": the flag makes the pass go again.
    adv.request_rerun(nb["id"])
    rec = asyncio.run(adv.run(nb["id"], b.uid, "manual", complete=ok))
    assert rec["status"] == "ready" and rec["trigger"] == "rerun" and len(ok.calls) == 2
    assert not adv.rerun_flag(nb["id"]).exists() and adv.is_running(nb["id"]) is False
    # A lock held by another live process is never released by us.
    adv.hold_lock_for(nb["id"], os.getppid())
    adv.release_lock(nb["id"])
    assert adv.lock_path(nb["id"]).exists()
    adv.lock_path(nb["id"]).unlink()
