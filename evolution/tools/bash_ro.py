"""Read-only bash for navigation: ls/find/grep/git-log/cat. No mutating ops."""
from __future__ import annotations

import asyncio
import shlex

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import settings


_ALLOWED_PROGRAMS = {
    "ls", "find", "grep", "rg", "cat", "head", "tail", "wc", "sort", "uniq",
    "tree", "git", "pwd", "echo", "stat", "file",
}
_BAD_GIT_SUBS = {"checkout", "reset", "rebase", "merge", "push", "pull", "stash"}


def _is_safe(cmd: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError as e:
        return False, f"shlex error: {e}"
    if not tokens:
        return False, "empty command"
    program = tokens[0]
    if program not in _ALLOWED_PROGRAMS:
        return False, f"program {program!r} not in read-only allowlist"
    if any(t in (";", "&&", "||", "|", ">", ">>", "<") for t in tokens):
        return False, "redirection/piping/chaining not allowed"
    if program == "git" and len(tokens) >= 2 and tokens[1] in _BAD_GIT_SUBS:
        return False, f"git {tokens[1]} not allowed in bash_ro"
    return True, ""


def make_server():
    @tool("run", "Run a read-only shell command rooted at the repo. Allowlist enforced.", {
        "command": str,
    })
    async def run(args):
        ok, why = _is_safe(args["command"])
        if not ok:
            return {"content": [{"type": "text", "text": f"ERROR: {why}"}], "isError": True}
        proc = await asyncio.create_subprocess_shell(
            args["command"],
            cwd=str(settings.ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"content": [{"type": "text", "text": "ERROR: timeout"}], "isError": True}
        body = (out.decode("utf-8", errors="replace") + err.decode("utf-8", errors="replace"))[:50_000]
        return {"content": [{"type": "text", "text": f"[exit {proc.returncode}]\n{body}"}]}

    return create_sdk_mcp_server("bash_ro", "0.1.0", tools=[run])
