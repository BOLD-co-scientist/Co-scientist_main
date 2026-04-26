"""Human-in-the-loop router. Sensitive actions write a pending request and
block until the human answers via the API."""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from . import eventlog, settings
from ._atomic import read_json, write_json


_POLL_INTERVAL = 0.5  # seconds
_TIMEOUT = 60 * 60  # 1 hour default — researchers need time to think


def _pending(session_id: str) -> Path:
    return settings.session_dir(session_id) / "hitl" / "pending"


def _answered(session_id: str) -> Path:
    return settings.session_dir(session_id) / "hitl" / "answered"


def list_pending(session_id: str) -> list[dict]:
    p = _pending(session_id)
    if not p.exists():
        return []
    return [read_json(f) for f in sorted(p.glob("*.json"))]


def answer(session_id: str, request_id: str, decision: str, note: str | None = None) -> None:
    """Decision in {'approve', 'reject', 'edit'}. 'edit' is reserved for future use."""
    pending = _pending(session_id) / f"{request_id}.json"
    if not pending.exists():
        raise FileNotFoundError(f"no pending HITL request {request_id}")
    rec = read_json(pending)
    rec["decision"] = decision
    rec["decided_at"] = time.time()
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


async def ask(session_id: str, kind: str, summary: str, payload: Any, *, timeout: float = _TIMEOUT) -> dict:
    """Block until the human answers. Returns the full answered record."""
    rid = uuid.uuid4().hex[:12]
    rec = {
        "id": rid,
        "ts": time.time(),
        "kind": kind,            # e.g. "tool_use", "evolution_merge", "spawn_grant"
        "summary": summary,
        "payload": payload,
        "decision": None,
    }
    write_json(_pending(session_id) / f"{rid}.json", rec)
    eventlog.append(session_id, actor="system", kind="hitl.pending", ref=rid, summary=summary)

    answered_path = _answered(session_id) / f"{rid}.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if answered_path.exists():
            return read_json(answered_path)
        await asyncio.sleep(_POLL_INTERVAL)
    # Timeout — auto-reject for safety.
    eventlog.append(session_id, actor="system", kind="hitl.timeout", ref=rid)
    return {"id": rid, "decision": "reject", "note": "timeout"}
