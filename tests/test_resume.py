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


@pytest.fixture
def api(fresh_root):
    """A TestClient over a freshly-rooted api.server."""
    from fastapi.testclient import TestClient

    import api.server as srv

    importlib.reload(srv)
    return srv, TestClient(srv.app)


def test_messages_404_unknown_session(api):
    _srv, client = api
    r = client.post("/research/sessions/does-not-exist/messages", json={"text": "hi"})
    assert r.status_code == 404


def test_messages_409_when_busy(api):
    srv, client = api
    sid = "busy-session"
    srv.settings.ensure_session_dirs(sid)
    # Even with a resumable uuid present, a running turn must block follow-ups.
    srv.settings.write_sdk_session(sid, "uuid-x", turns=1)
    srv._RUNNING[sid] = object()  # sentinel: a turn is "running"
    try:
        r = client.post(f"/research/sessions/{sid}/messages", json={"text": "hi"})
        assert r.status_code == 409
        assert "busy" in r.json()["detail"].lower()
    finally:
        srv._RUNNING.pop(sid, None)


def test_messages_409_when_no_prior_turn(api):
    srv, client = api
    sid = "no-uuid-session"
    srv.settings.ensure_session_dirs(sid)  # session exists but never produced a uuid
    r = client.post(f"/research/sessions/{sid}/messages", json={"text": "hi"})
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
