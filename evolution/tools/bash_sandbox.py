"""Bash inside a worktree. Working directory is hard-pinned to the worktree
path; commands cannot reach paths outside it via path validation."""
from __future__ import annotations

import asyncio
import re

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import sandbox


_TRAVERSAL = re.compile(r"(\.\./)|(/etc/)|(/var/)|(/usr/)|(/root/)|(/home/[^/]+/)|(^/)")


def make_server(wt: sandbox.Worktree):
    def _is_safe(cmd: str) -> tuple[bool, str]:
        # Block obvious escapes from the worktree. Tool layer is the boundary.
        if _TRAVERSAL.search(cmd):
            return False, "absolute or parent paths blocked; stay inside the worktree"
        if "sudo" in cmd or "docker" in cmd:
            return False, "elevation/docker calls not allowed"
        return True, ""

    @tool("run", "Run a shell command inside the evolution worktree. Cannot escape it.", {
        "command": str,
    })
    async def run(args):
        ok, why = _is_safe(args["command"])
        if not ok:
            return {"content": [{"type": "text", "text": f"ERROR: {why}"}], "isError": True}
        proc = await asyncio.create_subprocess_shell(
            args["command"],
            cwd=str(wt.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            return {"content": [{"type": "text", "text": "ERROR: timeout"}], "isError": True}
        body = (out.decode("utf-8", errors="replace") + err.decode("utf-8", errors="replace"))[:50_000]
        return {"content": [{"type": "text", "text": f"[exit {proc.returncode}]\n{body}"}]}

    return create_sdk_mcp_server("bash_sandbox", "0.1.0", tools=[run])
