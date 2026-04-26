"""File-IPC. Each agent has an inbox dir; messages are atomically written JSON."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from . import eventlog, settings
from ._atomic import read_json, write_json


def _inbox(session_id: str, agent_id: str) -> Path:
    p = settings.session_dir(session_id) / "inbox" / agent_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _archive(session_id: str, agent_id: str) -> Path:
    p = settings.session_dir(session_id) / "inbox" / agent_id / ".archive"
    p.mkdir(parents=True, exist_ok=True)
    return p


def send(
    session_id: str,
    sender: str,
    target: str,
    kind: str,
    payload: Any,
    parent_id: str | None = None,
) -> str:
    msg_id = uuid.uuid4().hex[:12]
    record = {
        "id": msg_id,
        "ts": time.time(),
        "from": sender,
        "to": target,
        "kind": kind,
        "payload": payload,
        "parent_id": parent_id,
    }
    write_json(_inbox(session_id, target) / f"{msg_id}.json", record)
    eventlog.append(
        session_id,
        actor=sender,
        kind="bus.send",
        ref=msg_id,
        target=target,
        msg_kind=kind,
    )
    return msg_id


def drain(session_id: str, agent_id: str) -> list[dict]:
    """Pop all pending messages for an agent. Atomic move into .archive/."""
    inbox = _inbox(session_id, agent_id)
    archive = _archive(session_id, agent_id)
    msgs: list[dict] = []
    for path in sorted(inbox.glob("*.json")):
        rec = read_json(path)
        if rec is None:
            continue
        msgs.append(rec)
        path.replace(archive / path.name)
        eventlog.append(
            session_id,
            actor=agent_id,
            kind="bus.drain",
            ref=rec.get("id"),
            sender=rec.get("from"),
            msg_kind=rec.get("kind"),
        )
    return msgs


def peek(session_id: str, agent_id: str) -> list[dict]:
    inbox = _inbox(session_id, agent_id)
    return [read_json(p) for p in sorted(inbox.glob("*.json"))]
