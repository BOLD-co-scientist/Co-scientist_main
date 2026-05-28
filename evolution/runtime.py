"""Evolution-agent entry point. Single fresh process per command. Self-edits
are safe because the running process never patches itself in-flight — the
*next* invocation reads the new code from the bind-mounted source."""

from __future__ import annotations

import argparse
import asyncio
import re

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    ToolUseBlock,
)
from evolution.tools import bash_ro, bash_sandbox, edit, propose_merge, run_tests
from scaffold import config_loader, eventlog, sandbox, settings, system_tools
from scaffold._atomic import write_json


EVOLUTION_AGENT_ID = "evolution"


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "evolve"


def _build_options(
    session_id: str, command: str, wt: sandbox.Worktree
) -> ClaudeAgentOptions:
    prompt_template = (settings.ROOT / "evolution" / "evolution_prompt.md").read_text(
        encoding="utf-8"
    )
    system_prompt = config_loader.render(
        prompt_template,
        WORKTREE_PATH=str(wt.path),
        WORKTREE_BRANCH=wt.branch,
        COMMAND=command,
    )

    mcp_servers = {
        "edit": edit.make_server(wt),
        "bash_ro": bash_ro.make_server(),
        "bash_sandbox": bash_sandbox.make_server(wt),
        "run_tests": run_tests.make_server(wt),
        "propose_merge": propose_merge.make_server(session_id, wt),
        "memory": system_tools.make_memory_server(session_id, EVOLUTION_AGENT_ID),
    }
    allowed_tools = [
        "mcp__edit",
        "mcp__bash_ro",
        "mcp__bash_sandbox",
        "mcp__run_tests",
        "mcp__propose_merge",
        "mcp__memory",
    ]

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=settings.MODEL_EVOLUTION,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        max_turns=settings.MAX_TURNS,
        permission_mode="acceptEdits",
        cwd=str(wt.path),
    )


async def run_command(session_id: str, command: str) -> None:
    settings.ensure_session_dirs(session_id)
    sandbox.ensure_repo()
    wt = sandbox.create(_slugify(command))
    eventlog.append(
        session_id,
        actor=EVOLUTION_AGENT_ID,
        kind="evolution.start",
        command=command,
        worktree=str(wt.path),
        branch=wt.branch,
    )

    options = _build_options(session_id, command, wt)
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(command)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            eventlog.append(
                                session_id,
                                actor=EVOLUTION_AGENT_ID,
                                kind="evolution.note",
                                text=block.text,
                            )
                        elif isinstance(block, ToolUseBlock):
                            eventlog.append(
                                session_id,
                                actor=EVOLUTION_AGENT_ID,
                                kind="evolution.tool",
                                tool=block.name,
                            )
    except Exception as e:
        eventlog.append(
            session_id, actor=EVOLUTION_AGENT_ID, kind="evolution.error", error=str(e)
        )
        # Worktree may still exist; leave it for human inspection.
        raise
    finally:
        eventlog.append(session_id, actor=EVOLUTION_AGENT_ID, kind="evolution.end")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--command", required=True)
    args = p.parse_args()
    asyncio.run(run_command(args.session, args.command))


if __name__ == "__main__":
    main()
