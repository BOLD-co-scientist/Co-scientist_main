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


async def run_command(session_id: str, command: str, base: str | None = None) -> None:
    settings.ensure_session_dirs(session_id)
    sandbox.ensure_repo()
    wt = sandbox.create(_slugify(command), base=base)
    # Diverge the branch from its base right away so it appears as an in-flight
    # child node in the evolution graph even if the agent makes no commits.
    sandbox.mark_start(wt, f"evolution (in progress): {command}")
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
    p.add_argument("--base", default=None, help="commit sha/ref to branch the worktree from")
    # R19: the API passes --scope for platform-scope evolutions (editing the
    # shared ui3/ + api/ instead of this tenant's harness). Accepted here so the
    # flag can never crash the process on argparse; the staging build + tested
    # cutover it requires are R19 Phase 4 and are NOT implemented, so a platform
    # launch refuses loudly instead of half-running against the wrong repo.
    p.add_argument("--scope", default="harness", choices=("harness", "platform"))
    args = p.parse_args()
    if args.scope == "platform":
        eventlog.append(
            args.session, actor=EVOLUTION_AGENT_ID, kind="evolution.error",
            error=("Platform-scope evolution (editing the shared UI/API) is not implemented yet: it "
                   "needs the staging gate and the host-side cutover from R19 Phase 4. Set "
                   "COSCIENTIST_PLATFORM_EVOLUTION=0 (the default) — the API refuses these with 501."),
        )
        raise SystemExit(2)
    asyncio.run(run_command(args.session, args.command, base=args.base))


if __name__ == "__main__":
    main()
