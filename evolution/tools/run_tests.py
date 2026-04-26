"""Run pytest inside the worktree. The smoke test under tests/ is the floor;
the evolution agent can write more tests inside the worktree as part of a
patch."""
from __future__ import annotations

import asyncio

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import sandbox


def make_server(wt: sandbox.Worktree):
    @tool("run", "Run pytest inside the worktree. Returns exit code + summary.", {
        "args": str,
    })
    async def run(args):
        extra = args.get("args", "") or ""
        cmd = f"pytest -q --maxfail=5 {extra}".strip()
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(wt.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            return {"content": [{"type": "text", "text": "ERROR: pytest timeout"}], "isError": True}
        body = (out.decode("utf-8", errors="replace") + err.decode("utf-8", errors="replace"))[:80_000]
        return {"content": [{"type": "text", "text": f"[exit {proc.returncode}]\n{body}"}]}

    return create_sdk_mcp_server("run_tests", "0.1.0", tools=[run])
