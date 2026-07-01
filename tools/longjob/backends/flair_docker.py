"""FLAIR docker backend (container side). Does NOT run docker — that would need
the docker socket (root-equivalent, forbidden by BOLD Rule 1). Instead it writes
a job request into the shared spool; the host-side spooler (running as the user
on the node) launches the BOLD-compliant `docker run --gpus …` and writes status
back. See deploy/flair_spooler.py and docs/plans/R12-longjob-runner.md."""
from __future__ import annotations

import time
from pathlib import Path

from scaffold import settings
from ..job import JobSpec
from .. import spool as sp
from . import base


class FlairDockerBackend:
    name = "flair-docker"

    def submit(self, spec: JobSpec, workdir: str) -> dict:
        spool = str(settings.LONGJOB_SPOOL_DIR)
        job_id = Path(workdir).parent.name  # .../jobs/<job_id>/work
        image = spec.image or settings.LONGJOB_JOB_IMAGE
        if not image:
            raise ValueError(
                "no job image — set spec.image or COSCIENTIST_JOB_IMAGE")
        req = {
            "job_id": job_id,
            "image": image,
            "command": spec.command,
            "gpu": int(spec.gpu),
            "cpus": int(spec.cpu),
            "mem_mb": int(spec.mem_mb),
            "walltime_min": int(spec.walltime_min),
            "container_workdir": str(workdir),
            "env": dict(spec.env),
            "submitted_at": time.time(),
        }
        sp.submit_request(spool, req)
        return {"job_id": job_id, "spool": spool}

    def poll(self, ref: dict, workdir: str) -> tuple[str, int | None]:
        st = sp.read_status(ref["spool"], ref["job_id"])
        if not st:
            return base.QUEUED, None
        state = st.get("state", base.QUEUED)
        return state, st.get("rc")

    def logs(self, ref: dict, workdir: str, tail: int = 200) -> str:
        lp = sp.log_path(ref["spool"], ref["job_id"])
        if not lp.exists():
            return "(no logs yet)"
        lines = lp.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-tail:])

    def cancel(self, ref: dict, workdir: str) -> None:
        sp.request_cancel(ref["spool"], ref["job_id"])
