"""R12 Phase 3 — py_exec auto-escalation to the long-job runner."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_with_cap(tmp_path, monkeypatch, cap):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    monkeypatch.setenv("COSCIENTIST_PYEXEC_CPU_SECONDS", str(cap))
    import importlib
    from scaffold import settings as s
    importlib.reload(s)
    from tools.py_exec import server as pe
    importlib.reload(pe)
    return s, pe


def _run_tool(pe, sid):
    for t in pe.make_tools(sid):
        if t.name == "run":
            return t.handler
    raise LookupError("run")


def test_escalation_text_points_to_longjob():
    from tools.py_exec import server as pe
    txt = pe._escalation_text("execution timed out")
    assert "longjob.submit" in txt
    assert "flair-docker" in txt
    assert "async" in txt.lower()


def test_cpu_cap_exceeded_escalates(tmp_path, monkeypatch):
    # Tiny 1s CPU cap; a busy loop hits RLIMIT_CPU (SIGXCPU) fast → escalation.
    s, pe = _reload_with_cap(tmp_path, monkeypatch, 1)
    sid = "escal"
    s.ensure_session_dirs(sid)
    run = _run_tool(pe, sid)
    res = asyncio.run(run({"code": "x = 0\nwhile True:\n    x += 1\n"}))
    text = res["content"][0]["text"]
    assert res.get("isError") is True
    assert "longjob.submit" in text, text
    assert "py_exec is for SHORT" in text


def test_short_job_runs_normally(tmp_path, monkeypatch):
    s, pe = _reload_with_cap(tmp_path, monkeypatch, 10)
    sid = "ok"
    s.ensure_session_dirs(sid)
    run = _run_tool(pe, sid)
    res = asyncio.run(run({"code": "print('hi from pyexec')"}))
    text = res["content"][0]["text"]
    assert "hi from pyexec" in text
    assert "longjob" not in text
