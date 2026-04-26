"""Write tool restricted to the session's scratch/results dirs.
Cannot reach researcher_data (read-only mount), other sessions, scaffold, or roles."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import settings


def _allowed_roots(session_id: str) -> list[Path]:
    sd = settings.session_dir(session_id)
    return [(sd / "scratch").resolve(), (sd / "results").resolve()]


def _resolve_safe(session_id: str, p: str) -> Path:
    target = Path(p).expanduser().resolve()
    for root in _allowed_roots(session_id):
        if target == root or root in target.parents:
            return target
    raise PermissionError(f"writes outside scratch/results forbidden: {target}")


def make_server(session_id: str):
    @tool("write", "Write or overwrite a file in the session's scratch or results dir.", {
        "path": str,
        "content": str,
    })
    async def write_(args: dict[str, Any]) -> dict:
        try:
            p = _resolve_safe(session_id, args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        return {"content": [{"type": "text", "text": f"wrote {p} ({len(args['content'])}B)"}]}

    @tool("append", "Append text to a file in the session's scratch or results dir.", {
        "path": str,
        "content": str,
    })
    async def append_(args: dict[str, Any]) -> dict:
        try:
            p = _resolve_safe(session_id, args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(args["content"])
        return {"content": [{"type": "text", "text": f"appended {len(args['content'])}B to {p}"}]}

    return create_sdk_mcp_server("fs_write_workspace", "0.1.0", tools=[write_, append_])
