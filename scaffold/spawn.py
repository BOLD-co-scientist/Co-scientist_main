"""Builds AgentDefinition objects from role YAML files. The supervisor's
ClaudeAgentOptions(agents=...) lets the SDK's Task tool dispatch by role name.

The supervisor's MCP servers are session-scoped and shared with all subagents.
Per-role tool gating is enforced by AgentDefinition.tools, not by separate
servers. v0 trade-off: bus messages from a subagent are tagged with the
supervisor's id; subagents communicate primarily via Task return values.
"""
from __future__ import annotations

from claude_agent_sdk import AgentDefinition

from . import config_loader, settings


def build_agent_definitions(role_listing: list[config_loader.RoleConfig]) -> dict[str, AgentDefinition]:
    """Convert subagent RoleConfigs into the dict ClaudeAgentOptions(agents=...)."""
    agents: dict[str, AgentDefinition] = {}
    for role in role_listing:
        prompt = role.prompt_path.read_text(encoding="utf-8")
        tool_aliases = _resolve_tool_aliases(role.tools)
        agents[role.name] = AgentDefinition(
            description=role.description or f"{role.name} subagent",
            prompt=prompt,
            tools=tool_aliases,
            model=role.model if role.model else None,
        )
    return agents


def _resolve_tool_aliases(declared: list[str]) -> list[str]:
    """Convert role-config tool names to SDK allowed-tool aliases.

    Mirrors tools_registry.build_tools but emits names only — server objects
    are constructed once at the supervisor level and shared.
    """
    out: list[str] = []
    for name in declared:
        if name == "Task":
            out.append("Task")
        elif name in {"bus", "memory", "hitl", "fs_read", "fs_write_workspace", "py_exec"}:
            out.append(f"mcp__{name}")
        else:
            out.append(f"mcp__{name}")
    return out


def role_catalog_text(roles: list[config_loader.RoleConfig]) -> str:
    """Markdown catalog of available subagents to embed in the supervisor's prompt."""
    if not roles:
        return "(no subagents available)"
    lines = []
    for r in roles:
        spawn_note = " [can spawn subagents]" if r.can_spawn else ""
        desc = r.description or "(no description)"
        lines.append(f"- **{r.name}**{spawn_note}: {desc}")
    return "\n".join(lines)
