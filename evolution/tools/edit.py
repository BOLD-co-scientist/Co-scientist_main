"""File-edit tool restricted to a worktree. Path validation enforced here,
not in the prompt."""
from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import sandbox


def make_server(wt: sandbox.Worktree):
    def _check(p: str) -> Path:
        target = (wt.path / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
        if not sandbox.in_worktree(wt, target):
            raise PermissionError(f"path outside worktree: {target}")
        return target

    @tool("view", "Show a file with line numbers (or list a directory).", {
        "path": str,
        "offset": int,
        "limit": int,
    })
    async def view(args):
        try:
            p = _check(args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        if not p.exists():
            return {"content": [{"type": "text", "text": f"ERROR: not found {p}"}], "isError": True}
        if p.is_dir():
            entries = [(("d" if c.is_dir() else "f"), c.name) for c in sorted(p.iterdir())]
            text = "\n".join(f"{k} {n}" for k, n in entries) or "(empty)"
            return {"content": [{"type": "text", "text": text}]}
        offset = int(args.get("offset", 1))
        limit = int(args.get("limit", 2000))
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, offset - 1)
        end = min(len(lines), start + limit)
        body = "\n".join(f"{i+1:6}\t{lines[i]}" for i in range(start, end))
        return {"content": [{"type": "text", "text": body or "(empty file)"}]}

    @tool("create", "Create a new file with the given content. Fails if file exists.", {
        "path": str,
        "content": str,
    })
    async def create(args):
        try:
            p = _check(args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        if p.exists():
            return {"content": [{"type": "text", "text": f"ERROR: exists {p}"}], "isError": True}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        return {"content": [{"type": "text", "text": f"created {p}"}]}

    @tool("str_replace", "Replace exactly one occurrence of `old` with `new` in a file.", {
        "path": str,
        "old": str,
        "new": str,
    })
    async def str_replace(args):
        try:
            p = _check(args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        text = p.read_text(encoding="utf-8")
        count = text.count(args["old"])
        if count == 0:
            return {"content": [{"type": "text", "text": "ERROR: `old` not found"}], "isError": True}
        if count > 1:
            return {"content": [{"type": "text", "text": f"ERROR: `old` matches {count} times; make it unique"}], "isError": True}
        p.write_text(text.replace(args["old"], args["new"]), encoding="utf-8")
        return {"content": [{"type": "text", "text": f"replaced in {p}"}]}

    @tool("insert", "Insert text after the given 1-based line number.", {
        "path": str,
        "after_line": int,
        "content": str,
    })
    async def insert(args):
        try:
            p = _check(args["path"])
        except PermissionError as e:
            return {"content": [{"type": "text", "text": f"ERROR: {e}"}], "isError": True}
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        idx = max(0, min(len(lines), int(args["after_line"])))
        new_text = args["content"]
        if not new_text.endswith("\n"):
            new_text += "\n"
        lines.insert(idx, new_text)
        p.write_text("".join(lines), encoding="utf-8")
        return {"content": [{"type": "text", "text": f"inserted at line {idx} of {p}"}]}

    return create_sdk_mcp_server("edit", "0.1.0", tools=[view, create, str_replace, insert])
