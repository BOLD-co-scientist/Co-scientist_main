"""FastAPI surface. The UI plugs into this; researchers can also poke it
directly with curl. State is dual-readable from the filesystem under
state/."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from scaffold import bus, config_loader, eventlog, hitl, memory, settings
from . import schemas


app = FastAPI(title="coscientist", version="0.1.0")


# In-process registry of running session tasks. Survives only while server is up.
_RUNNING: dict[str, asyncio.Task] = {}


def _new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


@app.get("/health")
def health():
    return {"ok": True, "root": str(settings.ROOT)}


# ---------- research ----------

@app.post("/research/sessions", response_model=schemas.StartResearchResponse)
async def start_research(req: schemas.StartResearchRequest):
    from research.runtime import run_session

    sid = req.session_id or _new_session_id()
    settings.ensure_session_dirs(sid)
    eventlog.append(sid, actor="api", kind="research.requested", task=req.task)

    task = asyncio.create_task(run_session(sid, req.task))
    _RUNNING[sid] = task

    def _on_done(t: asyncio.Task):
        _RUNNING.pop(sid, None)
        if t.exception():
            eventlog.append(sid, actor="api", kind="research.crashed", error=str(t.exception()))

    task.add_done_callback(_on_done)
    return schemas.StartResearchResponse(session_id=sid, task=req.task)


@app.post("/research/sessions/{sid}/messages")
def post_human_directive(sid: str, body: schemas.HumanDirective):
    if not settings.session_dir(sid).exists():
        raise HTTPException(404, "unknown session")
    msg_id = bus.send(
        sid, sender="human", target="supervisor", kind="human_directive",
        payload={"text": body.text},
    )
    return {"ok": True, "msg_id": msg_id}


# ---------- evolution ----------

@app.post("/evolution/commands", response_model=schemas.StartEvolutionResponse)
async def start_evolution(req: schemas.StartEvolutionRequest):
    from evolution.runtime import run_command

    sid = req.session_id or ("evo-" + _new_session_id())
    settings.ensure_session_dirs(sid)
    eventlog.append(sid, actor="api", kind="evolution.requested", command=req.command)

    task = asyncio.create_task(run_command(sid, req.command))
    _RUNNING[sid] = task

    def _on_done(t: asyncio.Task):
        _RUNNING.pop(sid, None)
        if t.exception():
            eventlog.append(sid, actor="api", kind="evolution.crashed", error=str(t.exception()))

    task.add_done_callback(_on_done)
    return schemas.StartEvolutionResponse(session_id=sid, command=req.command)


# ---------- hitl ----------

@app.get("/hitl/{sid}/pending")
def hitl_pending(sid: str):
    return hitl.list_pending(sid)


@app.post("/hitl/{sid}/{request_id}/answer")
def hitl_answer(sid: str, request_id: str, body: schemas.HitlAnswer):
    try:
        hitl.answer(sid, request_id, body.decision, body.note)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


# ---------- events (SSE) ----------

@app.get("/events/{sid}")
async def events(sid: str, since: str | None = None):
    """Server-Sent Events tail of state/sessions/<sid>/events.jsonl."""
    if not settings.session_dir(sid).exists():
        raise HTTPException(404, "unknown session")

    async def generator():
        last = since
        path = settings.session_dir(sid) / "events.jsonl"
        while True:
            recs = eventlog.tail(sid, since_id=last)
            for rec in recs:
                last = rec["id"]
                yield f"data: {json.dumps(rec)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generator(), media_type="text/event-stream")


# ---------- introspection ----------

@app.get("/roles", response_model=list[schemas.RoleSummary])
def list_roles():
    out: list[schemas.RoleSummary] = []
    sup = config_loader.load_supervisor()
    out.append(schemas.RoleSummary(
        name=sup.name, description=sup.description, tools=sup.tools,
        can_spawn=sup.can_spawn, model=sup.model,
    ))
    for r in config_loader.list_subagent_roles():
        out.append(schemas.RoleSummary(
            name=r.name, description=r.description, tools=r.tools,
            can_spawn=r.can_spawn, model=r.model,
        ))
    return out


@app.get("/memory/{layer}", response_model=schemas.MemorySearchResult)
def search_memory(layer: str, q: str, k: int = 5, sid: str | None = None):
    if layer == "global":
        return schemas.MemorySearchResult(layer="global", hits=memory.recall_global(q, k))
    if layer == "project":
        if not sid:
            raise HTTPException(400, "project memory requires sid")
        return schemas.MemorySearchResult(layer="project", hits=memory.recall_project(sid, q, k))
    raise HTTPException(400, f"unknown layer {layer}")


@app.get("/sessions/{sid}/inbox/{agent}")
def peek_inbox(sid: str, agent: str):
    return bus.peek(sid, agent)
