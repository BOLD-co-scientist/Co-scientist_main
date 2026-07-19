"""R14 deep-research tool — import-safety, store durability, no-key path,
registry wiring, and a stubbed completed run (no network / no API key)."""
from __future__ import annotations

import asyncio
import sys
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


def _get_tool(server_module, name, sid):
    for t in server_module.make_tools(sid):
        if t.name == name:
            return t.handler
    raise LookupError(name)


def test_import_safe_without_openai_or_key():
    # Module import must never touch the network or require the openai SDK/key.
    import tools.deep_research.server  # noqa
    import tools.deep_research.client  # noqa
    import tools.deep_research.store  # noqa


def test_store_roundtrip(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    from tools.deep_research import store
    sid = "dr"
    s.ensure_session_dirs(sid)
    h = store.make_handle(sid, "what is X?", "o4-mini-deep-research", "", "scope")
    store.write_handle(sid, h)
    got = store.read_handle(sid, h["research_id"])
    assert got["research_id"] == h["research_id"]
    assert got["query"] == "what is X?" and got["mode"] == "scope"
    assert store.list_handles(sid)[0]["research_id"] == h["research_id"]
    assert store.work_dir(sid, h["research_id"]).is_dir()


def test_registry_wiring(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    from scaffold import tools_registry
    s.ensure_session_dirs("wire")
    servers, allowed = tools_registry.build_tools("wire", "supervisor", ["deep_research"])
    assert "deep_research" in servers
    assert "mcp__deep_research" in allowed


def test_supervisor_role_declares_deep_research():
    import yaml
    raw = yaml.safe_load((ROOT / "roles" / "supervisor.yaml").read_text(encoding="utf-8"))
    assert "deep_research" in (raw.get("tools") or [])


def test_run_without_key_reports_actionable_error(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "OPENAI_API_KEY", "")
    from tools.deep_research import server
    sid = "nokey"
    s.ensure_session_dirs(sid)
    run_fn = _get_tool(server, "run", sid)
    res = asyncio.run(run_fn({"query": "scope this problem"}))
    assert res.get("isError")
    assert "OPENAI_API_KEY" in _text(res)
    # The handle is persisted as FAILED so the run is auditable.
    from tools.deep_research import store
    h = store.list_handles(sid)[0]
    assert h["state"] == store.FAILED


def test_start_background_without_key_errors(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "OPENAI_API_KEY", "")
    from tools.deep_research import server
    sid = "bg"
    s.ensure_session_dirs(sid)
    start_fn = _get_tool(server, "start", sid)
    res = asyncio.run(start_fn({"query": "dig into Y"}))
    assert res.get("isError") and "OPENAI_API_KEY" in _text(res)


def test_run_completes_with_stubbed_openai(tmp_path, monkeypatch):
    """Drive `run` end-to-end with the OpenAI client stubbed — validates
    submit → poll → finalize → report write without any network call."""
    s = _reload(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(s, "DEEPRESEARCH_POLL_S", 0)
    from tools.deep_research import server, store
    from tools.deep_research import client as dr_client

    async def fake_submit(query, instructions, model):
        return "resp-123"

    async def fake_poll(response_id):
        assert response_id == "resp-123"
        return store.COMPLETED, object()

    def fake_extract(resp):
        return "# Findings\nThe landscape is Z.", ["https://example.org/a"]

    monkeypatch.setattr(dr_client, "submit", fake_submit)
    monkeypatch.setattr(dr_client, "poll", fake_poll)
    monkeypatch.setattr(dr_client, "extract_report", fake_extract)

    sid = "ok"
    s.ensure_session_dirs(sid)
    run_fn = _get_tool(server, "run", sid)
    res = asyncio.run(run_fn({"query": "scope the problem"}))
    txt = _text(res)
    assert not res.get("isError"), txt
    assert "Deep research complete" in txt
    assert "The landscape is Z." in txt

    h = store.list_handles(sid)[0]
    assert h["state"] == store.COMPLETED and h["n_citations"] == 1
    report = Path(h["report_path"]).read_text(encoding="utf-8")
    assert "The landscape is Z." in report
    assert "https://example.org/a" in report  # sources appended


def test_verification_failure_triggers_fallback(tmp_path, monkeypatch):
    """A deep-research model that fails org verification is retried once with the
    fallback model, and the finished report is labeled as fallback output."""
    s = _reload(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(s, "DEEPRESEARCH_POLL_S", 0)
    monkeypatch.setattr(s, "DEEPRESEARCH_MODEL", "o4-mini-deep-research")
    monkeypatch.setattr(s, "DEEPRESEARCH_FALLBACK_MODEL", "o4-mini")
    from tools.deep_research import server, store
    from tools.deep_research import client as dr_client

    submitted = []

    async def fake_submit(query, instructions, model):
        submitted.append(model)
        return f"resp-{model}"

    class _Err:
        message = ("Your organization must be verified to use the model "
                   "o4-mini-deep-research.")

    class _FailResp:
        error = _Err()
        status = "failed"

    class _OkResp:
        error = None
        status = "completed"

    async def fake_poll(response_id):
        if response_id == "resp-o4-mini-deep-research":
            return store.FAILED, _FailResp()
        return store.COMPLETED, _OkResp()

    monkeypatch.setattr(dr_client, "submit", fake_submit)
    monkeypatch.setattr(dr_client, "poll", fake_poll)
    monkeypatch.setattr(dr_client, "extract_report", lambda r: ("fallback findings", []))

    sid = "fb"
    s.ensure_session_dirs(sid)
    run_fn = _get_tool(server, "run", sid)
    res = asyncio.run(run_fn({"query": "scope"}))
    txt = _text(res)
    assert not res.get("isError"), txt
    assert "FALLBACK" in txt
    assert submitted == ["o4-mini-deep-research", "o4-mini"]
    h = store.list_handles(sid)[0]
    assert h["fell_back"] and h["model"] == "o4-mini"
    assert h["primary_model"] == "o4-mini-deep-research"
    assert h["state"] == store.COMPLETED
    report = Path(h["report_path"]).read_text(encoding="utf-8")
    assert "fallback model" in report.lower()


def test_verification_failure_hard_fails_without_fallback(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(s, "DEEPRESEARCH_POLL_S", 0)
    monkeypatch.setattr(s, "DEEPRESEARCH_MODEL", "o4-mini-deep-research")
    monkeypatch.setattr(s, "DEEPRESEARCH_FALLBACK_MODEL", "")  # disabled
    from tools.deep_research import server, store
    from tools.deep_research import client as dr_client

    class _Err:
        message = "organization must be verified"

    class _FailResp:
        error = _Err()

    async def fake_submit(q, i, m):
        return "resp-x"

    async def fake_poll(rid):
        return store.FAILED, _FailResp()

    monkeypatch.setattr(dr_client, "submit", fake_submit)
    monkeypatch.setattr(dr_client, "poll", fake_poll)
    sid = "hardfail"
    s.ensure_session_dirs(sid)
    run_fn = _get_tool(server, "run", sid)
    res = asyncio.run(run_fn({"query": "scope"}))
    assert res.get("isError")
    assert "verified" in _text(res).lower()


def test_status_fetch_cancel_on_stubbed_background(tmp_path, monkeypatch):
    s = _reload(tmp_path, monkeypatch)
    monkeypatch.setattr(s, "OPENAI_API_KEY", "sk-test")
    from tools.deep_research import server, store
    from tools.deep_research import client as dr_client

    state = {"n": 0}

    async def fake_submit(query, instructions, model):
        return "resp-bg"

    async def fake_poll(response_id):
        # First poll: still running; second: completed.
        state["n"] += 1
        if state["n"] < 2:
            return store.RUNNING, object()
        return store.COMPLETED, object()

    monkeypatch.setattr(dr_client, "submit", fake_submit)
    monkeypatch.setattr(dr_client, "poll", fake_poll)
    monkeypatch.setattr(dr_client, "extract_report", lambda r: ("done body", []))

    sid = "bgflow"
    s.ensure_session_dirs(sid)
    start_fn = _get_tool(server, "start", sid)
    res = asyncio.run(start_fn({"query": "background dig"}))
    assert "STARTED" in _text(res)
    rid = store.list_handles(sid)[0]["research_id"]

    status_fn = _get_tool(server, "status", sid)
    assert "running" in _text(asyncio.run(status_fn({"research_id": rid})))
    # Second status advances to completed and finalizes.
    assert "completed" in _text(asyncio.run(status_fn({"research_id": rid})))

    fetch_fn = _get_tool(server, "fetch", sid)
    assert "done body" in _text(asyncio.run(fetch_fn({"research_id": rid})))
