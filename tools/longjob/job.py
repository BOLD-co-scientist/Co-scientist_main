"""JobSpec + durable job-handle store (R12). Handles live under
``state/sessions/<sid>/jobs/`` so a long job survives turn-subprocess exits and
is reconcilable on session resume. See docs/plans/R12-longjob-runner.md."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scaffold import settings
from scaffold._atomic import read_json, write_json


@dataclass
class JobSpec:
    """What to run. ``command`` is a shell command line; ``backend`` selects the
    dispatch target. Resources are advisory for the local backend, enforced by
    the docker/HPC backends."""
    command: str
    backend: str = "local"
    cpu: int = 1
    gpu: int = 0
    walltime_min: int = 60
    mem_mb: int = 2048
    image: str = ""          # docker backends only
    env: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        bits = [f"backend={self.backend}", f"cpu={self.cpu}"]
        if self.gpu:
            bits.append(f"gpu={self.gpu}")
        bits.append(f"walltime={self.walltime_min}m")
        if self.image:
            bits.append(f"image={self.image}")
        return ", ".join(bits)


def jobs_dir(session_id: str) -> Path:
    return settings.session_dir(session_id) / "jobs"


def job_dir(session_id: str, job_id: str) -> Path:
    return jobs_dir(session_id) / job_id


def handle_path(session_id: str, job_id: str) -> Path:
    return jobs_dir(session_id) / f"{job_id}.json"


def new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def write_handle(session_id: str, handle: dict) -> None:
    handle["updated_at"] = time.time()
    path = handle_path(session_id, handle["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, handle)


def read_handle(session_id: str, job_id: str) -> dict | None:
    rec = read_json(handle_path(session_id, job_id), default=None)
    return rec if isinstance(rec, dict) else None


def list_handles(session_id: str) -> list[dict]:
    d = jobs_dir(session_id)
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        rec = read_json(p, default=None)
        if isinstance(rec, dict):
            out.append(rec)
    return out


def make_handle(session_id: str, spec: JobSpec) -> dict:
    job_id = new_job_id()
    wd = job_dir(session_id, job_id) / "work"
    wd.mkdir(parents=True, exist_ok=True)
    return {
        "job_id": job_id,
        "sid": session_id,
        "spec": asdict(spec),
        "backend": spec.backend,
        "backend_ref": None,
        "state": "queued",
        "rc": None,
        "workdir": str(wd),
        "submitted_at": time.time(),
        "updated_at": time.time(),
    }
