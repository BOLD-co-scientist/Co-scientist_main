"""O1 onboarding phase: fixed-format problem brief → data → hypothesis search
→ launch. Drives the real FastAPI routes through TestClient with the research
subprocess and the model call monkeypatched, so it runs offline in seconds.
See docs/plans/O1-onboarding.md."""

from __future__ import annotations

import importlib
import json
import shutil
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


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A fresh tenant + TestClient; returns (client, headers, ctx, server, spawned)."""
    _seed_base(tmp_path)
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings

    importlib.reload(settings)
    import api.auth as auth
    import api.tenancy as tenancy
    import api.onboarding as onboarding
    import api.server as server

    importlib.reload(auth)
    importlib.reload(tenancy)
    importlib.reload(onboarding)
    importlib.reload(server)

    spawned: list[dict] = []

    async def fake_spawn(ctx, sid, message, resume_uuid=None):
        spawned.append({"sid": sid, "message": message, "resume": resume_uuid})

    monkeypatch.setattr(server, "_spawn_research", fake_spawn)

    async def fake_generate(goal, parent=None, feedback=None, n=4, context=None):
        cards = [
            {"statement": f"H{i}: {goal[:30]} via mechanism {i}", "rationale": f"because {i}"}
            for i in range(n)
        ]
        fake_generate.calls.append({"goal": goal, "context": context, "n": n, "parent": parent})
        return cards, "fake-model"

    fake_generate.calls = []
    monkeypatch.setattr(server._hypothesis, "generate", fake_generate)

    user, key = auth.create_user("alice")
    ctx = tenancy.context_for(user)
    client = TestClient(server.app)
    headers = {"Authorization": f"Bearer {key}"}
    return client, headers, ctx, server, spawned, fake_generate


FULL = {
    "title": "Antibiotic tolerance in stationary-phase E. coli",
    "domain": "microbiology",
    "background": "Some populations survive bactericidal antibiotics without resistance mutations.",
    "research_question": "What physiological state predicts tolerance in the LTEM strains?",
    "objectives": ["Quantify survival vs growth phase", "Identify a predictive marker"],
    "data_notes": "CSV growth curves, one file per strain.",
    "constraints": "No wet-lab work; analysis only; finish in one session.",
    "success_criteria": "A marker with AUC > 0.8 on held-out strains.",
    "deliverables": ["Report (PDF)", "Marker table (CSV)"],
}


def _upload(client, headers, name: str, content: bytes = b"a,b\n1,2\n", relpath: str | None = None):
    data = {"relpath": relpath} if relpath else {}
    r = client.post("/library/files", files={"file": (name, content)}, data=data, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- pure rendering ----------


def test_render_brief_fixed_section_order():
    from api import onboarding

    rec = onboarding.new_record(dict(FULL))
    text = onboarding.render_brief(rec)
    heads = [line for line in text.splitlines() if line.startswith("#")]
    # Brief v2 (O2): the five-question statement in a fixed order. An O1-era
    # record (background, no significance/open_gap) renders the same headings.
    assert heads == [
        "# Problem brief: Antibiotic tolerance in stationary-phase E. coli",
        "## Why this matters",
        "## Prior work and what remains open",
        "## Research question",
        "## Objectives",
        "## Evaluation protocol",
        "## Data",
        "## Constraints",
        "## Success criteria",
        "## Deliverables",
        "## Project tree context",
        "## Harness",
        "## Working hypothesis",
    ]
    # The O1 `background` is read into "prior work".
    assert "**What existing work has achieved:** " + FULL["background"] in text
    assert "1. Quantify survival vs growth phase" in text
    assert "No data files were attached" in text
    assert "No working hypothesis was selected" in text


def test_render_brief_empty_fields_get_explicit_placeholders():
    from api import onboarding

    rec = onboarding.new_record({"title": "T", "research_question": "Q"})
    text = onboarding.render_brief(rec)
    # Every section is present even when empty, so the shape is always the same.
    for h in ("## Why this matters", "## Prior work and what remains open", "## Objectives", "## Evaluation protocol", "## Constraints", "## Success criteria", "## Deliverables", "## Project tree context", "## Harness"):
        assert h in text
    assert "_Not stated — establish the state of the art" in text
    assert "_None stated._" in text
    assert "not registered on the project tree" in text


def test_brief_goal_uses_question_and_context():
    from api import onboarding

    rec = onboarding.new_record(dict(FULL, data=[{"path": "x.csv", "description": "growth"}]))
    goal, context = onboarding.brief_goal(rec)
    assert goal == FULL["research_question"]
    assert "Prior work:" in context and "Objectives:" in context
    assert "- x.csv: growth" in context and "Constraints:" in context
    # Title-only briefs still produce a goal.
    goal2, _ = onboarding.brief_goal(onboarding.new_record({"title": "Just a title"}))
    assert goal2 == "Just a title"


# ---------- CRUD + tenancy ----------


def test_brief_crud_roundtrip(api):
    client, headers, ctx, server, _spawned, _gen = api
    r = client.post("/onboarding/briefs", json={"title": "Draft one"}, headers=headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "draft" and rec["title"] == "Draft one"
    # Fixed shape: every field is present even when empty.
    for f in ("domain", "background", "research_question", "objectives", "data", "data_notes",
              "constraints", "success_criteria", "deliverables", "hypothesis"):
        assert f in rec
    bid = rec["id"]
    assert (ctx.state / "onboarding" / f"{bid}.json").exists()

    r = client.put(f"/onboarding/briefs/{bid}", json={"research_question": "Why?", "objectives": ["a", " ", "b"]}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["research_question"] == "Why?"
    assert r.json()["objectives"] == ["a", "b"]  # blanks dropped
    assert r.json()["title"] == "Draft one"  # untouched by the partial update

    lst = client.get("/onboarding/briefs", headers=headers).json()
    assert [b["id"] for b in lst] == [bid]
    assert lst[0]["status"] == "draft" and lst[0]["data_count"] == 0

    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["id"] == bid
    assert client.delete(f"/onboarding/briefs/{bid}", headers=headers).status_code == 200
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).status_code == 404
    assert client.get("/onboarding/briefs/nope", headers=headers).status_code == 404
    assert client.get("/onboarding/briefs/../x", headers=headers).status_code in (400, 404)


def test_briefs_are_tenant_scoped(api):
    client, headers_a, ctx_a, server, _s, _g = api
    from api import auth, tenancy

    user_b, key_b = auth.create_user("bob")
    headers_b = {"Authorization": f"Bearer {key_b}"}
    bid = client.post("/onboarding/briefs", json={"title": "A's"}, headers=headers_a).json()["id"]
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers_b).status_code == 404
    assert client.get("/onboarding/briefs", headers=headers_b).json() == []
    assert client.get("/onboarding/briefs").status_code == 401


# ---------- preview + launch validation ----------


def test_preview_reports_missing_required_fields(api):
    client, headers, *_ = api
    bid = client.post("/onboarding/briefs", json={"background": "ctx only"}, headers=headers).json()["id"]
    p = client.get(f"/onboarding/briefs/{bid}/preview", headers=headers).json()
    assert p["missing"] == ["title", "research_question"]
    assert p["text"].startswith("[Problem brief")
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
    assert r.status_code == 422
    assert r.json()["detail"]["missing"] == ["title", "research_question"]


def test_launch_rejects_unresolvable_data(api):
    client, headers, ctx, *_ = api
    body = dict(FULL, data=[{"path": "missing.csv", "description": "nope"}, {"path": "../etc/passwd", "description": ""}])
    bid = client.post("/onboarding/briefs", json=body, headers=headers).json()["id"]
    # The preview flags the same problems before the human hits Launch.
    p = client.get(f"/onboarding/briefs/{bid}/preview", headers=headers).json()
    assert len(p["data_problems"]) == 2
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
    assert r.status_code == 422, r.text
    probs = r.json()["detail"]["data"]
    assert any("missing.csv" in p and "not found" in p for p in probs)
    assert any("etc/passwd" in p for p in probs)
    # Nothing was created.
    assert list(ctx.sessions.iterdir()) == []


# ---------- the full flow ----------


def test_launch_freezes_brief_into_session_and_hands_it_to_the_supervisor(api):
    client, headers, ctx, server, spawned, gen = api
    _upload(client, headers, "growth.csv", b"t,od\n0,0.1\n")
    _upload(client, headers, "wt.tsv", b"x\ty\n", relpath="strains/wt.tsv")

    body = dict(FULL, data=[
        {"path": "growth.csv", "description": "OD600 growth curves"},
        {"path": "strains", "description": "per-strain folder"},
    ])
    bid = client.post("/onboarding/briefs", json=body, headers=headers).json()["id"]

    # Hypothesis search seeded from the brief (goal = research question + context).
    r = client.post(f"/onboarding/briefs/{bid}/hypotheses", json={"n": 3}, headers=headers)
    assert r.status_code == 200, r.text
    hyp = r.json()
    assert hyp["brief_id"] == bid and len(hyp["rounds"][0]["hypotheses"]) == 3
    assert gen.calls[-1]["goal"] == FULL["research_question"]
    assert "Prior work:" in gen.calls[-1]["context"] and "growth.csv" in gen.calls[-1]["context"]
    brief = client.get(f"/onboarding/briefs/{bid}", headers=headers).json()
    assert brief["hypothesis_session_id"] == hyp["id"] and brief["hypothesis"] is None

    # Selecting through the regular hypothesis route syncs into the brief.
    chosen = hyp["rounds"][0]["hypotheses"][1]
    r = client.post(f"/hypothesis/{hyp['id']}/select", json={"hypothesis_id": chosen["id"], "note": "most testable"}, headers=headers)
    assert r.status_code == 200, r.text
    brief = client.get(f"/onboarding/briefs/{bid}", headers=headers).json()
    assert brief["hypothesis"]["id"] == chosen["id"]
    assert brief["hypothesis"]["statement"] == chosen["statement"]
    assert brief["hypothesis"]["note"] == "most testable"

    # Preview is the exact opening message.
    preview = client.get(f"/onboarding/briefs/{bid}/preview", headers=headers).json()
    assert preview["missing"] == []
    assert chosen["statement"] in preview["text"]

    # Launch.
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
    assert r.status_code == 200, r.text
    out = r.json()
    sid = out["session_id"]
    assert out["task"] == FULL["title"] and out["brief_id"] == bid

    # 1. The runtime got the whole brief as its first turn (API-side composition,
    #    so frozen per-tenant runtimes need no change).
    assert len(spawned) == 1 and spawned[0]["sid"] == sid and spawned[0]["resume"] is None
    msg = spawned[0]["message"]
    assert msg == preview["text"]
    assert "## Research question" in msg and FULL["research_question"] in msg
    assert "`state/library/growth.csv`" in msg and "OD600 growth curves" in msg
    assert str(ctx.library / "growth.csv") in msg  # absolute path for py_exec
    assert "`state/library/strains/`" in msg and "folder, 1 file" in msg
    assert "**Statement:** " + chosen["statement"] in msg
    assert "Human's note on this choice:** most testable" in msg
    assert "1. Report (PDF)" in msg

    # 2. Frozen copy in the session dir, with sizes stamped.
    frozen = json.loads((ctx.session_dir(sid) / "brief.json").read_text())
    assert frozen["status"] == "launched" and frozen["session_id"] == sid
    sizes = {d["path"]: d.get("size") for d in frozen["data"]}
    assert sizes["growth.csv"] == len(b"t,od\n0,0.1\n")
    assert sizes["strains"] == len(b"x\ty\n")

    # 3. Events: short title for the list, full brief in session.brief.
    events = [json.loads(l) for l in (ctx.session_dir(sid) / "events.jsonl").read_text().splitlines() if l.strip()]
    kinds = [e["kind"] for e in events]
    assert kinds == ["research.requested", "session.brief"]
    assert events[0]["task"] == FULL["title"] and events[0]["brief_id"] == bid
    assert events[1]["hypothesis"] == chosen["statement"]
    assert events[1]["data"] == ["growth.csv", "strains"]
    assert events[1]["text"].startswith("# Problem brief:")

    # 4. Session listing + session brief endpoint.
    sessions = client.get("/sessions", headers=headers).json()
    me = next(s for s in sessions if s["session_id"] == sid)
    assert me["task"] == FULL["title"]
    assert me["has_brief"] is True and me["brief_title"] == FULL["title"]
    assert me["hypothesis"] == chosen["statement"]
    sb = client.get(f"/sessions/{sid}/brief", headers=headers).json()
    assert sb["id"] == bid and sb["text"].startswith("# Problem brief:")

    # 5. The draft is now frozen: no more edits, no double launch.
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["status"] == "launched"
    assert client.put(f"/onboarding/briefs/{bid}", json={"title": "x"}, headers=headers).status_code == 409
    assert client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers).status_code == 409
    assert client.post(f"/onboarding/briefs/{bid}/hypotheses", json={}, headers=headers).status_code == 409


def test_launch_without_hypothesis_and_autonomous_flag(api):
    client, headers, ctx, server, spawned, _gen = api
    bid = client.post("/onboarding/briefs", json=FULL, headers=headers).json()["id"]
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={"autonomous": True}, headers=headers)
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert (ctx.session_dir(sid) / "control" / "autonomous").exists()
    kinds = [json.loads(l)["kind"] for l in (ctx.session_dir(sid) / "events.jsonl").read_text().splitlines() if l.strip()]
    assert kinds == ["session.autonomous", "research.requested", "session.brief"]
    assert "No working hypothesis was selected" in spawned[0]["message"]
    me = next(s for s in client.get("/sessions", headers=headers).json() if s["session_id"] == sid)
    assert me["has_brief"] is True and me["hypothesis"] is None


def test_free_form_sessions_are_unaffected(api):
    client, headers, ctx, server, spawned, _gen = api
    r = client.post("/research/sessions", json={"task": "quick one"}, headers=headers)
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert spawned[0]["message"] == "quick one"
    me = next(s for s in client.get("/sessions", headers=headers).json() if s["session_id"] == sid)
    assert me["has_brief"] is False and me["brief_title"] is None
    assert client.get(f"/sessions/{sid}/brief", headers=headers).status_code == 404


def test_folder_attachment_renders_listing_and_instructions(api):
    client, headers, ctx, server, spawned, _gen = api
    for i in range(25):
        _upload(client, headers, f"s{i:02d}.tsv", b"x\ty\n", relpath=f"strains/s{i:02d}.tsv")
    _upload(client, headers, "big.xlsx", b"PK\x03\x04binary", relpath="strains/big.xlsx")
    bid = client.post("/onboarding/briefs", json=dict(FULL, data=[{"path": "strains", "description": "per-strain tables"}]), headers=headers).json()["id"]
    p = client.get(f"/onboarding/briefs/{bid}/preview", headers=headers).json()
    text = p["text"]
    assert "`state/library/strains/`" in text and "folder, 26 files" in text
    # Bounded, sorted inner listing: big.xlsx sorts first, then s00..s18 (20 entries).
    assert "    - `big.xlsx`" in text and "    - `s00.tsv`" in text and "    - `s18.tsv`" in text
    assert "    - `s19.tsv`" not in text
    assert "… 6 more (use `fs_read.list`" in text
    assert "folders are listed with `fs_read.list`" in text
    assert p["size_bytes"] == len(text.encode("utf-8")) and p["too_large"] is False
    # Binary + large-file warnings on plain files.
    bid2 = client.post("/onboarding/briefs", json=dict(FULL, data=[{"path": "strains/big.xlsx", "description": "spreadsheet"}]), headers=headers).json()["id"]
    text2 = client.get(f"/onboarding/briefs/{bid2}/preview", headers=headers).json()["text"]
    assert "⚠ binary format" in text2
    from api import onboarding

    rec = onboarding.new_record(dict(FULL, data=[{"path": "huge.csv", "description": ""}]))
    rec["data"][0]["size"] = onboarding.LARGE_FILE_BYTES + 1
    assert "⚠ large" in onboarding.render_brief(rec)


def test_launch_refuses_oversized_brief_without_touching_state(api):
    client, headers, ctx, server, spawned, _gen = api
    from api import onboarding

    # Each field stays under its own cap; together they exceed the message cap.
    half = onboarding.MAX_TEXT_CHARS - 10
    body = dict(FULL, background="x" * half, constraints="y" * half)
    assert 2 * half > onboarding.OPENING_MESSAGE_MAX_BYTES
    r = client.post("/onboarding/briefs", json=body, headers=headers)
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    p = client.get(f"/onboarding/briefs/{bid}/preview", headers=headers).json()
    assert p["too_large"] is True and p["size_bytes"] > p["max_bytes"]
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
    assert r.status_code == 422, r.text
    assert "too large" in r.json()["detail"]["detail"]
    assert spawned == [] and list(ctx.sessions.iterdir()) == []
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["status"] == "draft"


def test_spawn_failure_removes_half_created_session_and_keeps_draft(api, monkeypatch):
    client, headers, ctx, server, spawned, _gen = api

    async def boom(ctx_, sid, message, resume_uuid=None):
        raise OSError(7, "Argument list too long")

    monkeypatch.setattr(server, "_spawn_research", boom)
    bid = client.post("/onboarding/briefs", json=FULL, headers=headers).json()["id"]
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
    assert r.status_code == 500 and "could not start the research runtime" in r.json()["detail"]
    assert list(ctx.sessions.iterdir()) == []  # no orphan session
    assert client.get("/sessions", headers=headers).json() == []
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["status"] == "draft"


def test_corrupt_session_brief_does_not_break_session_listing(api):
    client, headers, ctx, server, _spawned, _gen = api
    bid = client.post("/onboarding/briefs", json=FULL, headers=headers).json()["id"]
    sid = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers).json()["session_id"]
    (ctx.session_dir(sid) / "brief.json").write_text("{not json", encoding="utf-8")
    r = client.get("/sessions", headers=headers)
    assert r.status_code == 200
    me = next(s for s in r.json() if s["session_id"] == sid)
    assert me["has_brief"] is False
    assert client.get(f"/sessions/{sid}/brief", headers=headers).status_code == 404


def test_bad_attachment_paths_are_problems_not_500s(api):
    client, headers, ctx, *_ = api
    body = dict(FULL, data=[{"path": "a\x00b.csv", "description": ""}, {"path": "x" * 300 + ".csv", "description": ""}])
    bid = client.post("/onboarding/briefs", json=body, headers=headers).json()["id"]
    p = client.get(f"/onboarding/briefs/{bid}/preview", headers=headers)
    assert p.status_code == 200, p.text
    assert len(p.json()["data_problems"]) == 2
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
    assert r.status_code == 422 and len(r.json()["detail"]["data"]) == 2


def test_attachments_are_deduped_and_capped(api):
    client, headers, ctx, *_ = api
    from api import onboarding

    body = dict(FULL, data=[{"path": "a.csv"}, {"path": "a.csv"}, {"path": "/a.csv/"}, {"path": "b.csv"}])
    rec = client.post("/onboarding/briefs", json=body, headers=headers).json()
    assert [d["path"] for d in rec["data"]] == ["a.csv", "b.csv"]
    too_many = [{"path": f"f{i}.csv"} for i in range(onboarding.MAX_DATA_ITEMS + 1)]
    r = client.put(f"/onboarding/briefs/{rec['id']}", json={"data": too_many}, headers=headers)
    assert r.status_code == 422 and "too many data attachments" in r.json()["detail"]
    r = client.put(f"/onboarding/briefs/{rec['id']}", json={"background": "x" * (onboarding.MAX_TEXT_CHARS + 1)}, headers=headers)
    assert r.status_code == 422 and "too long" in r.json()["detail"]
    # Folder listings skip dotfiles, like the library view does.
    _upload(client, headers, "ok.csv", b"1\n", relpath="d/ok.csv")
    (ctx.library / "d" / ".DS_Store").write_bytes(b"\x00")
    bid = client.post("/onboarding/briefs", json=dict(FULL, data=[{"path": "d"}]), headers=headers).json()["id"]
    text = client.get(f"/onboarding/briefs/{bid}/preview", headers=headers).json()["text"]
    assert "folder, 1 file" in text and ".DS_Store" not in text


def test_concurrent_launch_of_one_brief_is_refused(api):
    client, headers, ctx, server, spawned, _gen = api
    bid = client.post("/onboarding/briefs", json=FULL, headers=headers).json()["id"]
    server._LAUNCHING.add((ctx.user.user_id, bid))  # simulate a launch in flight
    try:
        r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
        assert r.status_code == 409 and spawned == []
    finally:
        server._LAUNCHING.discard((ctx.user.user_id, bid))
    assert client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers).status_code == 200
    assert len(spawned) == 1
    assert (ctx.user.user_id, bid) not in server._LAUNCHING


def test_refine_keeps_brief_context(api):
    client, headers, ctx, server, _spawned, gen = api
    bid = client.post("/onboarding/briefs", json=FULL, headers=headers).json()["id"]
    h = client.post(f"/onboarding/briefs/{bid}/hypotheses", json={}, headers=headers).json()
    assert "Prior work:" in h["context"]
    r = client.post(f"/hypothesis/{h['id']}/refine", json={"parent_id": h["rounds"][0]["hypotheses"][0]["id"], "feedback": "sharper"}, headers=headers)
    assert r.status_code == 200, r.text
    assert gen.calls[-1]["parent"] is not None and "Prior work:" in (gen.calls[-1]["context"] or "")
    # Free-form searches carry no context.
    free = client.post("/hypothesis/sessions", json={"goal": "why?"}, headers=headers).json()
    client.post(f"/hypothesis/{free['id']}/refine", json={"parent_id": free["rounds"][0]["hypotheses"][0]["id"]}, headers=headers)
    assert gen.calls[-1]["context"] is None


def test_deleting_a_launched_session_returns_its_brief_to_draft(api):
    client, headers, ctx, server, _spawned, _gen = api
    bid = client.post("/onboarding/briefs", json=FULL, headers=headers).json()["id"]
    sid = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers).json()["session_id"]
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["status"] == "launched"
    assert client.delete(f"/sessions/{sid}", headers=headers).status_code == 200
    b = client.get(f"/onboarding/briefs/{bid}", headers=headers).json()
    assert b["status"] == "draft" and b["session_id"] is None
    assert [d["id"] for d in client.get("/onboarding/briefs", headers=headers).json() if d["status"] == "draft"] == [bid]
    # …and it can be edited and launched again.
    assert client.put(f"/onboarding/briefs/{bid}", json={"title": "second try"}, headers=headers).status_code == 200
    assert client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers).status_code == 200


def test_reseeding_hypotheses_resets_selection_and_ignores_stale_search(api):
    client, headers, ctx, server, _spawned, _gen = api
    bid = client.post("/onboarding/briefs", json=FULL, headers=headers).json()["id"]
    h1 = client.post(f"/onboarding/briefs/{bid}/hypotheses", json={}, headers=headers).json()
    c1 = h1["rounds"][0]["hypotheses"][0]["id"]
    client.post(f"/hypothesis/{h1['id']}/select", json={"hypothesis_id": c1}, headers=headers)
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["hypothesis"]["id"] == c1

    h2 = client.post(f"/onboarding/briefs/{bid}/hypotheses", json={}, headers=headers).json()
    brief = client.get(f"/onboarding/briefs/{bid}", headers=headers).json()
    assert brief["hypothesis_session_id"] == h2["id"] and brief["hypothesis"] is None
    # A late select on the superseded search must not overwrite the brief.
    client.post(f"/hypothesis/{h1['id']}/select", json={"hypothesis_id": c1}, headers=headers)
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["hypothesis"] is None
    # Refine on the live search, then select the refined card → synced.
    r = client.post(f"/hypothesis/{h2['id']}/refine", json={"parent_id": h2["rounds"][0]["hypotheses"][0]["id"], "feedback": "sharper"}, headers=headers)
    assert r.status_code == 200, r.text
    c2 = r.json()["rounds"][1]["hypotheses"][0]["id"]
    client.post(f"/hypothesis/{h2['id']}/select", json={"hypothesis_id": c2}, headers=headers)
    assert client.get(f"/onboarding/briefs/{bid}", headers=headers).json()["hypothesis"]["id"] == c2
