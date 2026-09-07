"""O2 harness/tool imports between two tenants, on real git repos: fetch,
pre-gate smoke in a worktree, the HITL request in evo-import-*, approve →
version node + detached HEAD (adopt) / child version (tool), reject prunes,
guards (self, unknown, not shared, busy, pending). Offline (no model)."""

from __future__ import annotations

import hashlib
import json
import subprocess

from tests.conftest import ADJACENT, COMPLETE, create_brief, git, launch, register, seed_version


def _donor_and_importer(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver = seed_version(platform.mods, a.ctx.root, a.uid)
    abid = create_brief(c, a.headers, ADJACENT)["id"]
    na = register(c, a.headers, abid)["node"]
    launch(c, a.headers, abid)
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    return ver, na, nb


def _state_digest(ctx) -> str:
    """Durable tenant data an import must never touch: research sessions,
    library, memory, drafts. (The archive manifest, the import's own
    evo-import-* session and control markers are written by design.)"""
    h = hashlib.sha256()
    for sub in ("sessions", "library", "memory", "onboarding"):
        for p in sorted((ctx.state / sub).rglob("*")):
            if p.is_file() and not any(part.startswith("evo-import-") for part in p.parts):
                h.update(p.relative_to(ctx.state).as_posix().encode())
                h.update(p.read_bytes())
    return h.hexdigest()


def test_add_tool_to_role_yaml_is_textual_and_idempotent():
    from api.imports import add_tool_to_role_yaml

    text = "name: x\nmodel: m\ntools:\n  - bus\n  - fs_read\n\ncan_spawn: false\n"
    out, changed = add_tool_to_role_yaml(text, "fancy_tool")
    assert changed and out == "name: x\nmodel: m\ntools:\n  - bus\n  - fs_read\n  - fancy_tool\n\ncan_spawn: false\n"
    assert add_tool_to_role_yaml(out, "fancy_tool") == (out, False)
    assert add_tool_to_role_yaml("name: y\n", "t") == ("name: y\ntools:\n  - t\n", True)
    assert add_tool_to_role_yaml("name: z\ntools: [bus, memory]\nx: 1\n", "t") == ("name: z\ntools: [bus, memory, t]\nx: 1\n", True)
    assert add_tool_to_role_yaml("tools: [bus, t]\n", "t") == ("tools: [bus, t]\n", False)


def test_harness_adopt_end_to_end(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    before = _state_digest(b.ctx)
    boot_sha = git(b.ctx.root, "rev-parse", "HEAD").strip()
    (b.ctx.root / "state" / "sessions").mkdir(parents=True, exist_ok=True)

    r = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"], "mode": "adopt"}, headers=b.headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "pending" and rec["mode"] == "adopt" and rec["sha"] == ver["sha"] and rec["from_node"] == na["id"]
    assert rec["smoke"]["ran"] is True and rec["smoke"]["ok"] is True and rec["smoke"]["tests"]
    assert rec["rollback_to"] == {"sha": boot_sha, "version_id": None}
    assert "tools/fancy_tool/server.py" in rec["diffstat"]
    assert not (b.ctx.root / "worktrees" / f"import-{rec['id']}").exists()  # pre-gate worktree cleaned
    # The approval sits in the existing HITL protocol, in a dedicated session.
    sid = rec["session_id"]
    assert sid == f"evo-import-{rec['id']}"
    pend = c.get(f"/hitl/{sid}/pending", headers=b.headers).json()
    assert len(pend) == 1 and pend[0]["kind"] == "harness_import" and pend[0]["id"] == rec["request_id"]
    pl = pend[0]["payload"]
    assert pl["smoke"]["ok"] is True and pl["owner_name"] == "alice" and pl["what_happens"].startswith("Your active harness becomes")
    assert isinstance(pl["risks"], list) and pl["diff_preview"]
    sessions = {s["session_id"]: s for s in c.get("/sessions", headers=b.headers).json()}
    assert sid in sessions and sessions[sid]["last_kind"] == "hitl.pending"
    # A second import while one is pending is refused.
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).status_code == 409
    # The preview warns about the pending approval.
    prev = c.get(f"/onboarding/briefs/{c.get('/onboarding/briefs', headers=b.headers).json()[0]['id']}/preview", headers=b.headers).json()
    assert prev["pending_imports"] == [rec["id"]]

    r = c.post(f"/hitl/{sid}/{rec['request_id']}/answer", json={"decision": "approve"}, headers=b.headers)
    assert r.status_code == 200, r.text
    imp = r.json()["import"]
    assert imp["status"] == "applied", imp
    new_vid = imp["result"]["version_id"]
    vl = c.get("/versions", headers=b.headers).json()
    assert vl["active"] == new_vid and vl["head"] == ver["sha"]
    node = next(v for v in vl["versions"] if v["id"] == new_vid)
    assert node["status"] == "imported" and node["imported_from"]["owner"] == a.uid and node["imported_from"]["version_id"] == ver["id"]
    assert node["rollback_to"]["sha"] == boot_sha
    # The donor commit is now a tagged version of the importer's own repo; the
    # tool is in the tree; state untouched. (Two roots in production — in tests
    # both tenants bootstrap from an identical tree in the same second, so the
    # root commits coincide.)
    assert git(b.ctx.root, "rev-list", "-n", "1", f"ver/{new_vid}").strip() == ver["sha"]
    assert (b.ctx.root / "tools" / "fancy_tool" / "server.py").exists()
    assert "fancy_tool" in [t for r_ in c.get("/roles", headers=b.headers).json() for t in r_["tools"]]
    assert _state_digest(b.ctx) == before
    assert (b.ctx.root / "state" / "archive" / "evolutions" / new_vid / "diff.patch").read_text()
    # Provenance on both nodes + a confirmed shares_method edge; the GET record.
    vb = c.get(f"/projects/{nb['id']}", headers=b.headers).json()
    assert vb["active_version"]["imported_from"]["owner"] == a.uid and "fancy_tool" in vb["custom_tools"]
    assert any(e["type"] == "shares_method" and e["source"] == "import" and e["status"] == "confirmed" for e in vb["edges"])
    assert c.get(f"/projects/imports/{rec['id']}", headers=b.headers).json()["status"] == "applied"
    assert c.get(f"/projects/imports/{rec['id']}", headers=a.headers).status_code == 404
    ev = c.get(f"/sessions/{sid}/events", headers=b.headers).json()
    assert [e["kind"] for e in ev] == ["import.requested", "hitl.pending", "hitl.answer", "import.approved", "import.applied"]
    # Rollback = the R17 switch to the previous node (the bootstrap root sha).
    r = c.post(f"/versions/{boot_sha}/activate", headers=b.headers)
    assert r.status_code == 200 and r.json()["head"] == boot_sha
    assert not (b.ctx.root / "tools" / "fancy_tool").exists()
    # A brief launched now records the imported harness once it is active again.
    c.post(f"/versions/{new_vid}/activate", headers=b.headers)
    bid = create_brief(c, b.headers, dict(COMPLETE, title="Follow-up on the imported harness"))["id"]
    prev = c.get(f"/onboarding/briefs/{bid}/preview", headers=b.headers).json()
    assert "**Imported from:** alice's version" in prev["text"] and "`fancy_tool`" in prev["text"]


def test_reject_prunes_ref_and_guards(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    # self-import, unknown version, bad owner id
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": b.uid, "version_id": ver["id"]}, headers=b.headers).status_code == 400
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": "nope"}, headers=b.headers).status_code == 403
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": "bogus", "version_id": ver["id"]}, headers=b.headers).status_code == 400
    assert c.post(f"/projects/{na['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).status_code == 403
    rec = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).json()
    assert rec["status"] == "pending" and rec["mode"] == "adopt"
    assert git(b.ctx.root, "show-ref", f"refs/imports/{a.uid}/{ver['id']}").strip()
    r = c.post(f"/hitl/{rec['session_id']}/{rec['request_id']}/answer", json={"decision": "reject", "note": "not yet"}, headers=b.headers)
    assert r.status_code == 200 and r.json()["import"]["status"] == "rejected"
    assert subprocess.run(["git", "show-ref", f"refs/imports/{a.uid}/{ver['id']}"], cwd=str(b.ctx.root), capture_output=True).returncode != 0
    assert c.get("/versions", headers=b.headers).json()["versions"] == []
    # Busy tenant → 409 (a research turn is running).
    platform.server._RUNNING[(b.uid, "20260906-000000-aaaaaa")] = object()
    try:
        assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).status_code == 409
    finally:
        platform.server._RUNNING.pop((b.uid, "20260906-000000-aaaaaa"))


def test_not_discoverable_until_linked_or_shared(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver = seed_version(platform.mods, a.ctx.root, a.uid)
    # Alice's problem is private → the version is not importable.
    abid = create_brief(c, a.headers, dict(ADJACENT, visibility="private"))["id"]
    register(c, a.headers, abid)
    launch(c, a.headers, abid)
    nb = register(c, b.headers, create_brief(c, b.headers, COMPLETE)["id"])["node"]
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).status_code == 403
    # Explicit share makes it importable even though the problem stays private.
    r = c.post(f"/versions/{ver['id']}/share", json={"shared": True}, headers=a.headers)
    assert r.status_code == 200 and next(v for v in r.json()["versions"] if v["id"] == ver["id"])["shared"] is True
    r = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert c.post("/versions/nope/share", json={}, headers=a.headers).status_code == 404


def test_tool_import_end_to_end_and_child_version(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    boot = git(b.ctx.root, "rev-parse", "HEAD").strip()
    r = c.post(f"/projects/{nb['id']}/imports", json={"kind": "tool", "owner": a.uid, "version_id": ver["id"], "tools": ["fancy_tool"], "roles": ["data_analyst", "generalist_researcher"]}, headers=b.headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "pending" and rec["kind"] == "tool" and rec["smoke"]["ok"] is True
    files = [l.split("|")[0].strip() for l in rec["diffstat"].splitlines() if "|" in l]
    assert set(files) == {"tools/fancy_tool/__init__.py", "tools/fancy_tool/server.py", "roles/subagents/data_analyst.yaml", "roles/subagents/generalist_researcher.yaml"}
    pend = c.get(f"/hitl/{rec['session_id']}/pending", headers=b.headers).json()
    assert pend[0]["kind"] == "tool_import" and pend[0]["payload"]["tools"] == ["fancy_tool"]
    assert (b.ctx.root / "worktrees" / f"import-{rec['id']}").is_dir()  # kept until the decision
    r = c.post(f"/hitl/{rec['session_id']}/{rec['request_id']}/answer", json={"decision": "approve"}, headers=b.headers)
    assert r.status_code == 200 and r.json()["import"]["status"] == "applied", r.text
    assert not (b.ctx.root / "worktrees" / f"import-{rec['id']}").exists()
    vl = c.get("/versions", headers=b.headers).json()
    assert len(vl["versions"]) == 1
    v = vl["versions"][0]
    assert v["active"] and v["status"] == "merged" and v["base_sha"] == boot and v["imported_from"]["tools"] == ["fancy_tool"]
    assert len(git(b.ctx.root, "rev-list", "--max-parents=0", "--all").split()) == 1  # a child, not a second root
    roles = {r_["name"]: r_["tools"] for r_ in c.get("/roles", headers=b.headers).json()}
    assert "fancy_tool" in roles["data_analyst"] and "fancy_tool" in roles["generalist_researcher"] and "fancy_tool" not in roles["supervisor"]
    # Duplicate tool / unknown role / unknown tool
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "tool", "owner": a.uid, "version_id": ver["id"], "tools": ["fancy_tool"], "roles": ["data_analyst"]}, headers=b.headers).status_code == 409
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "tool", "owner": a.uid, "version_id": ver["id"], "tools": ["ghost"], "roles": ["data_analyst"]}, headers=b.headers).status_code == 404
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "tool", "owner": a.uid, "version_id": ver["id"], "tools": ["ghost"], "roles": ["nobody"]}, headers=b.headers).status_code == 404


def test_tool_import_falls_back_to_an_evolution_command_when_smoke_fails(platform, monkeypatch):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    imports = platform.mods.imports
    monkeypatch.setattr(imports, "run_smoke", lambda wt: {"ran": True, "ok": False, "log": "ImportError: scaffold.spectral", "tests": ["tests/test_smoke_v0.py"]})
    r = c.post(f"/projects/{nb['id']}/imports", json={"kind": "tool", "owner": a.uid, "version_id": ver["id"], "tools": ["fancy_tool"], "roles": ["data_analyst"]}, headers=b.headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "fallback" and "fancy_tool" in rec["evolution_command"] and f"refs/imports/{a.uid}/{ver['id']}" in rec["evolution_command"]
    assert c.get(f"/hitl/{rec['session_id']}/pending", headers=b.headers).json() == []
    assert not (b.ctx.root / "worktrees" / f"import-{rec['id']}").exists()

    spawned_evo: list[tuple] = []

    async def fake_evo(ctx, sid, command, base):
        spawned_evo.append((sid, command, base))

    monkeypatch.setattr(platform.server, "_spawn_evolution", fake_evo)
    r = c.post(f"/projects/imports/{rec['id']}/evolve", headers=b.headers)
    assert r.status_code == 200 and r.json()["session_id"].startswith("evo-") and spawned_evo[0][1] == rec["evolution_command"]
    assert c.get(f"/projects/imports/{rec['id']}", headers=b.headers).json()["status"] == "evolving"
    assert c.post(f"/projects/imports/{rec['id']}/evolve", headers=b.headers).status_code == 409


def test_harness_merge_mode_applies_donor_delta_as_child_version(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    # Bob has his own version too, so merge is the natural mode.
    seed_version(platform.mods, b.ctx.root, b.uid, tool="bobs_tool", summary="Add bobs_tool")
    boot_children = git(b.ctx.root, "rev-parse", "HEAD").strip()
    r = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"], "mode": "merge"}, headers=b.headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "pending" and rec["mode"] == "merge" and rec["smoke"]["ok"] is True and rec["merged_sha"]
    r = c.post(f"/hitl/{rec['session_id']}/{rec['request_id']}/answer", json={"decision": "approve"}, headers=b.headers)
    assert r.status_code == 200 and r.json()["import"]["status"] == "applied", r.text
    assert (b.ctx.root / "tools" / "fancy_tool").is_dir() and (b.ctx.root / "tools" / "bobs_tool").is_dir()
    vl = c.get("/versions", headers=b.headers).json()
    merged = next(v for v in vl["versions"] if v.get("imported_from"))
    assert merged["active"] and merged["status"] == "merged" and merged["base_sha"] == boot_children
    roles = {r_["name"]: r_["tools"] for r_ in c.get("/roles", headers=b.headers).json()}
    assert "fancy_tool" in roles["data_analyst"] and "bobs_tool" in roles["data_analyst"]
    assert git(b.ctx.root, "rev-parse", "HEAD^").strip() == boot_children  # a real child of Bob's tip, own history kept
    assert rec["merge_report"]["tools_added"] == ["fancy_tool"] and rec["merge_report"]["roles_wired"] == {"data_analyst": ["fancy_tool"]}


def test_stale_import_worktrees_are_pruned(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    stale = b.ctx.root / "worktrees" / "import-imp-20260101-000000-abcdef"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("x")
    rec = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).json()
    assert rec["status"] == "pending" and not stale.exists()
    (b.ctx.root / "worktrees").mkdir(exist_ok=True)
    assert json.loads((platform.mods.projects.imports_dir() / f"{rec['id']}.json").read_text())["status"] == "pending"


def test_approve_while_busy_keeps_the_request_pending(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    rec = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).json()
    assert rec["status"] == "pending"
    platform.server._RUNNING[(b.uid, "20260906-000000-bbbbbb")] = object()
    try:
        r = c.post(f"/hitl/{rec['session_id']}/{rec['request_id']}/answer", json={"decision": "approve"}, headers=b.headers)
        assert r.status_code == 409 and "stop the running" in r.json()["detail"]
    finally:
        platform.server._RUNNING.pop((b.uid, "20260906-000000-bbbbbb"))
    # Nothing was consumed or applied; the same request still answers later.
    assert len(c.get(f"/hitl/{rec['session_id']}/pending", headers=b.headers).json()) == 1
    assert c.get(f"/projects/imports/{rec['id']}", headers=b.headers).json()["status"] == "pending"
    assert c.get("/versions", headers=b.headers).json()["versions"] == []
    # A switch in flight also refuses without consuming.
    lock = platform.server._switch_lock_path(b.ctx)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("x")
    try:
        assert c.post(f"/hitl/{rec['session_id']}/{rec['request_id']}/answer", json={"decision": "approve"}, headers=b.headers).status_code == 409
    finally:
        lock.unlink()
    r = c.post(f"/hitl/{rec['session_id']}/{rec['request_id']}/answer", json={"decision": "approve"}, headers=b.headers)
    assert r.status_code == 200 and r.json()["import"]["status"] == "applied"
    assert not lock.exists()


def test_head_moved_since_prepare_fails_the_apply_cleanly(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    rec = c.post(f"/projects/{nb['id']}/imports", json={"kind": "tool", "owner": a.uid, "version_id": ver["id"], "tools": ["fancy_tool"], "roles": ["data_analyst"]}, headers=b.headers).json()
    assert rec["status"] == "pending"
    # Bob evolves (or switches) before approving: the staged change no longer matches.
    seed_version(platform.mods, b.ctx.root, b.uid, tool="bobs_tool", summary="Add bobs_tool")
    r = c.post(f"/hitl/{rec['session_id']}/{rec['request_id']}/answer", json={"decision": "approve"}, headers=b.headers)
    imp = r.json()["import"]
    assert imp["status"] == "failed" and "changed since this import was prepared" in imp["error"]
    assert not (b.ctx.root / "tools" / "fancy_tool").exists() and not (b.ctx.root / "worktrees" / f"import-{rec['id']}").exists()
    assert [v["id"] for v in c.get("/versions", headers=b.headers).json()["versions"]] == ["20260906-120000__bobs_tool"]
    # The ledger is not stuck: a new import can be requested.
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "tool", "owner": a.uid, "version_id": ver["id"], "tools": ["fancy_tool"], "roles": ["data_analyst"]}, headers=b.headers).json()["status"] == "pending"


def test_deleting_the_import_session_withdraws_the_import(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    rec = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).json()
    assert c.delete(f"/sessions/{rec['session_id']}", headers=b.headers).status_code == 200
    assert c.get(f"/projects/imports/{rec['id']}", headers=b.headers).json()["status"] == "rejected"
    assert subprocess.run(["git", "show-ref", f"refs/imports/{a.uid}/{ver['id']}"], cwd=str(b.ctx.root), capture_output=True).returncode != 0
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).json()["status"] == "pending"


def test_ledger_self_heals_when_the_request_file_is_gone(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    rec = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).json()
    (b.ctx.session_dir(rec["session_id"]) / "hitl" / "pending" / f"{rec['request_id']}.json").unlink()  # crash between answer and apply
    v = c.get(f"/projects/{nb['id']}/recommendations", headers=b.headers).json()
    assert v["pending_imports"] == []
    assert c.get(f"/projects/imports/{rec['id']}", headers=b.headers).json()["status"] == "failed"


def test_in_flight_preparation_blocks_a_second_request_and_is_not_pruned(platform, monkeypatch):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    imports = platform.mods.imports
    # Simulate request A mid-smoke: a `preparing` ledger + its worktree exist.
    fake = {"id": "imp-20260906-120000-aaaaaa", "kind": "harness", "mode": "adopt", "user_id": b.uid, "owner": a.uid, "version_id": ver["id"], "session_id": "evo-import-imp-20260906-120000-aaaaaa", "request_id": None, "status": "preparing", "created": imports.now_str()}
    imports.save_import(fake)
    wt = b.ctx.root / "worktrees" / "import-imp-20260906-120000-aaaaaa"
    wt.mkdir(parents=True)
    r = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers)
    assert r.status_code == 409
    assert imports.prune_stale_worktrees(b.ctx.root) == 0 and wt.exists()
    # An abandoned preparation (API restart) expires and stops blocking.
    fake["created"] = "2026-01-01 00:00:00"
    imports.save_import(fake)
    assert c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"]}, headers=b.headers).json()["status"] == "pending"
    assert c.get(f"/projects/imports/{fake['id']}", headers=b.headers).json()["status"] == "failed" and not wt.exists()


def test_merge_mode_wires_every_donor_listed_tool(platform):
    c, a, b = platform.client, platform.a, platform.b
    ver, na, nb = _donor_and_importer(platform)
    # The donor also lists a scaffold-provided server (not a tools/ package) on the role.
    yp = a.ctx.root / "roles" / "subagents" / "data_analyst.yaml"
    text, _ = platform.mods.imports.add_tool_to_role_yaml(yp.read_text(), "hitl")
    yp.write_text(text)
    git(a.ctx.root, "add", "-A", "--", "roles")
    git(a.ctx.root, "commit", "-q", "-m", "evo: give data_analyst hitl")
    head = git(a.ctx.root, "rev-parse", "HEAD").strip()
    git(a.ctx.root, "tag", "-f", "-a", f"ver/{ver['id']}", head, "-m", "moved")
    meta_path = a.ctx.root / "state" / "archive" / "evolutions" / ver["id"] / "meta.json"
    meta = json.loads(meta_path.read_text()); meta["sha"] = head; meta_path.write_text(json.dumps(meta))
    rec = c.post(f"/projects/{nb['id']}/imports", json={"kind": "harness", "owner": a.uid, "version_id": ver["id"], "mode": "merge"}, headers=b.headers).json()
    assert rec["status"] == "pending", rec
    assert rec["merge_report"]["roles_wired"]["data_analyst"] == ["fancy_tool", "hitl"]
