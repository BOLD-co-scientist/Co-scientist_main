"""v0 smoke test. Verifies the scaffold imports and core primitives work
without making any API calls. Required to pass before any sensitive evolution
merge."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Ensure we can import scaffold even when pytest is invoked from a worktree.
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_scaffold_imports():
    import scaffold.bus  # noqa
    import scaffold.config_loader  # noqa
    import scaffold.eventlog  # noqa
    import scaffold.hitl  # noqa
    import scaffold.memory  # noqa
    import scaffold.sandbox  # noqa
    import scaffold.settings  # noqa
    import scaffold.spawn  # noqa
    import scaffold.system_tools  # noqa
    import scaffold.tools_registry  # noqa


def test_research_imports():
    import research.runtime  # noqa


def test_evolution_imports():
    import evolution.runtime  # noqa
    import evolution.tools.edit  # noqa
    import evolution.tools.bash_ro  # noqa
    import evolution.tools.bash_sandbox  # noqa
    import evolution.tools.run_tests  # noqa
    import evolution.tools.propose_merge  # noqa


def test_tool_servers_construct(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    # Re-import settings under the override so paths point at tmp_path.
    import importlib
    from scaffold import settings as s
    importlib.reload(s)
    from tools.fs_read import server as fs_read
    from tools.fs_write_workspace import server as fs_write
    from tools.py_exec import server as py_exec

    sid = "smoke-test-session"
    s.ensure_session_dirs(sid)
    assert fs_read.make_server(sid) is not None
    assert fs_write.make_server(sid) is not None
    assert py_exec.make_server(sid) is not None


def test_role_loader(monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(ROOT))
    import importlib
    from scaffold import settings as s
    importlib.reload(s)
    from scaffold import config_loader
    importlib.reload(config_loader)
    sup = config_loader.load_supervisor()
    assert sup.name == "supervisor"
    assert sup.can_spawn is True
    subs = config_loader.list_subagent_roles()
    names = {r.name for r in subs}
    assert "generalist_researcher" in names
    assert "data_analyst" in names


def test_bus_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    import importlib
    from scaffold import settings as s, bus
    importlib.reload(s)
    importlib.reload(bus)
    sid = "rt"
    s.ensure_session_dirs(sid)
    mid = bus.send(sid, "human", "supervisor", "human_directive", {"text": "hello"})
    assert mid
    msgs = bus.drain(sid, "supervisor")
    assert len(msgs) == 1
    assert msgs[0]["payload"]["text"] == "hello"


def test_memory_recall(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    import importlib
    from scaffold import settings as s, memory
    importlib.reload(s)
    importlib.reload(memory)
    sid = "mem"
    s.ensure_session_dirs(sid)
    memory.remember_project(sid, "supervisor", "the gene knockout reduced growth at 30C")
    memory.remember_project(sid, "supervisor", "we tried biolog plate PM1 first")
    hits = memory.recall_project(sid, "knockout growth", k=3)
    assert any("knockout" in h["text"] for h in hits)


def test_dockerfile_runs_nonroot():
    """BOLD/FLAIR Rule 1: the container must NOT run as root. Enforce that the
    Dockerfile keeps a non-root effective USER so a change (human or evolution
    agent) can never silently reintroduce root. See docs/BOLD-server-guide.md."""
    dockerfile = ROOT / "Dockerfile"
    if not dockerfile.exists():
        pytest.skip("no Dockerfile in this root")
    # Effective USER = the last uncommented USER instruction.
    users = []
    for line in dockerfile.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        if s.split()[0].upper() == "USER":
            users.append(s.split(maxsplit=1)[1].strip())
    assert users, "Dockerfile must set a non-root USER (BOLD/FLAIR Rule 1)"
    effective = users[-1]
    # Strip any :group and ${...} default wrappers for the root check.
    name = effective.split(":", 1)[0]
    assert name not in ("root", "0"), (
        f"Dockerfile must not run as root; effective USER is {effective!r} "
        "(BOLD/FLAIR Rule 1 — see docs/BOLD-server-guide.md)"
    )


def test_sandbox_path_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    import importlib
    from scaffold import settings as s, sandbox
    importlib.reload(s)
    importlib.reload(sandbox)
    # Set up a tiny git repo in the test root.
    (tmp_path / "f.txt").write_text("seed")
    sandbox.ensure_repo()
    wt = sandbox.create("test-slug")
    inside = wt.path / "ok.txt"
    outside = tmp_path / "escape.txt"
    assert sandbox.in_worktree(wt, inside)
    assert not sandbox.in_worktree(wt, outside)
    sandbox.discard(wt)
