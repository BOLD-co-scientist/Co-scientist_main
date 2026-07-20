"""Research-agent supervisor entry point.

Runs as a fresh subprocess per *turn*. A turn is one human message plus all the
agentic work the supervisor does in response (dispatching subagents via the SDK
Task tool, reporting back via bus to ``to="human"``), bounded by the existing
checkpoint/interrupt HITL machinery. When the turn finishes the process emits
``session.idle`` and exits.

Conversation continuity across turns is provided by the SDK: each run's CLI
session UUID is captured from the ``ResultMessage`` and persisted via
``settings.write_sdk_session``; the next turn passes it back as ``resume=`` so
the supervisor sees the full prior conversation. See
``docs/plans/R10-resumable-conversations.md``.
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
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk.types import HookMatcher
from scaffold import bus, config_loader, eventlog, hitl, settings, skills, spawn, tools_registry
from scaffold._atomic import read_json


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
    session_id: str, task_text: str, event_state: dict, resume_uuid: str | None = None
) -> ClaudeAgentOptions:
    sup = config_loader.load_supervisor()
    subs = config_loader.list_subagent_roles()

    catalog = spawn.role_catalog_text(subs)
    system_prompt = config_loader.render_prompt(
        sup,
        TASK=task_text,
        SUBAGENT_CATALOG=catalog,
        SESSION_ID=session_id,
        OUTPUT_FORMAT_GUIDANCE=config_loader.output_format_guidance(),  # R13
    )

    mcp_servers, allowed = tools_registry.build_tools(
        session_id, SUPERVISOR_AGENT_ID, sup.tools
    )

    # Register every MCP server any subagent declares. The SDK only wires servers
    # passed at the top level of ClaudeAgentOptions; AgentDefinition.tools merely
    # *filters* those servers per role — it cannot add one. So a subagent-only
    # tool (e.g. py_exec, longjob, ocr) whose server the supervisor doesn't also
    # declare would be absent from the subagent's manifest at runtime ("tool not
    # available"). Union the servers here; per-role gating still happens via each
    # AgentDefinition.tools list built in spawn.build_agent_definitions.
    for _role in subs:
        sub_servers, sub_allowed = tools_registry.build_tools(
            session_id, SUPERVISOR_AGENT_ID, _role.tools
        )
        for _name, _server in sub_servers.items():
            mcp_servers.setdefault(_name, _server)
        for _tool in sub_allowed:
            if _tool not in allowed:
                allowed.append(_tool)

    # R7: let the supervisor crystallize a reusable workflow into a skill
    # (HITL-gated). Always available to the supervisor, independent of role YAML.
    mcp_servers["propose_skill"] = skills.make_propose_skill_server(
        session_id, SUPERVISOR_AGENT_ID
    )
    allowed = list(allowed) + ["mcp__propose_skill"]

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
        # While the human is chatting with us during a checkpoint review, let
        # tool calls through so the agent can answer — don't open a nested
        # checkpoint mid-conversation.
        if event_state.get("suspend_checkpoints"):
            event_state.setdefault("_trace", []).append(f"CHAT_ALLOW:{tool_name}")
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
        # R7: discover this user's saved skills. "all" injects the Skill tool;
        # setting_sources=["project"] scopes discovery to <cwd>/.claude/skills
        # (= the user's root), not the container's ~/.claude.
        skills="all",
        setting_sources=["project"],
        # When resuming, the SDK replays the prior conversation transcript so
        # the supervisor keeps full context across turns/subprocess restarts.
        resume=resume_uuid,
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
        # Capture the SDK/CLI conversation UUID so the next turn can resume.
        # It rides on every ResultMessage; the init SystemMessage carries it
        # too, as a fallback for turns that error before a result.
        sid = getattr(message, "session_id", None)
        if sid:
            event_state["sdk_session_id"] = sid
        elif isinstance(message, SystemMessage):
            sid = (message.data or {}).get("session_id")
            if sid:
                event_state["sdk_session_id"] = sid

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    # The end-of-turn skill reflection asks the supervisor to
                    # reply "NONE" when nothing is reusable; that is internal
                    # bookkeeping, not a message to the human. Suppress reports
                    # during reflection so a bare "NONE" never surfaces as the
                    # supervisor's reply.
                    if not event_state.get("suppress_reports"):
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


async def run_session(
    session_id: str, message: str, resume_uuid: str | None = None
) -> None:
    """Run one conversation turn of the research agent.

    A *turn* is one human ``message`` plus all the agentic work the supervisor
    does in response, bounded by checkpoint/interrupt HITL. The first turn of a
    session passes ``resume_uuid=None``; every follow-up passes the SDK
    conversation UUID persisted by the previous turn so the supervisor keeps
    full context.

    Stops are signalled cross-process via ``hitl.request_stop`` (a flag file)
    rather than an in-memory event, so this can run as a subprocess.
    """
    settings.ensure_session_dirs(session_id)
    is_first = resume_uuid is None
    if is_first:
        eventlog.append(session_id, actor="system", kind="session.start", task=message)
        eventlog.append(
            session_id, actor="system", kind="evolution_live_check", marker="v3"
        )
    else:
        eventlog.append(
            session_id, actor="human", kind="human_directive", text=message
        )
        eventlog.append(
            session_id, actor="system", kind="turn.start", resumed=True
        )

    prior = read_json(settings.sdk_session_file(session_id), default={})
    prior_turns = prior.get("turns", 0) if isinstance(prior, dict) else 0

    event_state = {
        "event_count": 0,
        "last_checkpoint_count": 0,
    }
    if resume_uuid:
        # Fallback so a turn that errors before its first ResultMessage still
        # leaves a resumable UUID behind.
        event_state["sdk_session_id"] = resume_uuid

    options = _build_options(session_id, message, event_state, resume_uuid)

    first_turn = message
    if is_first:
        library_block = _library_listing_block()
        if library_block:
            first_turn = library_block + message

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(first_turn)
            await _turn_loop(client, session_id, event_state)
            if event_state.get("completed_naturally"):
                await _maybe_propose_skill(client, session_id, event_state)
    finally:
        sdk_id = event_state.get("sdk_session_id")
        if sdk_id:
            settings.write_sdk_session(session_id, sdk_id, prior_turns + 1)


async def _maybe_propose_skill(client, session_id: str, event_state: dict) -> None:
    """End-of-turn reflection (R7): after a turn finishes naturally, invite the
    supervisor to crystallize a reusable workflow into a skill via the
    ``propose_skill`` tool (HITL-gated). Guarded so trivial turns don't reflect
    and disable-able via ``settings.SKILL_REFLECTION``."""
    if not settings.SKILL_REFLECTION:
        return
    # Only reflect when the turn did substantive work (>= 2 supervisor tool calls).
    if event_state.get("event_count", 0) < 2:
        return

    existing = skills.list_skills()
    existing_txt = (
        "\n".join(f"- {s['name']}: {s['description']}" for s in existing) or "(none yet)"
    )
    # Suspend the checkpoint gate so this short reflection pass isn't deferred,
    # and suppress human-facing reports so the reflection's "NONE" / narration
    # never surfaces to the human as a supervisor reply.
    event_state["suspend_checkpoints"] = True
    event_state["suppress_reports"] = True
    try:
        await client.query(
            "[Reflection] Before we finish: review the workflow you just completed."
            " If — and ONLY if — it followed a coherent, reusable procedure likely to"
            " help in a FUTURE session (not a one-off), call the `propose_skill` tool"
            " to save it for the human to approve: a short kebab-case `name`, a"
            " one-line `description` of when to use it, and a concise step-by-step"
            " `body`. Do NOT duplicate an existing skill. If nothing is reusable,"
            " reply 'NONE' and call no tool.\n\n"
            f"Existing skills:\n{existing_txt}"
        )
        await _process_message_stream(client, session_id, event_state)
    except Exception as e:
        eventlog.append(
            session_id, actor="system", kind="skill.reflection_error", error=str(e)
        )
    finally:
        event_state["suspend_checkpoints"] = False
        event_state["suppress_reports"] = False


async def _turn_loop(client, session_id: str, event_state: dict) -> None:
    """Drive the supervisor through one turn, pausing at checkpoints/interrupts.

    Returns when the turn is done (natural finish, checkpoint/interrupt that
    ends the turn). Conversation context is preserved by the SDK transcript, so
    "ending a turn" never loses history — the human can always send another
    message to resume.
    """
    while True:
        result = await _process_message_stream(client, session_id, event_state)
        if result is None:
            event_state["completed_naturally"] = True
            eventlog.append(
                session_id, actor="system", kind="research.complete", reason="finished"
            )
            return

        deferred = getattr(result, "deferred_tool_use", None)
        if deferred is None:
            event_state["completed_naturally"] = True
            eventlog.append(
                session_id, actor="system", kind="research.complete", reason="finished"
            )
            return

        # ── Interrupt (Stop button) ─────────────────────────
        if event_state.pop("interrupted", False):
            event_state["deferring"] = False
            eventlog.append(session_id, actor="system", kind="session.interrupted")

            answer = await hitl.ask(
                session_id,
                kind="interrupt_feedback",
                summary=(
                    "Session interrupted. Enter new instructions and"
                    " approve, or reject to end the turn."
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
                return

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

        rid = hitl.open_request(
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
        # Interactive wait: the human can chat with the supervisor before
        # deciding. Approve/deny (button) or stop ends the wait; a chat message
        # is fed to the agent and its reply streams back, without resolving the
        # gate. Checkpoints are suspended so the chat doesn't nest another one.
        event_state["deferring"] = False
        event_state["suspend_checkpoints"] = True
        answer = None
        try:
            while answer is None:
                ans = hitl.poll_answer(session_id, rid)
                if ans is not None:
                    answer = ans
                    break
                if hitl.is_stop_requested(session_id):
                    hitl.clear_stop(session_id)
                    hitl.resolve_request(session_id, rid, "interrupted")
                    answer = {"decision": "interrupted", "note": ""}
                    break
                msgs = hitl.drain_chat(session_id)
                if msgs:
                    for m in msgs:
                        await client.query(
                            "[Human message during checkpoint review]\n"
                            + m.get("text", "")
                        )
                        await _process_message_stream(client, session_id, event_state)
                else:
                    await asyncio.sleep(0.5)
        finally:
            event_state["suspend_checkpoints"] = False

        event_state["last_checkpoint_count"] = count

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
                return

        if decision == "interrupted":
            eventlog.append(
                session_id,
                actor="system",
                kind="research.complete",
                reason="stopped",
            )
            return

        resume_msg = "[Checkpoint approved] Continue working."
        if note:
            resume_msg = f"[Checkpoint approved] [Human feedback] {note}"
        await client.query(resume_msg)


def _short_input(payload: Any) -> str:
    try:
        s = json.dumps(payload, ensure_ascii=False)
    except Exception:
        s = str(payload)
    return s


async def run_turn(
    session_id: str, message: str, resume_uuid: str | None = None
) -> None:
    """Run one conversation turn as a subprocess, then leave the session idle
    and resumable.

    Crashes are logged but never crash the session — ``run_session`` persists
    the SDK conversation UUID in a ``finally`` regardless, so the human can send
    another message to resume. A ``session.idle`` event is always emitted last
    so the API/UI know the turn is over and a follow-up is welcome.
    """
    try:
        await run_session(session_id, message, resume_uuid)
    except asyncio.CancelledError:
        eventlog.append(
            session_id, actor="system", kind="session.end", reason="cancelled"
        )
        raise
    except Exception as e:
        eventlog.append(
            session_id, actor="system", kind="research.crashed", error=str(e)
        )
    finally:
        eventlog.append(session_id, actor="system", kind="session.idle")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    p.add_argument("--task", required=True, help="The human message for this turn.")
    p.add_argument(
        "--resume",
        default=None,
        help="SDK conversation UUID to resume; omit for a session's first turn.",
    )
    args = p.parse_args()
    asyncio.run(run_turn(args.session, args.task, args.resume))


if __name__ == "__main__":
    main()
