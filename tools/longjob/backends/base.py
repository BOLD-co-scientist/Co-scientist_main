"""BackendAdapter protocol — isolates *where/how* a job runs from the runner
core. The core (handles, tools, HITL, routing) never knows backend specifics.
See docs/plans/R12-longjob-runner.md."""
from __future__ import annotations

from typing import Protocol

from ..job import JobSpec

# Job lifecycle states. "unreachable" is distinct from "failed": the job may
# still be alive but the backend (host/cluster) couldn't be reached — the runner
# must never treat unreachable as failed (no re-submit of a live job).
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
UNREACHABLE = "unreachable"


class BackendAdapter(Protocol):
    name: str

    def submit(self, spec: JobSpec, workdir: str) -> dict:
        """Launch the job. Return a backend_ref (JSON-serializable) used by the
        other methods. Must not block on completion."""
        ...

    def poll(self, ref: dict, workdir: str) -> tuple[str, int | None]:
        """Return (state, rc). rc is None until the job is done/failed."""
        ...

    def logs(self, ref: dict, workdir: str, tail: int = 200) -> str:
        ...

    def cancel(self, ref: dict, workdir: str) -> None:
        ...
