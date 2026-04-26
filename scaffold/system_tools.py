"""System MCP tools — bus, memory, hitl. Session-scoped via factories."""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from . import bus, eventlog, hitl, memory


def make_bus_server(session_id: str, agent_id: str):
    @tool("send", "Send a message to another agent in this session.", {
        "to": str,
        "kind": str,
        "payload": dict,
    })
    async def send_(args: dict[str, Any]) -> dict[str, Any]:
        msg_id = bus.send(session_id, agent_id, args["to"], args["kind"], args.get("payload", {}))
        return {"content": [{"type": "text", "text": f"sent {msg_id} to {args['to']}"}]}

    @tool("drain", "Drain (pop and archive) all pending messages for this agent.", {})
    async def drain_(args: dict[str, Any]) -> dict[str, Any]:
        msgs = bus.drain(session_id, agent_id)
        if not msgs:
            return {"content": [{"type": "text", "text": "(empty inbox)"}]}
        body = "\n".join(
            f"- [{m['from']}/{m['kind']}] {m.get('payload')}" for m in msgs
        )
        return {"content": [{"type": "text", "text": body}]}

    return create_sdk_mcp_server("bus", "0.1.0", tools=[send_, drain_])


def make_memory_server(session_id: str, agent_id: str):
    @tool("remember_agent", "Save a private note in this agent's memory.", {"text": str})
    async def remember_agent(args):
        rid = memory.remember_agent(session_id, agent_id, args["text"])
        return {"content": [{"type": "text", "text": f"agent-memory:{rid}"}]}

    @tool("remember_project", "Share a finding with the whole research team.", {"text": str})
    async def remember_project(args):
        rid = memory.remember_project(session_id, agent_id, args["text"])
        return {"content": [{"type": "text", "text": f"project-memory:{rid}"}]}

    @tool("remember_global", "Save a long-term lesson learned across sessions.", {"text": str})
    async def remember_global(args):
        rid = memory.remember_global(args["text"], source=f"{session_id}/{agent_id}")
        return {"content": [{"type": "text", "text": f"global-memory:{rid}"}]}

    @tool("recall_project", "Search the shared project memory.", {"query": str, "k": int})
    async def recall_project(args):
        hits = memory.recall_project(session_id, args["query"], args.get("k", 5))
        body = "\n".join(f"- ({h['agent']}) {h['text']}" for h in hits) or "(no matches)"
        return {"content": [{"type": "text", "text": body}]}

    @tool("recall_global", "Search the cross-session long-term memory.", {"query": str, "k": int})
    async def recall_global(args):
        hits = memory.recall_global(args["query"], args.get("k", 5))
        body = "\n".join(f"- {h['text']}" for h in hits) or "(no matches)"
        return {"content": [{"type": "text", "text": body}]}

    return create_sdk_mcp_server(
        "memory",
        "0.1.0",
        tools=[remember_agent, remember_project, remember_global, recall_project, recall_global],
    )


def make_hitl_server(session_id: str, agent_id: str):
    @tool("ask", "Ask the human an open-ended question and wait for the reply.", {
        "summary": str,
        "details": str,
    })
    async def ask_(args):
        rec = await hitl.ask(
            session_id,
            kind="ask",
            summary=args["summary"],
            payload={"details": args.get("details", ""), "asker": agent_id},
        )
        decision = rec.get("decision", "reject")
        note = rec.get("note") or ""
        eventlog.append(session_id, actor=agent_id, kind="hitl.ask.resolved", decision=decision)
        return {"content": [{"type": "text", "text": f"[{decision}] {note}"}]}

    return create_sdk_mcp_server("hitl", "0.1.0", tools=[ask_])
