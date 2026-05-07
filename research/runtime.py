"""Research-agent supervisor entry point.

Run as a long-lived task per session. Drains the supervisor's bus inbox between
turns to receive human directives mid-session, dispatches subagents via the
SDK Task tool, emits reports back via bus to `to="human"`.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import json
import os
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from scaffold import bus, config_loader, eventlog, settings, spawn, tools_registry


SUPERVISOR_AGENT_ID = "supervisor"


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _library_listing_block() -> str:
    """Render a short, agent-readable listing of state/library/ for prepending
    to the supervisor's first turn. Empty string if library is empty/missing."""
    settings.ensure_runtime_dirs()
    entries: list[tuple[str, int, float]] = []
    for p in settings.LIBRARY.iterdir():
        if p.name.startswith(".") or p.name == ".staging":
            continue
        try:
            stat = p.stat()
            entries.append((p.name, stat.st_size, stat.st_mtime))
        except OSError:
            entries.append((p.name + " (broken symlink)", 0, 0.0))
    if not entries:
        return ""
    entries.sort(key=lambda e: e[2], reverse=True)
    lines = [f"- {name} ({_format_size(size)}, mtime {_dt.datetime.fromtimestamp(mt).isoformat(timespec='seconds')})"
             if mt else f"- {name}"
             for name, size, mt in entries]
    return (
        "Files currently in the user's library (state/library/, read via fs_read):\n"
        + "\n".join(lines)
        + "\n\nUser task:\n"
    )


def _build_options(session_id: str, task_text: str) -> ClaudeAgentOptions:
    sup = config_loader.load_supervisor()
    subs = config_loader.list_subagent_roles()

    catalog = spawn.role_catalog_text(subs)
    system_prompt = config_loader.render_prompt(
        sup, TASK=task_text, SUBAGENT_CATALOG=catalog, SESSION_ID=session_id
    )

    mcp_servers, allowed = tools_registry.build_tools(
        session_id, SUPERVISOR_AGENT_ID, sup.tools
    )

    agents = spawn.build_agent_definitions(subs)

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=sup.model,
        mcp_servers=mcp_servers,
        allowed_tools=allowed,
        agents=agents,
        max_turns=settings.MAX_TURNS,
        permission_mode="acceptEdits",
        cwd=str(settings.ROOT),
    )


def _summarize_blocks(msg) -> str:
    """Human-readable line for an SDK message."""
    if isinstance(msg, AssistantMessage):
        parts = []
        for block in msg.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
            elif isinstance(block, ToolUseBlock):
                parts.append(f"[tool:{block.name}]")
        return " ".join(parts).strip()
    if isinstance(msg, ResultMessage):
        return f"[result {getattr(msg, 'subtype', '')}]"
    return ""


async def run_session(session_id: str, task_text: str) -> None:
    settings.ensure_session_dirs(session_id)
    eventlog.append(
        session_id, actor="system", kind="session.start", task=task_text
    )

    options = _build_options(session_id, task_text)

    library_block = _library_listing_block()
    first_turn = (library_block + task_text) if library_block else task_text

    async with ClaudeSDKClient(options=options) as client:
        await client.query(first_turn)
        async for message in client.receive_response():
            text = _summarize_blocks(message)
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        # Auto-mirror narrative text as a report on the bus.
                        bus.send(
                            session_id,
                            sender=SUPERVISOR_AGENT_ID,
                            target="human",
                            kind="report",
                            payload={"text": block.text},
                        )
                    elif isinstance(block, ToolUseBlock):
                        eventlog.append(
                            session_id,
                            actor=SUPERVISOR_AGENT_ID,
                            kind="tool.use",
                            tool=block.name,
                            input_summary=_short_input(block.input),
                        )
            elif isinstance(message, ResultMessage):
                eventlog.append(
                    session_id,
                    actor="system",
                    kind="session.turn_result",
                    summary=text,
                )

    eventlog.append(session_id, actor="system", kind="session.end")


def _short_input(payload: Any) -> str:
    try:
        s = json.dumps(payload, ensure_ascii=False)
    except Exception:
        s = str(payload)
    return s[:200] + ("…" if len(s) > 200 else "")


async def follow_session(session_id: str, task_text: str) -> None:
    """Outer loop: process one task, then poll the supervisor inbox for new
    human_directive messages and continue until the human signals completion
    or budget is exhausted.

    For v0 we run a single Claude session per directive; multi-directive
    continuation across a single Claude session is a follow-up improvement.
    """
    await run_session(session_id, task_text)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--task", required=True)
    args = p.parse_args()
    asyncio.run(follow_session(args.session, args.task))


if __name__ == "__main__":
    main()
