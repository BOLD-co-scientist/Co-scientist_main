"""Spool protocol shared by the container-side FLAIR backend and the host-side
spooler daemon (deploy/flair_spooler.py). Stdlib-only on purpose: the spooler
runs on the FLAIR node outside Docker and must import this without pulling in the
SDK/scaffold. The container writes a request; the spooler writes status + logs.
See docs/plans/R12-longjob-runner.md."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _sub(spool: str, name: str) -> Path:
    p = Path(spool) / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def request_path(spool: str, job_id: str) -> Path:
    return _sub(spool, "requests") / f"{job_id}.json"


def status_path(spool: str, job_id: str) -> Path:
    return _sub(spool, "status") / f"{job_id}.json"


def log_path(spool: str, job_id: str) -> Path:
    return _sub(spool, "logs") / f"{job_id}.log"


def cancel_path(spool: str, job_id: str) -> Path:
    return _sub(spool, "cancel") / job_id


def _write_atomic(path: Path, obj: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


# --- container side ---------------------------------------------------------
def submit_request(spool: str, req: dict) -> None:
    _write_atomic(request_path(spool, req["job_id"]), req)


def read_status(spool: str, job_id: str) -> dict | None:
    return _read(status_path(spool, job_id))


def request_cancel(spool: str, job_id: str) -> None:
    cancel_path(spool, job_id).write_text("1", encoding="utf-8")


# --- spooler side -----------------------------------------------------------
def list_requests(spool: str) -> list[Path]:
    return sorted(_sub(spool, "requests").glob("*.json"))


def read_request(path: Path) -> dict | None:
    return _read(path)


def write_status(spool: str, job_id: str, status: dict) -> None:
    _write_atomic(status_path(spool, job_id), status)


def cancel_requested(spool: str, job_id: str) -> bool:
    return cancel_path(spool, job_id).exists()
