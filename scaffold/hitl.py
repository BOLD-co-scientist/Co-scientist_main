"""Human-in-the-loop router. Sensitive actions write a pending request and
block until the human answers via the API."""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any
from datetime import datetime

from . import eventlog, settings
from ._atomic import read_json, write_json


_POLL_INTERVAL = 0.5  # seconds


def _stop_flag(session_id: str) -> Path:
    return settings.session_dir(session_id) / "control" / "stop"


def request_stop(session_id: str) -> None:
    """Signal a running session (possibly in another process) to stop.

    Cross-process safe: the runtime checks for this flag at tool boundaries
    (PreToolUse hook) and while blocking in ``ask()``. Replaces the old
    in-memory ``asyncio.Event`` so the runtime can run as a subprocess.
    """
    p = _stop_flag(session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def is_stop_requested(session_id: str) -> bool:
    return _stop_flag(session_id).exists()


def clear_stop(session_id: str) -> None:
    """Consume the stop flag (one-shot, mirrors the old ``event.clear()``)."""
    _stop_flag(session_id).unlink(missing_ok=True)


def _autonomous_flag(session_id: str) -> Path:
    return settings.session_dir(session_id) / "control" / "autonomous"


def set_autonomous(session_id: str, enabled: bool = True) -> None:
    """Toggle autonomous (no-human) mode for a session.

    In autonomous mode every HITL gate except the ones in
    ``_NEVER_AUTO_KINDS`` is answered immediately by the system, so a research
    run proceeds without a human. Built for HITL-vs-autonomous benchmarking:
    prompts and gates are identical in both modes — only who answers differs.
    Auto-answers are logged as ``hitl.auto_answer`` with ``actor="auto"`` so
    runs can be compared from ``events.jsonl`` alone.
    """
    p = _autonomous_flag(session_id)
    if enabled:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    else:
        p.unlink(missing_ok=True)


def is_autonomous(session_id: str) -> bool:
    return _autonomous_flag(session_id).exists()


# Gates that must never be auto-answered, even in autonomous mode:
# evolution merges keep the human as the final gate on self-modification, and
# interrupt feedback only exists because a human pressed Stop.
_NEVER_AUTO_KINDS = frozenset({"evolution_merge", "interrupt_feedback"})

AUTONOMOUS_ANSWER_NOTE = (
    "Autonomous mode: no human is available for this session. Decide using "
    "your own best judgment, state the assumption you made, and continue."
)


def _pending(session_id: str) -> Path:
    return settings.session_dir(session_id) / "hitl" / "pending"


def _answered(session_id: str) -> Path:
    return settings.session_dir(session_id) / "hitl" / "answered"


def list_pending(session_id: str) -> list[dict]:
    p = _pending(session_id)
    if not p.exists():
        return []
    a = _answered(session_id)
    result = []
    for f in sorted(p.glob("*.json")):
        rec = read_json(f)
        rid = rec.get("id", f.stem)
        if a.exists() and (a / f"{rid}.json").exists():
            f.unlink(missing_ok=True)
            continue
        result.append(rec)
    return result


def clear_pending(session_id: str) -> None:
    """Remove all pending HITL requests for a session (e.g. after interrupt)."""
    p = _pending(session_id)
    if not p.exists():
        return
    for f in p.glob("*.json"):
        f.unlink(missing_ok=True)


def reject_all_pending(session_id: str) -> None:
    """Write a reject answer for every pending request so that any
    ``ask()`` call blocking on that request returns immediately."""
    p = _pending(session_id)
    if not p.exists():
        return
    for f in sorted(p.glob("*.json")):
        rec = read_json(f)
        rid = rec.get("id", f.stem)
        rec["decision"] = "reject"
        rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rec["note"] = "stopped"
        write_json(_answered(session_id) / f"{rid}.json", rec)
        f.unlink(missing_ok=True)
        eventlog.append(
            session_id, actor="system", kind="hitl.answer",
            ref=rid, decision="reject",
        )


def _chat_dir(session_id: str) -> Path:
    return settings.session_dir(session_id) / "control" / "chat"


def queue_chat(session_id: str, text: str) -> str:
    """Queue a free-form human message for a turn that is currently blocked on a
    HITL prompt. Consumed by ``drain_chat`` inside the runtime's wait loops, so
    the human can talk to the agent *before* deciding approve/deny."""
    d = _chat_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    # Filename is time-ordered so ``drain_chat`` returns messages in send order.
    cid = f"{time.time():.6f}-{uuid.uuid4().hex[:8]}"
    write_json(d / f"{cid}.json", {
        "id": cid,
        "text": text,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    return cid


def drain_chat(session_id: str) -> list[dict]:
    """Return and remove all queued chat messages, in send order."""
    d = _chat_dir(session_id)
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.json")):
        rec = read_json(f)
        if rec:
            out.append(rec)
        f.unlink(missing_ok=True)
    return out


def open_request(session_id: str, kind: str, summary: str, payload: Any) -> str:
    """Write a pending HITL request and return its id, without blocking.

    Pair with ``poll_answer`` (and optionally ``drain_chat``) to build an
    interactive wait that lets the human chat with the agent before deciding."""
    rid = uuid.uuid4().hex[:12]
    rec = {
        "id": rid,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "summary": summary,
        "payload": payload,
        "decision": None,
    }
    write_json(_pending(session_id) / f"{rid}.json", rec)
    eventlog.append(session_id, actor="system", kind="hitl.pending", ref=rid, summary=summary)

    # Autonomous mode: answer the gate immediately so both ``ask()`` and the
    # open_request/poll_answer waits (e.g. checkpoints) return without a human.
    if kind not in _NEVER_AUTO_KINDS and is_autonomous(session_id):
        decision = "answer" if kind == "ask" else "approve"
        rec["decision"] = decision
        rec["note"] = AUTONOMOUS_ANSWER_NOTE if kind == "ask" else ""
        rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rec["auto"] = True
        write_json(_answered(session_id) / f"{rid}.json", rec)
        (_pending(session_id) / f"{rid}.json").unlink(missing_ok=True)
        eventlog.append(
            session_id, actor="auto", kind="hitl.auto_answer", ref=rid, decision=decision
        )
    return rid


def poll_answer(session_id: str, request_id: str) -> dict | None:
    """Return the answered record for ``request_id`` if the human has decided,
    else None. Non-blocking."""
    answered_path = _answered(session_id) / f"{request_id}.json"
    if answered_path.exists():
        return read_json(answered_path)
    return None


def resolve_request(session_id: str, request_id: str, decision: str, note: str = "") -> None:
    """Resolve an open request programmatically (e.g. on stop/interrupt) so any
    poller and the UI both see it as decided."""
    pending_path = _pending(session_id) / f"{request_id}.json"
    rec = read_json(pending_path) if pending_path.exists() else {"id": request_id}
    rec["decision"] = decision
    rec["note"] = note
    rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_json(_answered(session_id) / f"{request_id}.json", rec)
    pending_path.unlink(missing_ok=True)
    eventlog.append(session_id, actor="system", kind="hitl.answer", ref=request_id, decision=decision)


def answer(session_id: str, request_id: str, decision: str, note: str | None = None) -> None:
    """Decision in {'approve', 'reject', 'edit'}. 'edit' is reserved for future use."""
    pending = _pending(session_id) / f"{request_id}.json"
    if not pending.exists():
        raise FileNotFoundError(f"no pending HITL request {request_id}")
    rec = read_json(pending)
    rec["decision"] = decision
    rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec["note"] = note
    write_json(_answered(session_id) / f"{request_id}.json", rec)
    pending.unlink()
    eventlog.append(
        session_id,
        actor="human",
        kind="hitl.answer",
        ref=request_id,
        decision=decision,
    )


async def ask(
    session_id: str,
    kind: str,
    summary: str,
    payload: Any,
) -> dict:
    """Block until the human answers. Returns the full answered record.

    If a stop is requested for this session (via ``request_stop``) while we
    are polling, the pending request is removed and a synthetic
    ``"interrupted"`` answer is returned immediately.  There is no timeout —
    the call blocks indefinitely until the human responds or the session is
    stopped.
    """
    rid = open_request(session_id, kind, summary, payload)
    pending_path = _pending(session_id) / f"{rid}.json"
    answered_path = _answered(session_id) / f"{rid}.json"
    while True:
        if answered_path.exists():
            return read_json(answered_path)
        # A free-form chat reply counts as the human's answer here: the agent is
        # blocked inside this call (e.g. an open-ended ``ask``), so the only way
        # to reach it is via the returned record. Deliver the text as the note.
        msgs = drain_chat(session_id)
        if msgs:
            text = "\n".join(m.get("text", "") for m in msgs).strip()
            rec = read_json(pending_path) if pending_path.exists() else {"id": rid}
            rec["decision"] = "answer"
            rec["note"] = text
            rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            write_json(answered_path, rec)
            pending_path.unlink(missing_ok=True)
            eventlog.append(
                session_id, actor="human", kind="hitl.answer", ref=rid, decision="answer"
            )
            return rec
        if is_stop_requested(session_id):
            clear_stop(session_id)
            pending_path.unlink(missing_ok=True)
            eventlog.append(
                session_id, actor="system", kind="hitl.answer",
                ref=rid, decision="interrupted",
            )
            return {"id": rid, "decision": "interrupted", "note": ""}
        await asyncio.sleep(_POLL_INTERVAL)
