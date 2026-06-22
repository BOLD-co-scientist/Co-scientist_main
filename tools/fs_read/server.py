"""Read-only filesystem tool. Restricted to researcher_data/ and the session
scratch + results dirs. The host filesystem is unreachable inside Docker."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import settings


def _allowed_roots(session_id: str) -> list[Path]:
    sd = settings.session_dir(session_id)
    return [
        settings.RESEARCHER_DATA.resolve(),
        # User's persistent context library — populated via UI uploads or host-drop.
        settings.LIBRARY.resolve(),
        (sd / "scratch").resolve(),
        (sd / "results").resolve(),
        (sd / "memory").resolve(),
    ]


def _candidate_paths(session_id: str, p: str) -> list[Path]:
    raw = Path(p).expanduser()
    sd = settings.session_dir(session_id)
    candidates: list[Path] = []
    if not raw.is_absolute():
        parts = raw.parts
        if parts and parts[0] in {"scratch", "results", "memory"}:
            candidates.append(sd / raw)
        candidates.append(settings.ROOT / raw)
    candidates.append(raw)
    return [candidate.resolve() for candidate in candidates]


def _resolve_safe(session_id: str, p: str) -> Path:
    for target in _candidate_paths(session_id, p):
        for root in _allowed_roots(session_id):
            if target == root or root in target.parents:
                return target
    raise PermissionError(f"path outside allowed roots: {target}")


def make_server(session_id: str):
    @tool("list", "List files under a directory inside researcher_data or session workspace.", {"path": str})
    async def list_(args: dict[str, Any]) -> dict:
        try:
            p = _resolve_safe(session_id, args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        if not p.exists():
            return {"content": [{"type": "text", "text": f"ERROR: not found {p}"}], "isError": True}
        if p.is_file():
            return {"content": [{"type": "text", "text": f"file: {p.name} ({p.stat().st_size}B)"}]}
        items = []
        for child in sorted(p.iterdir()):
            kind = "d" if child.is_dir() else "f"
            size = child.stat().st_size if child.is_file() else "-"
            items.append(f"{kind} {child.name}\t{size}")
        return {"content": [{"type": "text", "text": "\n".join(items) or "(empty)"}]}

    @tool("read", "Read a text file (max 200KB). Use offset/limit for large files.", {
        "path": str,
        "offset": int,
        "limit": int,
    })
    async def read_(args: dict[str, Any]) -> dict:
        try:
            p = _resolve_safe(session_id, args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        if not p.exists() or not p.is_file():
            return {"content": [{"type": "text", "text": f"ERROR: not a file {p}"}], "isError": True}
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", 200_000))
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        chunk = text[offset : offset + limit]
        return {"content": [{"type": "text", "text": chunk}]}

    return create_sdk_mcp_server("fs_read", "0.1.0", tools=[list_, read_])
