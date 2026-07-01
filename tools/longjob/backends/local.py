"""Local reference backend — runs a job as a detached in-container subprocess.

Uses ``start_new_session=True`` so the job survives the (short-lived) turn
subprocess that launched it: it's reparented to the container's init and keeps
running until it finishes or the container restarts. This is the test/reference
backend and a CPU fallback — heavy/GPU work belongs on the docker backend. See
docs/plans/R12-longjob-runner.md."""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

from ..job import JobSpec
from . import base


class LocalBackend:
    name = "local"

    def submit(self, spec: JobSpec, workdir: str) -> dict:
        wd = Path(workdir)
        wd.mkdir(parents=True, exist_ok=True)
        rc_file = wd / "rc"
        rc_file.unlink(missing_ok=True)
        # Wrapper writes the exit code to ``rc`` so poll() detects completion
        # even after the launching process is gone.
        wrapper = f"{spec.command}\necho $? > {rc_file}\n"
        (wd / "run.sh").write_text(wrapper, encoding="utf-8")
        env = {**os.environ, **spec.env}
        out = open(wd / "stdout.log", "wb")
        err = open(wd / "stderr.log", "wb")
        proc = subprocess.Popen(
            ["bash", str(wd / "run.sh")],
            cwd=str(wd),
            env=env,
            stdout=out,
            stderr=err,
            start_new_session=True,  # detach: survive the turn subprocess
        )
        return {"pid": proc.pid}

    def poll(self, ref: dict, workdir: str) -> tuple[str, int | None]:
        wd = Path(workdir)
        rc_file = wd / "rc"
        if rc_file.exists():
            try:
                rc = int(rc_file.read_text().strip())
            except (ValueError, OSError):
                return base.FAILED, None
            return (base.DONE if rc == 0 else base.FAILED), rc
        pid = ref.get("pid")
        if pid and _alive(pid):
            return base.RUNNING, None
        # No rc file and process gone → it died without recording an exit code.
        return base.FAILED, None

    def logs(self, ref: dict, workdir: str, tail: int = 200) -> str:
        wd = Path(workdir)
        out = _tail(wd / "stdout.log", tail)
        err = _tail(wd / "stderr.log", tail)
        return f"--- stdout ---\n{out}\n--- stderr ---\n{err}"

    def cancel(self, ref: dict, workdir: str) -> None:
        pid = ref.get("pid")
        if not pid:
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tail(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])
