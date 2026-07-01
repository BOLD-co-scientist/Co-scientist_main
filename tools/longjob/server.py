"""Long-job runner MCP tools (R12). submit → durable async handle; poll with
status/wait; logs/cancel/fetch. submit is HITL-gated because a job spends real,
shared, accountable resources. Backend-agnostic — see backends/. Docs:
docs/plans/R12-longjob-runner.md."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import eventlog, hitl
from . import job as jobmod
from .backends import base
from .backends.local import LocalBackend

_TERMINAL = {base.DONE, base.FAILED}


def _get_backend(name: str):
    if name == "local":
        return LocalBackend()
    # Phase 2 backends land here once the dispatch-security fork is decided.
    raise ValueError(
        f"backend {name!r} is not available yet — only 'local' exists in R12 "
        "Phase 1 (flair-docker / arm64-hpc are stubbed pending the dispatch "
        "decision and R11 recon)."
    )


def _advance(session_id: str, handle: dict) -> dict:
    """Poll the backend and persist any state change. Returns the handle."""
    if handle["state"] in _TERMINAL or handle.get("backend_ref") is None:
        return handle
    try:
        backend = _get_backend(handle["backend"])
        state, rc = backend.poll(handle["backend_ref"], handle["workdir"])
    except Exception as e:
        handle["state"] = base.UNREACHABLE
        handle["error"] = str(e)
        jobmod.write_handle(session_id, handle)
        return handle
    if state != handle["state"] or rc != handle.get("rc"):
        handle["state"] = state
        handle["rc"] = rc
        jobmod.write_handle(session_id, handle)
    return handle


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def make_tools(session_id: str):
    @tool(
        "submit",
        "Dispatch a long-running (CPU or GPU) job asynchronously and return a "
        "job_id immediately. Use this instead of py_exec for anything that may "
        "exceed ~60s. The human approves the resource request before it runs.",
        {"command": str, "backend": str, "cpu": int, "gpu": int,
         "walltime_min": int, "mem_mb": int, "image": str},
    )
    async def submit(args: dict[str, Any]) -> dict:
        cmd = (args.get("command") or "").strip()
        if not cmd:
            return {**_text("ERROR: command is required"), "isError": True}
        spec = jobmod.JobSpec(
            command=cmd,
            backend=(args.get("backend") or "local"),
            cpu=int(args.get("cpu") or 1),
            gpu=int(args.get("gpu") or 0),
            walltime_min=int(args.get("walltime_min") or 60),
            mem_mb=int(args.get("mem_mb") or 2048),
            image=(args.get("image") or ""),
        )
        try:
            backend = _get_backend(spec.backend)
        except ValueError as e:
            return {**_text(f"ERROR: {e}"), "isError": True}

        handle = jobmod.make_handle(session_id, spec)
        eventlog.append(session_id, actor="system", kind="longjob.proposed",
                        job_id=handle["job_id"], summary=spec.summary())

        decision = await hitl.ask(
            session_id,
            kind="longjob_submit",
            summary=f"Dispatch long job: {spec.summary()}",
            payload={"job_id": handle["job_id"], "command": cmd,
                     "spec": handle["spec"]},
        )
        if decision.get("decision") != "approve":
            handle["state"] = "rejected"
            jobmod.write_handle(session_id, handle)
            eventlog.append(session_id, actor="system", kind="longjob.rejected",
                            job_id=handle["job_id"])
            note = (decision.get("note") or "").strip()
            return _text(f"Job not submitted." + (f" Feedback: {note}" if note else ""))

        try:
            ref = backend.submit(spec, handle["workdir"])
        except Exception as e:
            handle["state"] = base.FAILED
            handle["error"] = str(e)
            jobmod.write_handle(session_id, handle)
            return {**_text(f"ERROR launching job: {e}"), "isError": True}
        handle["backend_ref"] = ref
        handle["state"] = base.RUNNING
        jobmod.write_handle(session_id, handle)
        eventlog.append(session_id, actor="system", kind="longjob.submitted",
                        job_id=handle["job_id"], backend=spec.backend)
        return _text(f"SUBMITTED job {handle['job_id']} (backend={spec.backend}). "
                     f"Poll with status/wait; results in {handle['workdir']}.")

    @tool("status", "Check a long job's state (queued/running/done/failed/unreachable).",
          {"job_id": str})
    async def status(args: dict[str, Any]) -> dict:
        h = jobmod.read_handle(session_id, args.get("job_id", ""))
        if h is None:
            return {**_text("ERROR: unknown job_id"), "isError": True}
        h = _advance(session_id, h)
        rc = h.get("rc")
        return _text(f"job {h['job_id']}: {h['state']}" + (f" (rc={rc})" if rc is not None else ""))

    @tool("wait", "Poll a long job until it finishes or timeout_s elapses; returns its state.",
          {"job_id": str, "timeout_s": int})
    async def wait(args: dict[str, Any]) -> dict:
        jid = args.get("job_id", "")
        h = jobmod.read_handle(session_id, jid)
        if h is None:
            return {**_text("ERROR: unknown job_id"), "isError": True}
        deadline = int(args.get("timeout_s") or 60)
        waited = 0
        while waited < deadline:
            h = _advance(session_id, h)
            if h["state"] in _TERMINAL:
                break
            await asyncio.sleep(2)
            waited += 2
        rc = h.get("rc")
        return _text(f"job {h['job_id']}: {h['state']}" + (f" (rc={rc})" if rc is not None else ""))

    @tool("logs", "Fetch stdout/stderr tail of a long job.", {"job_id": str, "tail": int})
    async def logs(args: dict[str, Any]) -> dict:
        h = jobmod.read_handle(session_id, args.get("job_id", ""))
        if h is None:
            return {**_text("ERROR: unknown job_id"), "isError": True}
        try:
            backend = _get_backend(h["backend"])
            text = backend.logs(h.get("backend_ref") or {}, h["workdir"], int(args.get("tail") or 200))
        except Exception as e:
            return {**_text(f"ERROR: {e}"), "isError": True}
        return _text(text or "(no output yet)")

    @tool("cancel", "Cancel a running long job.", {"job_id": str})
    async def cancel(args: dict[str, Any]) -> dict:
        h = jobmod.read_handle(session_id, args.get("job_id", ""))
        if h is None:
            return {**_text("ERROR: unknown job_id"), "isError": True}
        try:
            _get_backend(h["backend"]).cancel(h.get("backend_ref") or {}, h["workdir"])
        except Exception as e:
            return {**_text(f"ERROR: {e}"), "isError": True}
        h["state"] = "cancelled"
        jobmod.write_handle(session_id, h)
        eventlog.append(session_id, actor="system", kind="longjob.cancelled", job_id=h["job_id"])
        return _text(f"cancelled {h['job_id']}")

    @tool("fetch", "List a finished job's output files (in its workdir) for reading via fs_read.",
          {"job_id": str})
    async def fetch(args: dict[str, Any]) -> dict:
        h = jobmod.read_handle(session_id, args.get("job_id", ""))
        if h is None:
            return {**_text("ERROR: unknown job_id"), "isError": True}
        wd = Path(h["workdir"])
        bookkeeping = {"run.sh", "rc", "stdout.log", "stderr.log"}
        files = [str(p.relative_to(wd)) for p in sorted(wd.rglob("*"))
                 if p.is_file() and p.name not in bookkeeping]
        listing = "\n".join(f"- {f}" for f in files) or "(no output files)"
        return _text(f"job {h['job_id']} outputs under {wd}:\n{listing}")

    return [submit, status, wait, logs, cancel, fetch]


def make_server(session_id: str):
    return create_sdk_mcp_server("longjob", "0.1.0", tools=make_tools(session_id))
