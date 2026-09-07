"""O2 project tree: registration, visibility, deterministic adjacency,
edge decisions, spawn-brief, launch linking, tree context in the brief,
library download, and O1-era briefs still loading. Offline."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import ADJACENT, COMPLETE, UNRELATED, create_brief, launch, mark_advised, register

ROOT = Path(__file__).resolve().parents[1]


def test_registration_requires_questions_1_to_3(platform):
    c, a = platform.client, platform.a
    bid = create_brief(c, a.headers, {"title": "T", "research_question": "Q?"})["id"]
    r = c.post(f"/onboarding/briefs/{bid}/register", json={}, headers=a.headers)
    assert r.status_code == 422
    assert r.json()["detail"]["missing"] == ["significance", "prior_work", "open_gap", "evaluation_protocol"]
    s = c.get("/onboarding/briefs", headers=a.headers).json()[0]
    assert s["registrable"] is False
    assert [q["filled"] for q in s["completeness"]] == [True, False, False, False, False]
    # Launch still works with just title + question (O1 rule) — no node is created.
    out = launch(c, a.headers, bid)
    assert out["node_id"] is None
    assert c.get("/projects/tree", headers=a.headers).json()["nodes"] == []


def test_register_creates_node_visible_org_wide_but_private_stays_owner_only(platform):
    c, a, b = platform.client, platform.a, platform.b
    bid = create_brief(c, a.headers, COMPLETE)["id"]
    out = register(c, a.headers, bid)
    node = out["node"]
    assert node["owner"]["user_id"] == a.uid and node["visibility"] == "org" and node["status"] == "registered"
    assert out["changed"] is True and out["advisor_started"] is True
    assert platform.advisor_calls[-1]["pid"] == node["id"] and platform.advisor_calls[-1]["trigger"] == "register"
    assert c.get(f"/onboarding/briefs/{bid}", headers=a.headers).json()["node_id"] == node["id"]
    # Bob sees the statement, owner name and status — nothing tenant-private.
    tree_b = c.get("/projects/tree", headers=b.headers).json()
    assert [n["id"] for n in tree_b["nodes"]] == [node["id"]]
    view_b = c.get(f"/projects/{node['id']}", headers=b.headers).json()
    assert view_b["statement"]["significance"] == COMPLETE["significance"]
    assert view_b["is_owner"] is False and "recommendation" not in view_b and "source" not in view_b
    # A private node is invisible to Bob (404, not 403 — no existence leak).
    pbid = create_brief(c, a.headers, dict(COMPLETE, title="Private variant", visibility="private"))["id"]
    pnode = register(c, a.headers, pbid)["node"]
    assert pnode["visibility"] == "private"
    assert c.get(f"/projects/{pnode['id']}", headers=b.headers).status_code == 404
    assert len(c.get("/projects/tree", headers=b.headers).json()["nodes"]) == 1
    assert len(c.get("/projects/tree", headers=a.headers).json()["nodes"]) == 2
    # Re-registering an unchanged statement does not re-run the advisor (once
    # a record exists; with none it retries, so the tree never stays unadvised).
    mark_advised(platform.mods, node["id"])
    n_calls = len(platform.advisor_calls)
    out2 = register(c, a.headers, bid)
    assert out2["changed"] is False and out2["node"]["id"] == node["id"]
    assert len(platform.advisor_calls) == n_calls
    # Editing the statement + re-registering makes a new revision and re-runs it.
    c.put(f"/onboarding/briefs/{bid}", json={"open_gap": "Now a sharper gap."}, headers=a.headers)
    out3 = register(c, a.headers, bid)
    assert out3["changed"] is True and out3["node"]["revisions"] == 2
    assert len(platform.advisor_calls) == n_calls + 1


def test_bm25_adjacency_links_overlapping_statements_and_ignores_unrelated_ones(platform):
    c, a, b = platform.client, platform.a, platform.b
    na = register(c, a.headers, create_brief(c, a.headers, COMPLETE)["id"])["node"]
    nu = register(c, a.headers, create_brief(c, a.headers, UNRELATED)["id"])["node"]
    nb = register(c, b.headers, create_brief(c, b.headers, ADJACENT)["id"])["node"]
    near = c.get(f"/projects/{nb['id']}/near", headers=b.headers).json()
    ids = [x["node"]["id"] for x in near["neighbours"]]
    assert na["id"] in ids and nu["id"] not in ids
    e = next(x["edge"] for x in near["neighbours"] if x["node"]["id"] == na["id"])
    assert e["type"] == "adjacent" and e["source"] == "keyword" and e["status"] == "proposed" and 0.12 <= e["weight"] <= 1.0
    assert e["rationale"].startswith("Shared terms:") and "decoupling" in e["rationale"]
    # The edge is symmetric: Alice's near-tree lists Bob's problem with the same edge.
    near_a = c.get(f"/projects/{na['id']}/near", headers=a.headers).json()
    assert any(x["edge"]["id"] == e["id"] for x in near_a["neighbours"])
    # Either owner can confirm; the keyword pass never overwrites a decided edge.
    r = c.post(f"/projects/{na['id']}/edges/{e['id']}/confirm", headers=a.headers)
    assert r.status_code == 200
    assert any(x["status"] == "confirmed" for x in r.json()["edges"] if x["id"] == e["id"])
    register(c, b.headers, c.get("/onboarding/briefs", headers=b.headers).json()[0]["id"])
    still = [x for x in c.get(f"/projects/{nb['id']}", headers=b.headers).json()["edges"] if x["id"] == e["id"]]
    assert still and still[0]["status"] == "confirmed"
    # A third party cannot decide it.
    _u, k = platform.mods.auth.create_user("carol")
    assert c.post(f"/projects/{na['id']}/edges/{e['id']}/reject", headers={"Authorization": f"Bearer {k}"}).status_code == 403
    # Rejecting hides the edge from both tree views.
    assert c.post(f"/projects/{nb['id']}/edges/{e['id']}/reject", headers=b.headers).status_code == 200
    assert e["id"] not in [x["id"] for x in c.get("/projects/tree", headers=a.headers).json()["edges"]]


def test_shared_dataset_edge_from_identical_attached_content(platform):
    c, a, b = platform.client, platform.a, platform.b
    payload = b"x,y\n1,2\n3,4\n" * 50
    for who, name in ((a, "spectra_q1.csv"), (b, "copy_of_spectra.csv")):
        r = c.post("/library/files", files={"file": (name, payload)}, headers=who.headers)
        assert r.status_code == 200
    na = register(c, a.headers, create_brief(c, a.headers, dict(UNRELATED, data=[{"path": "spectra_q1.csv", "description": "raw"}]))["id"])["node"]
    nb = register(c, b.headers, create_brief(c, b.headers, dict(COMPLETE, data=[{"path": "copy_of_spectra.csv", "description": "same file"}]))["id"])["node"]
    edges = c.get(f"/projects/{nb['id']}", headers=b.headers).json()["edges"]
    ds = [e for e in edges if e["type"] == "shares_dataset"]
    assert len(ds) == 1 and {ds[0]["src"], ds[0]["dst"]} == {na["id"], nb["id"]}
    assert not [e for e in edges if e["type"] == "adjacent"]  # statements are unrelated
    # Names never leak across tenants: Bob's view shows dataset counts only.
    view = c.get(f"/projects/{na['id']}", headers=b.headers).json()
    assert view["dataset_count"] == 1 and view["links"]["datasets"][0]["path"] == "spectra_q1.csv"


def test_launch_links_session_and_brief_carries_tree_context_and_harness(platform):
    c, a, b = platform.client, platform.a, platform.b
    na = register(c, a.headers, create_brief(c, a.headers, ADJACENT)["id"])["node"]
    bid = create_brief(c, b.headers, COMPLETE)["id"]
    nb = register(c, b.headers, bid)["node"]
    prev = c.get(f"/onboarding/briefs/{bid}/preview", headers=b.headers).json()
    assert prev["missing_for_registration"] == [] and prev["pending_imports"] == []
    assert "## Project tree context" in prev["text"]
    assert f"`{nb['id']}`" in prev["text"] and ADJACENT["title"] in prev["text"] and "alice" in prev["text"]
    assert "**Active version:** bootstrap harness" in prev["text"]
    out = launch(c, b.headers, bid)
    assert out["node_id"] == nb["id"]
    sid = out["session_id"]
    msg = platform.spawned[-1]["message"]
    assert msg == prev["text"]
    frozen = json.loads((b.ctx.session_dir(sid) / "brief.json").read_text())
    assert frozen["node_id"] == nb["id"] and frozen["harness"]["tools"] and frozen["tree_context"].startswith("**Registered as project node**")
    ev = [json.loads(l) for l in (b.ctx.session_dir(sid) / "events.jsonl").read_text().splitlines()]
    brief_ev = next(e for e in ev if e["kind"] == "session.brief")
    assert brief_ev["node_id"] == nb["id"] and brief_ev["harness"]["version_id"] is None and "tree_context" in brief_ev
    node = c.get(f"/projects/{nb['id']}", headers=b.headers).json()
    assert node["status"] == "active" and node["links"]["sessions"] == [sid] and node["session_count"] == 1
    # Alice sees the session id + (later) an outcome one-liner, never the events.
    assert c.get(f"/projects/{nb['id']}", headers=a.headers).json()["links"]["sessions"] == [sid]
    # A complete but never-registered brief is registered at launch, with its visibility.
    bid2 = create_brief(c, a.headers, dict(UNRELATED, visibility="private"))["id"]
    out2 = launch(c, a.headers, bid2)
    assert out2["node_id"] and c.get(f"/projects/{out2['node_id']}", headers=b.headers).status_code == 404
    assert c.get(f"/projects/{out2['node_id']}", headers=a.headers).json()["status"] == "active"
    # GET /sessions/{sid}/brief renders the same frozen text.
    assert c.get(f"/sessions/{sid}/brief", headers=b.headers).json()["text"] in msg


def test_launch_refreshes_harness_links_so_a_later_version_is_discoverable(platform):
    from tests.conftest import seed_version

    c, a, b = platform.client, platform.a, platform.b
    bid = create_brief(c, a.headers, COMPLETE)["id"]
    na = register(c, a.headers, bid)["node"]
    assert na["version_count"] == 0
    ver = seed_version(platform.mods, a.ctx.root, a.uid)  # evolved AFTER registering
    assert platform.mods.projects.version_discoverable(a.uid, ver["id"]) is False
    launch(c, a.headers, bid)
    view = c.get(f"/projects/{na['id']}", headers=b.headers).json()
    assert [v["id"] for v in view["links"]["harness_versions"]] == [ver["id"]] and "fancy_tool" in view["custom_tools"]
    assert platform.mods.projects.version_discoverable(a.uid, ver["id"]) is True


def test_session_outcome_sync_writes_outcome_and_results_on_the_node(platform):
    c, b = platform.client, platform.b
    bid = create_brief(c, b.headers, COMPLETE)["id"]
    nb = register(c, b.headers, bid)["node"]
    sid = launch(c, b.headers, bid)["session_id"]
    sd = b.ctx.session_dir(sid)
    (sd / "results").mkdir(exist_ok=True)
    (sd / "results" / "report.pdf").write_bytes(b"%PDF")
    with open(sd / "events.jsonl", "a") as f:
        f.write(json.dumps({"id": "e1", "ts": "2026-09-06 10:00:00", "session": sid, "actor": "supervisor", "kind": "research.complete", "summary": "T2 improved by 27% on Q1 and Q3; CPMG baseline reproduced."}) + "\n")
    out = platform.mods.projects.sync_session(b.ctx, sid)
    assert out["results"] == ["report.pdf"] and out["summary"].startswith("T2 improved")
    view = c.get(f"/projects/{nb['id']}", headers=platform.a.headers).json()
    assert view["outcome"].startswith("T2 improved") and view["links"]["outcomes"][0]["results"] == ["report.pdf"]


def test_spawn_brief_sets_parent_and_prefills_prior_work(platform):
    c, a, b = platform.client, platform.a, platform.b
    na = register(c, a.headers, create_brief(c, a.headers, COMPLETE)["id"])["node"]
    r = c.post(f"/projects/{na['id']}/spawn-brief", headers=b.headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["parent_node"] == na["id"] and rec["domain"] == COMPLETE["domain"] and rec["keywords"] == COMPLETE["keywords"]
    assert COMPLETE["title"] in rec["prior_work"] and rec["title"] == ""
    # Registering the child writes a confirmed subproblem edge to the parent.
    c.put(f"/onboarding/briefs/{rec['id']}", json={"title": "Sub: two-tone on Q3 only", "research_question": "Does it hold on Q3?", "significance": "s", "open_gap": "g", "evaluation_protocol": "p"}, headers=b.headers)
    nb = register(c, b.headers, rec["id"])["node"]
    assert nb["parent_node"] == na["id"]
    sub = [e for e in nb["edges"] if e["type"] == "subproblem"]
    assert len(sub) == 1 and sub[0]["status"] == "confirmed" and sub[0]["src"] == nb["id"] and sub[0]["dst"] == na["id"]
    assert "**Derives from:**" in c.get(f"/onboarding/briefs/{rec['id']}/preview", headers=b.headers).json()["text"]
    # An unknown parent is rejected on update.
    assert c.put(f"/onboarding/briefs/{rec['id']}", json={"parent_node": "p-20260101-ffffff"}, headers=b.headers).status_code == 422


def test_patch_node_and_declare_relation(platform):
    c, a, b = platform.client, platform.a, platform.b
    na = register(c, a.headers, create_brief(c, a.headers, COMPLETE)["id"])["node"]
    nb = register(c, b.headers, create_brief(c, b.headers, UNRELATED)["id"])["node"]
    assert c.patch(f"/projects/{na['id']}", json={"status": "solved"}, headers=b.headers).status_code == 403
    assert c.patch(f"/projects/{na['id']}", json={"status": "bogus"}, headers=a.headers).status_code == 422
    v = c.patch(f"/projects/{na['id']}", json={"status": "solved", "keywords": ["transmon", "Transmon", " qubit "]}, headers=a.headers).json()
    assert v["status"] == "solved" and v["keywords"] == ["transmon", "qubit"]
    r = c.post(f"/projects/{nb['id']}/edges", json={"dst": na["id"], "type": "shares_method", "rationale": "same fitting pipeline"}, headers=b.headers)
    assert r.status_code == 200
    e = next(x for x in r.json()["edges"] if x["type"] == "shares_method")
    assert e["source"] == "human" and e["status"] == "confirmed" and e["rationale"] == "same fitting pipeline"
    assert c.post(f"/projects/{nb['id']}/edges", json={"dst": nb["id"]}, headers=b.headers).status_code == 422
    assert c.get("/projects/p-notanid", headers=b.headers).status_code == 400


def test_deleting_a_never_launched_draft_withdraws_its_node(platform):
    c, a, b = platform.client, platform.a, platform.b
    bid = create_brief(c, a.headers, COMPLETE)["id"]
    na = register(c, a.headers, bid)["node"]
    assert c.get(f"/projects/{na['id']}", headers=b.headers).status_code == 200
    assert c.delete(f"/onboarding/briefs/{bid}", headers=a.headers).status_code == 200
    assert c.get(f"/projects/{na['id']}", headers=b.headers).status_code == 404
    assert c.get(f"/projects/{na['id']}", headers=a.headers).json()["status"] == "abandoned"


def test_o1_era_brief_loads_renders_and_launches(platform):
    c, a = platform.client, platform.a
    src = json.loads((ROOT / "tests" / "fixtures" / "onboarding_brief_v1.json").read_text())
    (a.ctx.state / "onboarding").mkdir(parents=True, exist_ok=True)
    (a.ctx.state / "onboarding" / f"{src['id']}.json").write_text(json.dumps(src))
    (a.ctx.library / "growth.csv").write_text("t,od\n0,0.1\n")
    rec = c.get(f"/onboarding/briefs/{src['id']}", headers=a.headers).json()
    assert rec["prior_work"] == src["background"] and rec["schema_version"] == 2 and rec["visibility"] == "org"
    assert rec["keywords"] == [] and rec["node_id"] is None and rec["harness"] is None
    s = c.get("/onboarding/briefs", headers=a.headers).json()[0]
    assert s["registrable"] is False and s["completeness"][2]["missing"] == ["open_gap"]
    prev = c.get(f"/onboarding/briefs/{src['id']}/preview", headers=a.headers).json()
    assert "**What existing work has achieved:** " + src["background"] in prev["text"]
    assert prev["missing"] == [] and prev["missing_for_registration"] == ["significance", "open_gap", "evaluation_protocol"]
    out = launch(c, a.headers, src["id"])
    assert out["node_id"] is None and platform.spawned[-1]["message"] == prev["text"]


def test_library_download_is_tenant_scoped_and_traversal_guarded(platform):
    c, a, b = platform.client, platform.a, platform.b
    r = c.post("/library/files", files={"file": ("data.csv", b"a,b\n1,2\n")}, headers=a.headers)
    assert r.status_code == 200
    r = c.post("/library/files", files={"file": ("nested.csv", b"z\n")}, data={"relpath": "run1/nested.csv"}, headers=a.headers)
    assert r.status_code == 200
    r = c.get("/library/files/download", params={"name": "data.csv"}, headers=a.headers)
    assert r.status_code == 200 and r.content == b"a,b\n1,2\n"
    assert r.headers["content-disposition"].endswith('filename="data.csv"')
    assert c.get("/library/files/download", params={"name": "run1/nested.csv"}, headers=a.headers).content == b"z\n"
    assert c.get("/library/files/download", params={"name": "data.csv"}, headers=b.headers).status_code == 404
    assert c.get("/library/files/download", params={"name": "../auth/users.db"}, headers=a.headers).status_code == 400
    assert c.get("/library/files/download", params={"name": "run1"}, headers=a.headers).status_code == 404
    assert c.get("/library/files/download", params={"name": ".staging/x"}, headers=a.headers).status_code == 400


def test_private_nodes_never_leak_through_edges_or_parent_links(platform):
    c, a, b = platform.client, platform.a, platform.b
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    # Alice registers a PRIVATE statement that overlaps Bob's: the keyword pass
    # links them (so Alice's own near-tree works)…
    pa = register(c, a.headers, create_brief(c, a.headers, dict(ADJACENT, visibility="private"))["id"])["node"]
    assert any(x["node"]["id"] == nb["id"] for x in c.get(f"/projects/{pa['id']}/near", headers=a.headers).json()["neighbours"])
    # …but Bob must not see the edge, its id, or its rationale anywhere.
    for path in (f"/projects/{nb['id']}", f"/projects/{nb['id']}/near"):
        body = c.get(path, headers=b.headers).json()
        assert pa["id"] not in json.dumps(body), path
    assert pa["id"] not in json.dumps(c.get("/projects/tree", headers=b.headers).json())
    # Bob cannot hang a brief under Alice's private node (same 422 as an unknown id).
    r = c.put(f"/onboarding/briefs/{create_brief(c, b.headers, {'title': 'x'})['id']}", json={"parent_node": pa["id"]}, headers=b.headers)
    assert r.status_code == 422
    assert c.post("/onboarding/briefs", json={"title": "y", "parent_node": pa["id"]}, headers=b.headers).status_code == 422
    # A private node's title never lands in a colleague's brief text.
    assert pa["id"] not in c.get(f"/onboarding/briefs/{create_brief(c, b.headers, COMPLETE)['id']}/preview", headers=b.headers).json()["text"]


def test_only_versions_a_problem_ran_on_are_discoverable(platform):
    from tests.conftest import seed_version

    c, a, b = platform.client, platform.a, platform.b
    v1 = seed_version(platform.mods, a.ctx.root, a.uid, tool="private_tool", summary="private experiment")
    v2 = seed_version(platform.mods, a.ctx.root, a.uid, tool="fancy_tool", summary="Add fancy_tool")
    bid = create_brief(c, a.headers, COMPLETE)["id"]
    na = register(c, a.headers, bid)["node"]
    launch(c, a.headers, bid)  # runs on v2 (the active tip)
    proj = platform.mods.projects
    assert proj.version_discoverable(a.uid, v2["id"]) is True
    assert proj.version_discoverable(a.uid, v1["id"]) is False  # never ran a shared problem
    view_b = c.get(f"/projects/{na['id']}", headers=b.headers).json()
    assert [v["id"] for v in view_b["links"]["harness_versions"]] == [v2["id"]]
    assert "harness_inventory" not in view_b
    view_a = c.get(f"/projects/{na['id']}", headers=a.headers).json()
    assert {v["id"] for v in view_a["harness_inventory"]} == {v1["id"], v2["id"]}
    cat = proj.importable_catalogue(b.uid)
    assert [v["version_id"] for v in cat["versions"]] == [v2["id"]]
    assert c.post(f"/projects/{register(c, b.headers, create_brief(c, b.headers, ADJACENT)['id'])['node']['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": v1["id"]}, headers=b.headers).status_code == 403
