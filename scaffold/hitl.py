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
    *,
    stop_event: "asyncio.Event | None" = None,
) -> dict:
    """Block until the human answers. Returns the full answered record.

    If *stop_event* is provided and becomes set while we are polling,
    the pending request is removed and a synthetic ``"interrupted"``
    answer is returned immediately.  There is no timeout — the call
    blocks indefinitely until the human responds or the task is stopped.
    """
    rid = uuid.uuid4().hex[:12]
    rec = {
        "id": rid,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "summary": summary,
        "payload": payload,
        "decision": None,
    }
    pending_path = _pending(session_id) / f"{rid}.json"
    write_json(pending_path, rec)
    eventlog.append(session_id, actor="system", kind="hitl.pending", ref=rid, summary=summary)

    answered_path = _answered(session_id) / f"{rid}.json"
    while True:
        if answered_path.exists():
            return read_json(answered_path)
        if stop_event is not None and stop_event.is_set():
            stop_event.clear()
            pending_path.unlink(missing_ok=True)
            eventlog.append(
                session_id, actor="system", kind="hitl.answer",
                ref=rid, decision="interrupted",
            )
            return {"id": rid, "decision": "interrupted", "note": ""}
        await asyncio.sleep(_POLL_INTERVAL)
