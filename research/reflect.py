"""R16 evolution reflection runner.

Read-only. After a research turn completes cleanly, look at the session's
trajectory + the current harness inventory and — only when warranted — propose
harness evolutions the human can pick from. This is the reflect→propose arm of a
human-gated self-improvement loop; it changes nothing itself.

Runs as a short-lived subprocess kicked by ``api.server`` on clean completion
(``python -m research.reflect --session <sid>``). Writes
``state/sessions/<sid>/reflection.json`` and emits a ``reflection.ready`` event
with the proposal count (0 → the UI shows no reminder).

See docs/plans/R16-evo-reflection-trigger.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

from scaffold import config_loader, eventlog, settings
from scaffold._atomic import read_json, write_json

REFLECT_AGENT_ID = "reflection"
_TRAJECTORY_MAX = 200  # cap events fed to the model


def _extract_task(events: list[dict]) -> str:
    """The session's original task plus any follow-up human messages, so a
    multi-turn session reflects on the full ask, not just the first turn."""
    msgs: list[str] = []
    for e in events:
        if e.get("kind") == "research.requested" and e.get("task"):
            msgs.append(str(e["task"]))
        elif e.get("kind") == "message.received" and e.get("text"):
            msgs.append(str(e["text"]))
    if not msgs:
        return "(task not recorded)"
    if len(msgs) == 1:
        return msgs[0]
    return msgs[0] + "\n\nFollow-up messages:\n" + "\n".join(f"- {m}" for m in msgs[1:])


def _extract_outcome(events: list[dict]) -> str:
    """Prefer the last research.complete summary; else the last reply to human."""
    outcome = ""
    for e in events:
        k = e.get("kind")
        if k == "research.complete":
            outcome = str(e.get("summary") or e.get("reason") or "completed")
        elif k == "bus.send" and e.get("target") == "human":
            payload = e.get("payload")
            t = payload.get("text") if isinstance(payload, dict) else None
            if t:
                outcome = str(t)
    return outcome or "(outcome not recorded)"


def _count_toolcalls(events: list[dict]) -> int:
    return sum(1 for e in events if e.get("kind") == "tool.use")


def _trajectory(events: list[dict]) -> str:
    """A compact numbered trace: actor + kind + the most informative field."""
    lines: list[str] = []
    for e in events[-_TRAJECTORY_MAX:]:
        k = e.get("kind", "?")
        actor = e.get("actor", "?")
        detail = ""
        for f in ("task", "summary", "note", "text", "tool", "input_summary", "error", "target", "reason"):
            v = e.get(f)
            if isinstance(v, str) and v.strip():
                detail = v.strip().replace("\n", " ")
                break
        if len(detail) > 160:
            detail = detail[:159] + "…"
        lines.append(f"{len(lines) + 1}. [{actor}] {k}{(': ' + detail) if detail else ''}")
    return "\n".join(lines) if lines else "(no events)"


def _harness_inventory() -> str:
    parts: list[str] = []
    roles: list[str] = []
    if (settings.ROOT / "roles" / "supervisor.yaml").exists():
        roles.append("supervisor")
    subdir = settings.ROOT / "roles" / "subagents"
    if subdir.exists():
        roles += [p.stem for p in sorted(subdir.glob("*.yaml"))]
    parts.append("Roles: " + (", ".join(roles) if roles else "(none)"))

    tdir = settings.ROOT / "tools"
    tools = (
        [p.name for p in sorted(tdir.iterdir()) if p.is_dir() and not p.name.startswith("__")]
        if tdir.exists()
        else []
    )
    parts.append("Tools: " + (", ".join(tools) if tools else "(none)"))

    pdir = settings.ROOT / "prompts"
    prompts = [p.name for p in sorted(pdir.glob("*.md"))] if pdir.exists() else []
    parts.append("Prompts: " + (", ".join(prompts) if prompts else "(none)"))
    return "\n".join(parts)


def _previous_block(prev: dict | None) -> str:
    """Render the prior reflection (this session) for the prompt, so a new pass
    builds on it instead of re-proposing the same things."""
    if not prev:
        return "(none — this is the first reflection for this session)"
    refl = str(prev.get("reflection", "")).strip()
    lines = [
        f"- {p.get('title', '(untitled)')} [{p.get('direction', '?')}]"
        for p in (prev.get("proposals") or [])
        if isinstance(p, dict)
    ]
    out = "Previous reflection:\n" + (refl or "(none)")
    if lines:
        out += (
            "\n\nProposals made last time (re-surface any still unaddressed in the "
            "inventory and note the recurrence in the rationale; only drop ones now "
            "implemented):\n"
            + "\n".join(lines)
        )
    return out


def _parse_json(text: str) -> dict:
    """Tolerant: strip code fences, then take the outermost {...}."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        t = t[a : b + 1]
    return json.loads(t)


async def _call_model(prompt: str) -> str:
    options = ClaudeAgentOptions(
        system_prompt=prompt,
        model=settings.MODEL_REFLECT,
        allowed_tools=[],
        max_turns=1,
        cwd=str(settings.ROOT),
    )
    out: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query("Reflect on the session now. Output only the JSON object.")
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        out.append(block.text)
    return "".join(out).strip()


async def reflect(session_id: str) -> None:
    if not settings.EVO_REFLECT:
        return
    settings.ensure_session_dirs(session_id)
    out_path = settings.session_dir(session_id) / "reflection.json"
    events = eventlog.tail(session_id)
    prev = read_json(out_path, default=None)
    prev = prev if isinstance(prev, dict) else None

    # Deliverable gate: nothing to reflect on until the session has produced a
    # result at least once (a clarifying-question turn ends idle without one).
    if not any(e.get("kind") == "research.complete" for e in events):
        return

    # Substantial-new-work gate: only (re)reflect when this turn added enough NEW
    # tool calls since the last reflection — so chatty turns don't burn a call or
    # clobber a good prior reflection. First pass: prev count is 0 → delta = total.
    current_tc = _count_toolcalls(events)
    prev_tc = int(prev.get("toolcalls_at_reflection", 0)) if prev else 0
    if current_tc - prev_tc < settings.EVO_REFLECT_MIN_TOOLCALLS:
        if prev is None:
            write_json(out_path, {
                "reflection": "Trivial session; reflection skipped.",
                "proposals": [], "skipped": True, "toolcalls_at_reflection": current_tc,
            })
        return  # keep the prior reflection untouched

    prompt = config_loader.render(
        (settings.ROOT / "prompts" / "evo_reflect.md").read_text(encoding="utf-8"),
        TASK=_extract_task(events),
        OUTCOME=_extract_outcome(events),
        TRAJECTORY=_trajectory(events),
        HARNESS_INVENTORY=_harness_inventory(),
        PREVIOUS_REFLECTION=_previous_block(prev),
    )
    try:
        data = _parse_json(await _call_model(prompt))
        proposals = data.get("proposals") or []
        if not isinstance(proposals, list):
            proposals = []
        result = {
            "reflection": str(data.get("reflection", "")).strip(),
            "proposals": proposals,
            "toolcalls_at_reflection": current_tc,
        }
    except Exception as e:  # noqa: BLE001 — a failed reflection must never crash the flow
        if prev and (prev.get("proposals") or prev.get("reflection")):
            return  # don't clobber a good prior reflection on a transient failure
        result = {"reflection": f"(reflection failed: {e})", "proposals": [], "error": str(e), "toolcalls_at_reflection": current_tc}

    write_json(out_path, result)
    eventlog.append(
        session_id, actor=REFLECT_AGENT_ID, kind="reflection.ready",
        proposal_count=len(result.get("proposals") or []),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--session", required=True)
    args = p.parse_args()
    asyncio.run(reflect(args.session))


if __name__ == "__main__":
    main()
