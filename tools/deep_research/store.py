"""Durable deep-research handle store (R14).

Handles live under ``state/sessions/<sid>/deep_research/`` so a background
research job survives the per-turn supervisor subprocess and is reconcilable on
session resume — the same durability contract as the R12 long-job runner.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from scaffold import settings
from scaffold._atomic import read_json, write_json

# Handle states (our own vocabulary; OpenAI statuses map onto these in client.py).
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = {COMPLETED, FAILED, CANCELLED}


def base_dir(session_id: str) -> Path:
    return settings.session_dir(session_id) / "deep_research"


def work_dir(session_id: str, research_id: str) -> Path:
    return base_dir(session_id) / research_id


def handle_path(session_id: str, research_id: str) -> Path:
    return base_dir(session_id) / f"{research_id}.json"


def report_path(session_id: str, research_id: str) -> Path:
    return work_dir(session_id, research_id) / "report.md"


def new_research_id() -> str:
    return "dr-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def make_handle(session_id: str, query: str, model: str, instructions: str, mode: str) -> dict:
    rid = new_research_id()
    work_dir(session_id, rid).mkdir(parents=True, exist_ok=True)
    return {
        "research_id": rid,
        "sid": session_id,
        "query": query,
        "instructions": instructions,
        "model": model,
        "mode": mode,               # "scope" (mandatory blocking call) | "background"
        "state": QUEUED,
        "response_id": None,        # OpenAI background response id
        "error": None,
        "report_path": str(report_path(session_id, rid)),
        "n_citations": 0,
        "submitted_at": time.time(),
        "updated_at": time.time(),
    }


def write_handle(session_id: str, handle: dict) -> None:
    handle["updated_at"] = time.time()
    p = handle_path(session_id, handle["research_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, handle)


def read_handle(session_id: str, research_id: str) -> dict | None:
    rec = read_json(handle_path(session_id, research_id), default=None)
    return rec if isinstance(rec, dict) else None


def list_handles(session_id: str) -> list[dict]:
    d = base_dir(session_id)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        rec = read_json(p, default=None)
        if isinstance(rec, dict):
            out.append(rec)
    return out
