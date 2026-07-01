"""R10 — resumable multi-turn conversations.

Cheap unit tests run anywhere (no API calls): the SDK-session round-trip and
the ``/messages`` endpoint's 404/409 guards. The live end-to-end resume test is
skipped unless ``ANTHROPIC_API_KEY`` is set, since it spends real tokens.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def fresh_root(tmp_path, monkeypatch):
    """Reload settings rooted at a tmp dir so writes never touch real state."""
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    from scaffold import settings as s

    importlib.reload(s)
    s.ensure_runtime_dirs()
    return s


def test_sdk_session_roundtrip(fresh_root):
    s = fresh_root
    sid = "rt-resume"
    s.ensure_session_dirs(sid)

    # No file yet -> None.
    assert s.read_sdk_session(sid) is None

    s.write_sdk_session(sid, "abc-123-uuid", turns=1)
    assert s.read_sdk_session(sid) == "abc-123-uuid"

    # Round-trip persists turn count and is overwritable.
    rec = importlib.import_module("scaffold._atomic").read_json(s.sdk_session_file(sid))
    assert rec["turns"] == 1
    s.write_sdk_session(sid, "def-456-uuid", turns=2)
    assert s.read_sdk_session(sid) == "def-456-uuid"


def _seed_base(root: Path) -> None:
    """Seed the source dirs into the tmp root so tenancy can bootstrap a user."""
    import shutil

    source = Path(__file__).resolve().parents[1]
    for name in ("scaffold", "research", "evolution", "tools", "roles", "prompts", "tests"):
        target = root / name
        if not target.exists():
            shutil.copytree(source / name, target)
    for name in ("pyproject.toml", "Dockerfile", "README.md", "CLAUDE.md", "ROADMAP.md", ".gitignore"):
        src = source / name
        if src.exists():
            (root / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture
def authed(tmp_path, monkeypatch):
    """A TestClient over a freshly-rooted, multi-tenant api.server + one user."""
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    _seed_base(tmp_path)
    from scaffold import settings as s

    importlib.reload(s)
    import api.auth as auth
    import api.tenancy as tenancy
    import api.server as srv

    importlib.reload(auth)
    importlib.reload(tenancy)
    importlib.reload(srv)
    from fastapi.testclient import TestClient

    user, key = auth.create_user("alice")
    ctx = tenancy.context_for(user)
    headers = {"Authorization": f"Bearer {key}"}
    return srv, ctx, TestClient(srv.app), headers


def test_messages_401_without_auth(authed):
    _srv, _ctx, client, _headers = authed
    r = client.post("/research/sessions/whatever/messages", json={"text": "hi"})
    assert r.status_code == 401


def test_messages_404_unknown_session(authed):
    _srv, _ctx, client, headers = authed
    r = client.post("/research/sessions/does-not-exist/messages", json={"text": "hi"}, headers=headers)
    assert r.status_code == 404


def test_messages_409_when_busy(authed):
    srv, ctx, client, headers = authed
    sid = "busy-session"
    srv._ensure_session_dirs(ctx, sid)
    # Even with a resumable uuid present, a running turn must block follow-ups.
    srv._RUNNING[srv._monitor_key(ctx, sid)] = object()  # sentinel: a turn is "running"
    try:
        r = client.post(f"/research/sessions/{sid}/messages", json={"text": "hi"}, headers=headers)
        assert r.status_code == 409
        assert "busy" in r.json()["detail"].lower()
    finally:
        srv._RUNNING.pop(srv._monitor_key(ctx, sid), None)


def test_messages_409_when_no_prior_turn(authed):
    srv, ctx, client, headers = authed
    sid = "no-uuid-session"
    srv._ensure_session_dirs(ctx, sid)  # session exists but never produced a uuid
    r = client.post(f"/research/sessions/{sid}/messages", json={"text": "hi"}, headers=headers)
    assert r.status_code == 409
    assert "resumable" in r.json()["detail"].lower()


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="live resume test needs ANTHROPIC_API_KEY (spends tokens)",
)
def test_live_resume_preserves_context():
    """End-to-end: a fact stated in turn 1 is recalled in turn 2 after resume.

    Uses the *real* repo root (the agent needs ``roles/``/``prompts/``), with a
    throwaway session id under ``state/sessions/`` that is cleaned up after.
    """
    import asyncio
    import shutil

    # Real root — do NOT reparent COSCIENTIST_ROOT here.
    from scaffold import settings as s

    importlib.reload(s)
    import research.runtime as rt

    importlib.reload(rt)
    sid = "test-live-resume"
    try:
        asyncio.run(
            rt.run_turn(
                sid,
                "Acknowledge in one short sentence and remember it: my sample is "
                "labeled XJ-7. Do not delegate or call any tools.",
            )
        )
        uuid = s.read_sdk_session(sid)
        assert uuid, "first turn should persist an SDK session uuid"

        asyncio.run(
            rt.run_turn(
                sid,
                "What did I say my sample is labeled? One short sentence. Do not "
                "delegate or call any tools.",
                resume_uuid=uuid,
            )
        )
        # The resumed turn's reply (a bus 'report' surfaced into the event log)
        # should recall the fact from turn 1.
        text = (s.session_dir(sid) / "events.jsonl").read_text(encoding="utf-8")
        assert "XJ-7" in text, "resumed turn lost the context from turn 1"
        # And resume continued the SAME conversation, not a fork.
        assert s.read_sdk_session(sid) == uuid
    finally:
        shutil.rmtree(s.session_dir(sid), ignore_errors=True)
