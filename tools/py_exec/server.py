"""Run a Python snippet in an ephemeral subprocess. Working dir = session
scratch/<exec_id>; pandas/numpy preinstalled in the container image. Resource
limits via setrlimit. Cannot touch state/, roles/, scaffold/, other sessions."""
from __future__ import annotations

import asyncio
import os
import resource
import shutil
import signal
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import settings


def _escalation_text(reason: str) -> str:
    """Guidance returned when a snippet blows past the local py_exec cap, so the
    agent re-dispatches to the long-job runner instead of losing the work (R12
    Phase 3 auto-escalation). See docs/plans/R12-longjob-runner.md."""
    return (
        f"ERROR: {reason} — py_exec is for SHORT computations only "
        f"(~{settings.PYEXEC_CPU_SECONDS}s CPU cap). This is a long or heavy job, "
        "so do NOT retry it here. Re-dispatch it via the `longjob` tool: call "
        "`longjob.submit` with the same work as `command`, `backend=\"flair-docker\"` "
        "for GPU/heavy jobs (or `\"local\"` for a long CPU job), and resources "
        "(`cpu`, `gpu`, `walltime_min`, `image`). It runs asynchronously — you get "
        "a job_id to poll with `longjob.status`/`wait` and read results with "
        "`longjob.fetch`. The human approves the resource request first."
    )


_PRELUDE = textwrap.dedent("""
    import os, sys
    # Block accidental wandering: chdir is sticky, cd into our own scratch.
    os.chdir(os.environ['COSCIENTIST_EXEC_CWD'])
""").strip()


def _set_limits():
    cpu = settings.PYEXEC_CPU_SECONDS
    mem = settings.PYEXEC_MEM_MB * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except (ValueError, OSError):
        pass


def make_tools(session_id: str):
    @tool("run", "Execute Python code in an ephemeral subprocess (stdlib, pandas, and numpy "
          "are available). The working dir is a private scratch dir; researcher_data is mounted "
          "READ-ONLY at ./data — e.g. open('data/LTEMData/d_labeled.tsv') or "
          "pd.read_csv('data/LTEMData/PM1_WT_baseline.csv'). Write outputs to the cwd. "
          "Returns stdout+stderr. Always pass `intent`: a short one-line description of what "
          "this snippet does (e.g. 'count mutations per lineage') — it labels the step in the "
          "activity log; the code itself is not shown unless expanded.", {
        "intent": str,
        "code": str,
    })
    async def run_(args: dict[str, Any]) -> dict:
        exec_id = uuid.uuid4().hex[:8]
        cwd = settings.session_dir(session_id) / "scratch" / exec_id
        cwd.mkdir(parents=True, exist_ok=True)
        # Read-only access to researcher_data via symlink.
        try:
            (cwd / "data").symlink_to(settings.RESEARCHER_DATA, target_is_directory=True)
        except (OSError, FileExistsError):
            pass

        script = cwd / "run.py"
        script.write_text(_PRELUDE + "\n" + args["code"], encoding="utf-8")

        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "COSCIENTIST_EXEC_CWD": str(cwd),
            "PYTHONDONTWRITEBYTECODE": "1",
            # No HOME, no ANTHROPIC key — explicit minimum environment.
            "HOME": str(cwd),
        }

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=_set_limits,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.PYEXEC_CPU_SECONDS + 5
            )
        except asyncio.TimeoutError:
            proc.kill()
            # Blocked past the wall-clock cap (I/O-bound / sleeping) → escalate.
            return {"content": [{"type": "text", "text": _escalation_text(
                f"execution timed out after {settings.PYEXEC_CPU_SECONDS + 5}s"
            )}], "isError": True}

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        # CPU-bound jobs hit RLIMIT_CPU first: the kernel kills them with SIGXCPU
        # (returncode == -SIGXCPU). Treat that as "too big for py_exec" and
        # escalate rather than reporting an opaque signal death.
        if proc.returncode in (-signal.SIGXCPU, -signal.SIGKILL):
            return {"content": [{"type": "text", "text": _escalation_text(
                f"job exceeded the {settings.PYEXEC_CPU_SECONDS}s CPU limit "
                f"(killed by signal {-proc.returncode})"
            )}], "isError": True}
        text = f"[exit {proc.returncode}]\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        # Preserve scratch dir for inspection.
        return {"content": [{"type": "text", "text": text}]}

    @tool("clear_scratch", "Delete this session's scratch dirs to free space.", {})
    async def clear_(args):
        sd = settings.session_dir(session_id) / "scratch"
        if sd.exists():
            shutil.rmtree(sd, ignore_errors=True)
            sd.mkdir(parents=True, exist_ok=True)
        return {"content": [{"type": "text", "text": "scratch cleared"}]}

    return [run_, clear_]


def make_server(session_id: str):
    return create_sdk_mcp_server("py_exec", "0.1.0", tools=make_tools(session_id))
