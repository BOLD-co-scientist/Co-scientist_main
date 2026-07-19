"""Deep-research MCP tools (R14).

``run``   — blocking scope call: submit + poll to completion, return the report.
            The supervisor is mandated to call this once per session, after it
            understands the problem statement and BEFORE implementing anything.
``start`` — fire-and-forget background research (the "run in background on
            request" capability); returns a research_id immediately.
``status``/``fetch``/``cancel``/``list`` — manage in-flight/finished runs.

Background runs are durable handles under ``state/sessions/<sid>/deep_research/``
so they outlive the per-turn supervisor subprocess. See
docs/plans/R14-deep-research.md.
"""
from __future__ import annotations

import asyncio
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import eventlog, hitl, settings
from . import client as dr_client
from . import store


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def _err(s: str) -> dict:
    return {**_text(s), "isError": True}


def _resp_error(resp: Any) -> str:
    e = getattr(resp, "error", None)
    if e is None:
        return "run failed"
    return getattr(e, "message", None) or str(e)


def _is_verification_error(msg: str) -> bool:
    """OpenAI's deep-research models are gated behind org verification; the run
    fails mid-flight with this class of message. Detect it so we can fall back."""
    m = (msg or "").lower()
    return "must be verified" in m or "verify organization" in m or (
        "organization" in m and "verif" in m
    )


def _finalize(session_id: str, handle: dict, resp: Any) -> dict:
    """Write the finished report to the workdir and stamp the handle."""
    report, citations = dr_client.extract_report(resp)
    from pathlib import Path

    rp = Path(handle["report_path"])
    rp.parent.mkdir(parents=True, exist_ok=True)
    body = report or "(the deep-research run returned no text)"
    if citations:
        body += "\n\n## Sources\n" + "\n".join(f"- {u}" for u in citations)
    if handle.get("fell_back"):
        banner = (
            f"> ⚠️ Produced with the fallback model `{handle['model']}` "
            f"(web-search-grounded), NOT OpenAI Deep Research. The intended model "
            f"`{handle.get('primary_model')}` is gated behind OpenAI organization "
            f"verification. Verify the org at "
            f"platform.openai.com/settings/organization/general to get true "
            f"deep-research runs.\n\n"
        )
        body = banner + body
    rp.write_text(body, encoding="utf-8")
    handle["state"] = store.COMPLETED
    handle["n_citations"] = len(citations)
    store.write_handle(session_id, handle)
    eventlog.append(session_id, actor="system", kind="deep_research.completed",
                    research_id=handle["research_id"], n_citations=len(citations),
                    fell_back=bool(handle.get("fell_back")), model=handle["model"])
    return handle


async def _fallback(session_id: str, handle: dict, errmsg: str, fb_model: str) -> dict:
    """Resubmit a verification-failed run once with the fallback model."""
    eventlog.append(session_id, actor="system", kind="deep_research.fallback",
                    research_id=handle["research_id"],
                    from_model=handle["model"], to_model=fb_model,
                    reason="org_not_verified")
    try:
        response_id = await dr_client.submit(handle["query"], handle["instructions"], fb_model)
    except Exception as e:
        handle["state"] = store.FAILED
        handle["error"] = f"deep-research model failed ({errmsg}); fallback to {fb_model} also failed: {e}"
        store.write_handle(session_id, handle)
        return handle
    handle["fell_back"] = True
    handle["fallback_reason"] = errmsg
    handle["primary_model"] = handle["model"]
    handle["model"] = fb_model
    handle["response_id"] = response_id
    handle["state"] = store.RUNNING
    handle["error"] = None
    store.write_handle(session_id, handle)
    return handle


async def _advance(session_id: str, handle: dict) -> dict:
    """Poll OpenAI once and persist any state change; finalize on completion.
    On a verification failure, retry once with the fallback model."""
    if handle["state"] in store.TERMINAL or not handle.get("response_id"):
        return handle
    try:
        state, resp = await dr_client.poll(handle["response_id"])
    except dr_client.DeepResearchUnavailable as e:
        handle["state"] = store.FAILED
        handle["error"] = str(e)
        store.write_handle(session_id, handle)
        return handle
    except Exception as e:
        handle["state"] = store.FAILED
        handle["error"] = f"poll error: {e}"
        store.write_handle(session_id, handle)
        return handle
    if state == store.COMPLETED:
        return _finalize(session_id, handle, resp)
    if state == store.FAILED:
        errmsg = _resp_error(resp)
        fb = settings.DEEPRESEARCH_FALLBACK_MODEL
        if (fb and not handle.get("fell_back") and handle["model"] != fb
                and _is_verification_error(errmsg)):
            return await _fallback(session_id, handle, errmsg, fb)
        handle["state"] = store.FAILED
        handle["error"] = errmsg
        store.write_handle(session_id, handle)
        return handle
    if state != handle["state"]:
        handle["state"] = state
        store.write_handle(session_id, handle)
    return handle


async def _submit_run(session_id: str, query: str, instructions: str,
                      model: str, mode: str) -> dict | None:
    """Create a handle and launch a background OpenAI run. Returns the handle,
    or None with the error already surfaced to the caller via the handle."""
    handle = store.make_handle(session_id, query, model, instructions, mode)
    eventlog.append(session_id, actor="system", kind="deep_research.started",
                    research_id=handle["research_id"], mode=mode, model=model,
                    query=query[:200])

    # Optional HITL gate (off by default so the mandated scope call is automatic).
    if settings.DEEPRESEARCH_HITL:
        decision = await hitl.ask(
            session_id, kind="deep_research_submit",
            summary=f"Run OpenAI deep research ({model}): {query[:160]}",
            payload={"research_id": handle["research_id"], "query": query, "model": model},
        )
        if decision.get("decision") != "approve":
            handle["state"] = store.CANCELLED
            handle["error"] = "declined by human"
            store.write_handle(session_id, handle)
            return handle

    try:
        response_id = await dr_client.submit(query, instructions, model)
    except dr_client.DeepResearchUnavailable as e:
        handle["state"] = store.FAILED
        handle["error"] = str(e)
        store.write_handle(session_id, handle)
        return handle
    except Exception as e:
        handle["state"] = store.FAILED
        handle["error"] = f"submit error: {e}"
        store.write_handle(session_id, handle)
        return handle

    handle["response_id"] = response_id
    handle["state"] = store.RUNNING
    store.write_handle(session_id, handle)
    return handle


def make_tools(session_id: str):
    @tool(
        "run",
        "Run OpenAI deep research on a question and BLOCK until the report is "
        "ready (polls in the background). Use this for the mandatory once-per-"
        "session problem-scoping pass, before you implement or dispatch build "
        "work. Returns the full report text.",
        {"query": str, "instructions": str, "model": str, "max_wait_s": int},
    )
    async def run_(args: dict[str, Any]) -> dict:
        query = (args.get("query") or "").strip()
        if not query:
            return _err("ERROR: query is required")
        model = (args.get("model") or settings.DEEPRESEARCH_MODEL)
        instructions = args.get("instructions") or ""
        max_wait = int(args.get("max_wait_s") or settings.DEEPRESEARCH_MAX_WAIT_S)

        handle = await _submit_run(session_id, query, instructions, model, "scope")
        if handle["state"] in store.TERMINAL:  # submit failed / declined
            return _err(f"deep_research could not start: {handle.get('error')}")

        waited = 0
        while waited < max_wait:
            handle = await _advance(session_id, handle)
            if handle["state"] in store.TERMINAL:
                break
            await asyncio.sleep(settings.DEEPRESEARCH_POLL_S)
            waited += settings.DEEPRESEARCH_POLL_S

        rid = handle["research_id"]
        if handle["state"] == store.COMPLETED:
            from pathlib import Path
            report = Path(handle["report_path"]).read_text(encoding="utf-8")
            tag = (f" [FALLBACK model {handle['model']} — org not verified for "
                   f"deep research]") if handle.get("fell_back") else ""
            return _text(
                f"Deep research complete (research_id={rid}, model={handle['model']}, "
                f"{handle['n_citations']} sources){tag}. Full report saved to "
                f"{handle['report_path']}.\n\n{report}"
            )
        if handle["state"] == store.FAILED:
            return _err(f"deep research failed (research_id={rid}): {handle.get('error')}")
        # Still running past max_wait: hand back a pollable id. Do NOT start
        # implementing until you have the report — poll status/fetch.
        return _text(
            f"Deep research is still running after {max_wait}s "
            f"(research_id={rid}). It runs asynchronously — poll with "
            f"`deep_research.status` and read it with `deep_research.fetch` when "
            f"done. Wait for the report before implementing."
        )

    @tool(
        "start",
        "Start an OpenAI deep-research run in the BACKGROUND and return a "
        "research_id immediately (does not block). Use when the human asks to "
        "kick off research to work on other things meanwhile. Poll with status; "
        "read with fetch.",
        {"query": str, "instructions": str, "model": str},
    )
    async def start(args: dict[str, Any]) -> dict:
        query = (args.get("query") or "").strip()
        if not query:
            return _err("ERROR: query is required")
        model = (args.get("model") or settings.DEEPRESEARCH_MODEL)
        instructions = args.get("instructions") or ""
        handle = await _submit_run(session_id, query, instructions, model, "background")
        if handle["state"] in store.TERMINAL:
            return _err(f"deep_research could not start: {handle.get('error')}")
        return _text(
            f"STARTED background deep research {handle['research_id']} "
            f"(model={model}). Poll with `deep_research.status`; read with "
            f"`deep_research.fetch` when it reports completed."
        )

    @tool("status", "Check a deep-research run's state (running/completed/failed/cancelled).",
          {"research_id": str})
    async def status(args: dict[str, Any]) -> dict:
        h = store.read_handle(session_id, args.get("research_id", ""))
        if h is None:
            return _err("ERROR: unknown research_id")
        h = await _advance(session_id, h)
        extra = ""
        if h["state"] == store.COMPLETED:
            extra = f" ({h['n_citations']} sources; fetch to read)"
        elif h["state"] == store.FAILED:
            extra = f" — {h.get('error')}"
        return _text(f"deep research {h['research_id']}: {h['state']}{extra}")

    @tool("fetch", "Return the finished report for a completed deep-research run.",
          {"research_id": str})
    async def fetch(args: dict[str, Any]) -> dict:
        h = store.read_handle(session_id, args.get("research_id", ""))
        if h is None:
            return _err("ERROR: unknown research_id")
        h = await _advance(session_id, h)
        if h["state"] != store.COMPLETED:
            return _text(
                f"deep research {h['research_id']} is {h['state']}, not ready. "
                "Poll `deep_research.status` until completed."
            )
        from pathlib import Path
        report = Path(h["report_path"]).read_text(encoding="utf-8")
        return _text(
            f"Report for {h['research_id']} ({h['n_citations']} sources), saved "
            f"at {h['report_path']}:\n\n{report}"
        )

    @tool("cancel", "Cancel an in-flight deep-research run.", {"research_id": str})
    async def cancel(args: dict[str, Any]) -> dict:
        h = store.read_handle(session_id, args.get("research_id", ""))
        if h is None:
            return _err("ERROR: unknown research_id")
        if h["state"] in store.TERMINAL:
            return _text(f"deep research {h['research_id']} already {h['state']}")
        if h.get("response_id"):
            await dr_client.cancel(h["response_id"])
        h["state"] = store.CANCELLED
        store.write_handle(session_id, h)
        eventlog.append(session_id, actor="system", kind="deep_research.cancelled",
                        research_id=h["research_id"])
        return _text(f"cancelled {h['research_id']}")

    @tool("list", "List this session's deep-research runs and their states.", {})
    async def list_(args: dict[str, Any]) -> dict:
        handles = store.list_handles(session_id)
        if not handles:
            return _text("(no deep-research runs this session)")
        lines = [
            f"- {h['research_id']}: {h['state']} [{h.get('mode', '?')}] — {h['query'][:80]}"
            for h in handles
        ]
        return _text("Deep-research runs:\n" + "\n".join(lines))

    return [run_, start, status, fetch, cancel, list_]


def make_server(session_id: str):
    return create_sdk_mcp_server("deep_research", "0.1.0", tools=make_tools(session_id))
