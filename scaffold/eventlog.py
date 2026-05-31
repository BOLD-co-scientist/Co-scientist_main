"""Append-only audit trail. UIs tail this file."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any
from datetime import datetime

from . import settings
from ._atomic import append_jsonl


def _path(session_id: str) -> Path:
    return settings.session_dir(session_id) / "events.jsonl"


def append(session_id: str, actor: str, kind: str, **fields: Any) -> str:
    eid = uuid.uuid4().hex[:12]
    record = {
        "id": eid,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": session_id,
        "actor": actor,
        "kind": kind,
        **fields,
    }
    append_jsonl(_path(session_id), record)
    return eid


def tail(session_id: str, since_id: str | None = None) -> list[dict]:
    """Return records strictly after `since_id`. Used by SSE."""
    p = _path(session_id)
    if not p.exists():
        return []
    out: list[dict] = []
    found = since_id is None
    import json
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if found:
                out.append(rec)
            elif rec.get("id") == since_id:
                found = True
    return out
