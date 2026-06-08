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
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk.types import HookMatcher
from scaffold import bus, config_loader, eventlog, hitl, settings, spawn, tools_registry


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
    lines = [
        (
            f"- {name} ({_format_size(size)}, mtime {_dt.datetime.fromtimestamp(mt).isoformat(timespec='seconds')})"
            if mt
            else f"- {name}"
        )
        for name, size, mt in entries
    ]
    return (
        "Files currently in the user's library (state/library/, read via fs_read):\n"
        + "\n".join(lines)
        + "\n\nUser task:\n"
    )


def _build_options(
    session_id: str, task_text: str, event_state: dict
) -> ClaudeAgentOptions:
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

    _DEFER = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "defer",
        },
    }
    _ALLOW = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        },
    }

    async def _checkpoint_gate(hook_input, tool_use_id, context):
        tool_name = hook_input.get("tool_name", "?")
        agent_id = hook_input.get("agent_id")
        if agent_id:
            event_state.setdefault("_trace", []).append(
                f"SKIP:{tool_name}(agent={agent_id})"
            )
            return _ALLOW
        if event_state.get("deferring"):
            event_state.setdefault("_trace", []).append(f"BATCH_DEFER:{tool_name}")
            return _DEFER

        # --- Stop button: interrupt at the next supervisor tool call ---
        # Cross-process: the stop signal is a flag file, not an in-memory
        # event, so the API server can stop us while we run as a subprocess.
        if hitl.is_stop_requested(session_id):
            hitl.clear_stop(session_id)
            event_state["deferring"] = True
            event_state["interrupted"] = True
            event_state.setdefault("_trace", []).append(f"INTERRUPT:{tool_name}")
            return _DEFER

        event_state["event_count"] += 1
        event_state.setdefault("_trace", []).append(
            f"{event_state['event_count']}:{tool_name}"
        )
        delta = event_state["event_count"] - event_state["last_checkpoint_count"]
        if delta >= settings.CHECKPOINT_EVENT_INTERVAL:
            event_state["deferring"] = True
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "defer",
                    "permissionDecisionReason": (
                        f"Event checkpoint reached ({event_state['event_count']} events). "
                        "Pausing for human review."
                    ),
                },
            }
        return _ALLOW

    hooks = {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[_checkpoint_gate])],
    }

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=sup.model,
        mcp_servers=mcp_servers,
        allowed_tools=allowed,
        agents=agents,
        max_turns=settings.MAX_TURNS,
        permission_mode="acceptEdits",
        cwd=str(settings.ROOT),
        hooks=hooks,
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


async def _process_message_stream(client, session_id: str, event_state: dict):
    """Drain one response stream.

    Event counting is handled by the PreToolUse hook — this function
    only needs to log messages and forward reports.
    """
    result = None

    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    bus.send(
                        session_id,
                        sender=SUPERVISOR_AGENT_ID,
                        target="human",
                        kind="report",
                        payload={"text": block.text},
                    )
                    event_state["last_report_text"] = block.text
                elif isinstance(block, ToolUseBlock):
                    eventlog.append(
                        session_id,
                        actor=SUPERVISOR_AGENT_ID,
                        kind="tool.use",
                        tool=block.name,
                        input_summary=_short_input(block.input),
                    )
        elif isinstance(message, ResultMessage):
            result = message
            eventlog.append(
                session_id,
                actor="system",
                kind="session.turn_result",
                summary=_summarize_blocks(message),
            )

    return result


async def run_session(session_id: str, task_text: str) -> bool:
    """Run the research agent.

    Returns ``True`` when research finishes normally (the evolution loop
    should follow).  Returns ``False`` when the human interrupted and
    chose *not* to continue — the caller should skip the evolution loop
    and end the session.

    Stops are signalled cross-process via ``hitl.request_stop`` (a flag
    file) rather than an in-memory event, so this can run as a subprocess.
    """
    settings.ensure_session_dirs(session_id)
    eventlog.append(session_id, actor="system", kind="session.start", task=task_text)
    eventlog.append(session_id, actor="system", kind="evolution_live_check", marker="v3")

    event_state = {
        "event_count": 0,
        "last_checkpoint_count": 0,
    }

    options = _build_options(session_id, task_text, event_state)

    library_block = _library_listing_block()
    first_turn = (library_block + task_text) if library_block else task_text

    async with ClaudeSDKClient(options=options) as client:
        await client.query(first_turn)

        while True:
            result = await _process_message_stream(client, session_id, event_state)
            if result is None:
                break

            deferred = getattr(result, "deferred_tool_use", None)
            if deferred is None:
                break

            # ── Interrupt (Stop button) ─────────────────────────
            if event_state.pop("interrupted", False):
                event_state["deferring"] = False
                eventlog.append(session_id, actor="system", kind="session.interrupted")

                answer = await hitl.ask(
                    session_id,
                    kind="interrupt_feedback",
                    summary=(
                        "Session interrupted. Enter new instructions and"
                        " approve, or reject to end the session."
                    ),
                    payload={},
                )

                decision = answer.get("decision", "reject")
                new_instructions = (answer.get("note") or "").strip()

                if decision in ("reject", "interrupted"):
                    eventlog.append(
                        session_id,
                        actor="system",
                        kind="research.complete",
                        reason="stopped",
                    )
                    return False

                if new_instructions:
                    eventlog.append(
                        session_id,
                        actor="human",
                        kind="human_directive",
                        text=new_instructions,
                    )
                    await client.query(
                        f"[Human interrupted with new instructions]\n{new_instructions}"
                    )
                else:
                    await client.query("[Resuming after pause]")
                continue

            # ── Checkpoint ──────────────────────────────────────
            count = event_state["event_count"]
            trace = event_state.pop("_trace", [])
            eventlog.append(
                session_id,
                actor=SUPERVISOR_AGENT_ID,
                kind="checkpoint.triggered",
                event_count=count,
                deferred_tool=deferred.name,
                hook_trace=trace,
            )

            await client.query(
                "[Checkpoint] Summarize progress by role, then state next steps."
                " Do NOT call any tools — just reply with text.\n"
                "Format:\n"
                "**Supervisor:** what you have done directly (planning, synthesis, communications).\n"
                "**Subagents:** for each subagent launched, list its role, task, status, and key output.\n"
                "**Next steps:** what you plan to do or delegate next."
            )
            summary_result = await _process_message_stream(
                client, session_id, event_state
            )
            summary_text = event_state.pop("last_report_text", "")

            answer = await hitl.ask(
                session_id,
                kind="checkpoint",
                summary=f"Checkpoint ({count} events).",
                payload={
                    "event_count": count,
                    "agent_summary": summary_text,
                    "deferred_tool": deferred.name,
                    "deferred_input": _short_input(deferred.input),
                },
            )

            event_state["last_checkpoint_count"] = count
            event_state["deferring"] = False

            decision = answer.get("decision", "reject")
            note = answer.get("note") or ""
            eventlog.append(
                session_id,
                actor="human",
                kind="checkpoint.resolved",
                decision=decision,
                note=note,
            )

            if decision == "reject":
                if note:
                    await client.query(f"[Session terminated by human] {note}")
                else:
                    eventlog.append(
                        session_id,
                        actor="system",
                        kind="research.complete",
                        reason="checkpoint_rejected",
                    )
                    return True

            if decision == "interrupted":
                eventlog.append(
                    session_id,
                    actor="system",
                    kind="research.complete",
                    reason="stopped",
                )
                return False

            resume_msg = "[Checkpoint approved] Continue working."
            if note:
                resume_msg = f"[Checkpoint approved] [Human feedback] {note}"
            await client.query(resume_msg)

    eventlog.append(session_id, actor="system", kind="research.complete")
    return True


def _short_input(payload: Any) -> str:
    try:
        s = json.dumps(payload, ensure_ascii=False)
    except Exception:
        s = str(payload)
    return s


async def follow_session(session_id: str, task_text: str) -> None:
    """Run research, then enter an evolution loop.

    A stop is requested via ``hitl.request_stop(session_id)`` (a flag file).
    The PreToolUse hook inside ``run_session`` checks it and defers the next
    tool call, so the human can redirect research without losing context.
    """
    try:
        await _follow_session_inner(session_id, task_text)
    except asyncio.CancelledError:
        eventlog.append(
            session_id, actor="system", kind="session.end", reason="cancelled"
        )
        raise


async def _follow_session_inner(session_id: str, task_text: str) -> None:
    try:
        completed = await run_session(session_id, task_text)
    except Exception as e:
        eventlog.append(
            session_id, actor="system", kind="research.crashed", error=str(e)
        )
        completed = True

    if not completed:
        eventlog.append(
            session_id, actor="system", kind="session.end", reason="stopped"
        )
        return

    while True:
        answer = await hitl.ask(
            session_id,
            kind="evolution_prompt",
            summary=(
                "Research phase finished. Enter an evolution command and"
                " approve, or reject to end the session."
            ),
            payload={},
        )

        decision = answer.get("decision", "reject")
        command = (answer.get("note") or "").strip()

        if decision in ("reject", "interrupted") or not command:
            break

        from evolution.runtime import run_command

        eventlog.append(
            session_id, actor="human", kind="evolution.requested", command=command
        )
        try:
            await run_command(session_id, command)
        except Exception as e:
            eventlog.append(
                session_id, actor="system", kind="evolution.crashed", error=str(e)
            )

    eventlog.append(session_id, actor="system", kind="session.end")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--task", required=True)
    args = p.parse_args()
    asyncio.run(follow_session(args.session, args.task))


if __name__ == "__main__":
    main()
