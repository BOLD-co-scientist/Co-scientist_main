"""Single source of truth: maps role-declared tool names to MCP servers + the
allowed-tools list passed to ClaudeAgentOptions."""
from __future__ import annotations

from typing import Any, Iterable

from . import system_tools


def _import(modpath: str):
    import importlib
    return importlib.import_module(modpath)


def build_tools(session_id: str, agent_id: str, declared: Iterable[str]) -> tuple[dict, list[str]]:
    """Return (mcp_servers, allowed_tools_for_options).

    `declared` is the role.tools list. Names map to MCP server modules; the SDK
    namespaces tools as `mcp__<server>__<tool>`. `Task` is an SDK built-in that
    is auto-allowed when `agents` are passed to ClaudeAgentOptions; including
    it in `declared` is a hint that this role should be allowed to spawn.
    """
    servers: dict[str, Any] = {}
    allowed: list[str] = []

    for name in declared:
        if name == "Task":
            # Built-in. We surface it in allowed via "Task".
            allowed.append("Task")
            continue
        if name == "bus":
            servers["bus"] = system_tools.make_bus_server(session_id, agent_id)
            allowed.append("mcp__bus")
        elif name == "memory":
            servers["memory"] = system_tools.make_memory_server(session_id, agent_id)
            allowed.append("mcp__memory")
        elif name == "hitl":
            servers["hitl"] = system_tools.make_hitl_server(session_id, agent_id)
            allowed.append("mcp__hitl")
        elif name == "fs_read":
            mod = _import("tools.fs_read.server")
            servers["fs_read"] = mod.make_server(session_id)
            allowed.append("mcp__fs_read")
        elif name == "fs_write_workspace":
            mod = _import("tools.fs_write_workspace.server")
            servers["fs_write_workspace"] = mod.make_server(session_id)
            allowed.append("mcp__fs_write_workspace")
        elif name == "py_exec":
            mod = _import("tools.py_exec.server")
            servers["py_exec"] = mod.make_server(session_id)
            allowed.append("mcp__py_exec")
        elif name == "deep_research":
            mod = _import("tools.deep_research.server")
            servers["deep_research"] = mod.make_server(session_id)
            allowed.append("mcp__deep_research")
        else:
            # Unknown tool name. Try a generic loader: tools/<name>/server.py:make_server.
            try:
                mod = _import(f"tools.{name}.server")
                servers[name] = mod.make_server(session_id)
                allowed.append(f"mcp__{name}")
            except Exception:
                # Skip silently — missing tool means the evolution agent
                # named one that doesn't exist yet. Surface in eventlog.
                from . import eventlog
                eventlog.append(session_id, actor=agent_id, kind="tool.missing", name=name)
    return servers, allowed
