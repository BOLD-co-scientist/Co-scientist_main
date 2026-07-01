"""R12 long-job runner — core + local backend + HITL + durability."""
from __future__ import annotations

import asyncio
import glob
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    import importlib
    from scaffold import settings as s
    importlib.reload(s)
    return s


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _answer_pending(s, sid, decision, note=""):
    from scaffold import hitl
    pend = glob.glob(str(s.session_dir(sid) / "hitl" / "pending" / "*.json"))
    rid = json.loads(open(pend[0]).read())["id"]
    hitl.answer(sid, rid, decision, note)


def test_handle_roundtrip(tmp_path, monkeypatch):
    _reload(tmp_path, monkeypatch)
    from tools.longjob import job as j
    sid = "lj"
    from scaffold import settings as s
    s.ensure_session_dirs(sid)
    spec = j.JobSpec(command="echo hi", cpu=2, gpu=1, walltime_min=30)
    h = j.make_handle(sid, spec)
    j.write_handle(sid, h)
    got = j.read_handle(sid, h["job_id"])
    assert got["job_id"] == h["job_id"]
    assert got["spec"]["cpu"] == 2 and got["spec"]["gpu"] == 1
    assert "gpu=1" in spec.summary()
    assert j.list_handles(sid)[0]["job_id"] == h["job_id"]


def test_submit_run_done_logs_fetch(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    from tools.longjob import server
    sid = "run"
    s.ensure_session_dirs(sid)
    from tools.longjob import job as j

    async def flow():
        # submit writes a HITL request and blocks; approve it concurrently.
        submit_fn = _get_tool(server, "submit", sid)
        task = asyncio.create_task(submit_fn({
            "command": "echo hello-longjob > out.txt; echo done",
        }))
        await asyncio.sleep(0.2)
        _answer_pending(s, sid, "approve")
        res = await asyncio.wait_for(task, timeout=5)
        return res

    res = asyncio.run(flow())
    txt = _text(res)
    assert "SUBMITTED" in txt, txt
    jid = j.list_handles(sid)[0]["job_id"]
    jid_sid = sid

    # Poll to completion via status.
    status_fn = _get_tool(server, "status", sid)
    deadline = time.time() + 10
    state = ""
    while time.time() < deadline:
        state = _text(asyncio.run(status_fn({"job_id": jid})))
        if "done" in state or "failed" in state:
            break
        time.sleep(0.3)
    assert "done" in state, state

    logs_fn = _get_tool(server, "logs", jid_sid)
    logs = _text(asyncio.run(logs_fn({"job_id": jid, "tail": 50})))
    assert "done" in logs

    fetch_fn = _get_tool(server, "fetch", jid_sid)
    files = _text(asyncio.run(fetch_fn({"job_id": jid})))
    assert "out.txt" in files


def test_submit_rejected_does_not_launch(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    from tools.longjob import server, job as j
    sid = "rej"
    s.ensure_session_dirs(sid)

    async def flow():
        submit_fn = _get_tool(server, "submit", sid)
        task = asyncio.create_task(submit_fn({"command": "echo should-not-run"}))
        await asyncio.sleep(0.2)
        _answer_pending(s, sid, "reject", "no")
        return await asyncio.wait_for(task, timeout=5)

    res = asyncio.run(flow())
    assert "not submitted" in _text(res).lower()
    h = j.list_handles(sid)[0]
    assert h["state"] == "rejected"
    assert h["backend_ref"] is None


def test_unknown_backend_errors(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    from tools.longjob import server
    sid = "bad"
    s.ensure_session_dirs(sid)
    submit_fn = _get_tool(server, "submit", sid)
    res = asyncio.run(submit_fn({"command": "echo x", "backend": "arm64-hpc"}))
    assert res.get("isError") and "not available yet" in _text(res)


# --- helper: pull a tool's callable via make_tools (SdkMcpTool.handler) ------
_TOOLS_SID = "run"  # tests below all use sid "run"/"rej"/"bad"; bind per call


def _get_tool(server_module, name, sid=None):
    """Return the raw async handler for a tool, bound to the given session."""
    from scaffold import settings as s
    sid = sid or "run"
    s.ensure_session_dirs(sid)
    for t in server_module.make_tools(sid):
        if t.name == name:
            return t.handler
    raise LookupError(name)
