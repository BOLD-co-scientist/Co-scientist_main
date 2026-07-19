"""R15: autonomous (no-human) mode.

A session flagged autonomous auto-answers its HITL gates so a research run
proceeds without a human — the counterfactual arm for benchmarking the human's
contribution. Prompts and gates are identical in both modes; only who answers
differs. Evolution merges and interrupt feedback are never auto-answered.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def hitl(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings as s
    importlib.reload(s)
    from scaffold import eventlog
    importlib.reload(eventlog)
    from scaffold import hitl as h
    importlib.reload(h)
    s.ensure_session_dirs("auto-test")
    return h


def test_flag_roundtrip(hitl):
    sid = "auto-test"
    assert not hitl.is_autonomous(sid)
    hitl.set_autonomous(sid)
    assert hitl.is_autonomous(sid)
    hitl.set_autonomous(sid, enabled=False)
    assert not hitl.is_autonomous(sid)


def test_ask_returns_immediately_with_judgment_note(hitl):
    """The open-ended `ask` gate resolves as a free-form answer telling the
    agent to proceed on its own judgment (same shape as a human chat reply)."""
    sid = "auto-test"
    hitl.set_autonomous(sid)
    rec = asyncio.run(
        asyncio.wait_for(hitl.ask(sid, "ask", "which dataset?", {}), timeout=5)
    )
    assert rec["decision"] == "answer"
    assert rec["note"] == hitl.AUTONOMOUS_ANSWER_NOTE
    assert rec.get("auto") is True


def test_approval_gate_auto_approves(hitl):
    sid = "auto-test"
    hitl.set_autonomous(sid)
    rec = asyncio.run(
        asyncio.wait_for(hitl.ask(sid, "tool_gate", "run tool?", {}), timeout=5)
    )
    assert rec["decision"] == "approve"
    assert hitl.list_pending(sid) == []


def test_checkpoint_open_request_resolves_for_pollers(hitl):
    """Checkpoints use open_request + poll_answer (not ask); the auto-answer
    must land in answered/ so the runtime's poll loop returns at once."""
    sid = "auto-test"
    hitl.set_autonomous(sid)
    rid = hitl.open_request(sid, "checkpoint", "Checkpoint (50 events).", {})
    ans = hitl.poll_answer(sid, rid)
    assert ans is not None and ans["decision"] == "approve"


@pytest.mark.parametrize("kind", ["evolution_merge", "interrupt_feedback"])
def test_protected_kinds_still_block(hitl, kind):
    sid = "auto-test"
    hitl.set_autonomous(sid)
    rid = hitl.open_request(sid, kind, "sensitive", {})
    assert hitl.poll_answer(sid, rid) is None
    assert any(r["id"] == rid for r in hitl.list_pending(sid))


def test_without_flag_nothing_auto_answers(hitl):
    sid = "auto-test"
    rid = hitl.open_request(sid, "ask", "question", {})
    assert hitl.poll_answer(sid, rid) is None


def test_schema_defaults_off():
    from api import schemas
    assert schemas.StartResearchRequest(task="t").autonomous is False
    assert schemas.StartResearchRequest(task="t", autonomous=True).autonomous is True
