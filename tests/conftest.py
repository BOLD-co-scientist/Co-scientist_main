"""Shared fixtures for the onboarding / project-tree / advisor / import tests
(O1 + O2). Everything runs offline: the research/evolution subprocess spawns
and the advisor subprocess are monkeypatched, and the model call is a fake."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def seed_base(root: Path) -> None:
    for name in ("scaffold", "research", "evolution", "tools", "roles", "prompts", "tests"):
        target = root / name
        if not target.exists():
            shutil.copytree(ROOT / name, target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for name in ("pyproject.toml", "Dockerfile", "README.md", "CLAUDE.md", "ROADMAP.md", ".gitignore"):
        src = ROOT / name
        if src.exists():
            (root / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def reload_platform(monkeypatch, root: Path):
    """Point the platform at a temp root and reload every module that caches
    settings-derived paths, in dependency order."""
    monkeypatch.setenv("COSCIENTIST_ROOT", str(root))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    from scaffold import settings

    importlib.reload(settings)
    import scaffold.archive as archive
    import api.auth as auth
    import api.tenancy as tenancy
    import api.onboarding as onboarding
    import api.llm as llm
    import api.hypothesis as hypothesis
    import api.projects as projects
    import api.advisor as advisor
    import api.imports as imports
    import api.server as server

    for m in (archive, auth, tenancy, onboarding, llm, hypothesis, projects, advisor, imports, server):
        importlib.reload(m)
    return types.SimpleNamespace(
        settings=settings, archive=archive, auth=auth, tenancy=tenancy, onboarding=onboarding, llm=llm,
        hypothesis=hypothesis, projects=projects, advisor=advisor, imports=imports, server=server,
    )


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(root), stderr=subprocess.STDOUT, text=True)


def seed_version(mods, root: Path, uid: str, tool: str = "fancy_tool", role: str = "data_analyst", summary: str = "Add fancy_tool for spectral fits") -> dict:
    """Give a tenant an evolved, switchable version: a new tool package wired
    into a role, committed and recorded as ver/<id> (exactly what propose_merge
    does on an approved merge)."""
    base = git(root, "rev-parse", "HEAD").strip()
    tdir = root / "tools" / tool
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "__init__.py").write_text("", encoding="utf-8")
    (tdir / "server.py").write_text(
        f'"""{tool}: fits spectral peaks and reports linewidths (evolved for an adjacent problem)."""\n\n'
        "def make_server(session_id):\n    return None\n",
        encoding="utf-8",
    )
    yp = root / "roles" / "subagents" / f"{role}.yaml"
    text, _ = mods.imports.add_tool_to_role_yaml(yp.read_text(encoding="utf-8"), tool)
    yp.write_text(text, encoding="utf-8")
    git(root, "add", "-A", "--", "tools", "roles")  # what propose_merge commits from a worktree
    git(root, "commit", "-q", "-m", f"evo: {summary}")
    head = git(root, "rev-parse", "HEAD").strip()
    vid = f"20260906-120000__{tool}"
    adir = root / "state" / "archive" / "evolutions" / vid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "diff.patch").write_text(git(root, "diff", base, head), encoding="utf-8")
    (adir / "rationale.md").write_text(f"# {summary}\n", encoding="utf-8")
    return mods.archive.record_merged_version(
        root, archive_dir=adir, head_sha=head, base_sha=base, summary=summary,
        rationale="Needed peak fitting for the adjacent spectroscopy problem.", owner=uid,
        smoke={"ran": True, "ok": True}, origin_session="evo-seed",
    )


COMPLETE = {
    "title": "Coherence limits of transmon qubits under two-tone drive",
    "domain": "quantum physics",
    "research_question": "Does two-tone driving extend T2 beyond the single-tone dynamical-decoupling limit in fixed-frequency transmons?",
    "objectives": ["Fit T2 vs drive detuning", "Compare against CPMG baseline"],
    "significance": "Coherence time bounds gate fidelity; a drive-based extension would apply to existing hardware without fabrication changes.",
    "prior_work": "Dynamical decoupling (CPMG, XY8) reaches T2 ≈ 2 T1 in transmons; two-tone protocols were shown for NV centres only.",
    "open_gap": "Whether the two-tone advantage survives transmon charge noise and the 1/f flux noise spectrum is unknown.",
    "evaluation_protocol": "Ramsey and echo decay curves fitted with a fixed model; report T2 with bootstrap CIs; the baseline is the CPMG sequence run on the same qubit in the same cooldown.",
    "success_criteria": "T2 improvement > 20% with non-overlapping CIs on at least two qubits.",
    "task_definition": "Fit decay curves in the attached CSV; each row is (delay_us, p_excited, qubit_id, sequence).",
    "existing_results": "CPMG baseline T2 = 41 µs on Q1.",
    "data_access": "Internal lab data; may be used for analysis.",
    "keywords": ["transmon", "coherence", "T2", "dynamical decoupling", "two-tone drive"],
    "deliverables": ["Report (PDF)", "Fit table (CSV)"],
}

ADJACENT = {
    "title": "Charge-noise spectroscopy of fixed-frequency transmons",
    "domain": "quantum physics",
    "research_question": "What is the charge noise power spectrum that limits T2 in fixed-frequency transmons across a cooldown?",
    "objectives": ["Extract the noise spectrum from CPMG filter functions"],
    "significance": "Charge noise is the dominant dephasing channel; its spectrum decides which decoupling sequence can help.",
    "prior_work": "CPMG noise spectroscopy is established for flux noise; transmon charge noise spectra are sparse.",
    "open_gap": "No published spectrum covers the 10 kHz–1 MHz band that two-tone decoupling would exploit.",
    "evaluation_protocol": "Filter-function inversion validated on a synthetic spectrum; report spectral density with error bars.",
    "keywords": ["transmon", "charge noise", "T2", "dynamical decoupling", "noise spectroscopy"],
}

UNRELATED = {
    "title": "Scaffold porosity and chondrocyte survival in cartilage grafts",
    "domain": "regenerative medicine",
    "research_question": "Which pore-size distribution maximises chondrocyte viability at day 14 in printed hydrogel scaffolds?",
    "significance": "Graft failure is dominated by early cell death; pore geometry is a controllable print parameter.",
    "prior_work": "Viability assays exist for bulk hydrogels; printed-pore studies report conflicting optima.",
    "open_gap": "No controlled sweep of pore size at fixed stiffness.",
    "evaluation_protocol": "Live/dead staining quantified by a blinded pipeline; three biological replicates.",
    "keywords": ["cartilage", "hydrogel", "chondrocyte", "scaffold porosity"],
}


@pytest.fixture
def platform(tmp_path, monkeypatch):
    """A fresh platform root with two tenants (alice = a, bob = b), the
    subprocess spawns patched, and the advisor recorded rather than spawned."""
    seed_base(tmp_path)
    mods = reload_platform(monkeypatch, tmp_path)
    server = mods.server

    spawned: list[dict] = []

    async def fake_spawn(ctx, sid, message, resume_uuid=None):
        spawned.append({"sid": sid, "user": ctx.user.user_id, "message": message, "resume": resume_uuid})

    monkeypatch.setattr(server, "_spawn_research", fake_spawn)

    advisor_calls: list[dict] = []

    async def fake_advisor(ctx, pid, trigger):
        advisor_calls.append({"pid": pid, "user": ctx.user.user_id, "trigger": trigger})
        return True

    monkeypatch.setattr(server, "_spawn_advisor", fake_advisor)

    async def fake_generate(goal, parent=None, feedback=None, n=4, context=None):
        return [{"statement": f"H{i}: {goal[:30]}", "rationale": f"because {i}"} for i in range(n)], "fake-model"

    monkeypatch.setattr(server._hypothesis, "generate", fake_generate)

    client = TestClient(server.app)
    users = {}
    for key, name in (("a", "alice"), ("b", "bob")):
        user, api_key = mods.auth.create_user(name)
        ctx = mods.tenancy.context_for(user)
        users[key] = types.SimpleNamespace(user=user, ctx=ctx, headers={"Authorization": f"Bearer {api_key}"}, uid=user.user_id)
    return types.SimpleNamespace(client=client, mods=mods, server=server, spawned=spawned, advisor_calls=advisor_calls, a=users["a"], b=users["b"], root=tmp_path)


def create_brief(client, headers, fields: dict) -> dict:
    r = client.post("/onboarding/briefs", json=fields, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def register(client, headers, bid: str, visibility: str | None = None) -> dict:
    r = client.post(f"/onboarding/briefs/{bid}/register", json={"visibility": visibility} if visibility else {}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def launch(client, headers, bid: str) -> dict:
    r = client.post(f"/onboarding/briefs/{bid}/launch", json={}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def mark_advised(mods, node_id: str) -> None:
    """Pretend the (faked) advisor already produced a record for this node's
    current statement, so the 'unchanged → no re-run' branch is exercised."""
    node = mods.projects.load_node(node_id)
    mods.advisor._write_record(node_id, {"rec_id": "rec-fake", "node_id": node_id, "status": "ready", "statement_sha256": node["statement_sha256"], "created": "2026-09-06 00:00:00", "related_problems": [], "harness_import": None, "tool_imports": [], "statement_feedback": {"missing": [], "notes": [], "suggested_keywords": []}, "hypothesis_seed": {"suggested": False, "why": ""}})
