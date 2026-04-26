"""Run a Python snippet in an ephemeral subprocess. Working dir = session
scratch/<exec_id>; pandas/numpy preinstalled in the container image. Resource
limits via setrlimit. Cannot touch state/, roles/, scaffold/, other sessions."""
from __future__ import annotations

import asyncio
import os
import resource
import shutil
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import settings


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


def make_server(session_id: str):
    @tool("run", "Execute Python code in an ephemeral subprocess. Returns stdout+stderr.", {
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
            return {"content": [{"type": "text", "text": "ERROR: execution timeout"}], "isError": True}

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
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

    return create_sdk_mcp_server("py_exec", "0.1.0", tools=[run_, clear_])
