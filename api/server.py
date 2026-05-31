"""FastAPI surface. The UI plugs into this; researchers can also poke it
directly with curl. State is dual-readable from the filesystem under
state/."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import datetime
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from scaffold import bus, config_loader, eventlog, hitl, memory, settings

from . import schemas


app = FastAPI(title="coscientist", version="0.1.0")


# In-process registry of running session tasks. Survives only while server is up.
_RUNNING: dict[str, asyncio.Task] = {}
_STOP_EVENTS: dict[str, asyncio.Event] = {}


def _new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


@app.get("/health")
def health():
    return {"ok": True, "root": str(settings.ROOT)}


# Make sure global runtime dirs exist before the first request lands.
settings.ensure_runtime_dirs()


# ---------- research ----------


@app.post("/research/sessions", response_model=schemas.StartResearchResponse)
async def start_research(req: schemas.StartResearchRequest):
    from research.runtime import follow_session

    sid = req.session_id or _new_session_id()
    settings.ensure_session_dirs(sid)
    eventlog.append(sid, actor="human", kind="research.requested", task=req.task)

    stop_event = asyncio.Event()
    _STOP_EVENTS[sid] = stop_event

    task = asyncio.create_task(follow_session(sid, req.task, stop_event))
    _RUNNING[sid] = task

    def _on_done(t: asyncio.Task):
        _RUNNING.pop(sid, None)
        _STOP_EVENTS.pop(sid, None)
        if t.cancelled():
            return
        if t.exception():
            eventlog.append(
                sid, actor="system", kind="session.crashed", error=str(t.exception())
            )

    task.add_done_callback(_on_done)
    return schemas.StartResearchResponse(session_id=sid, task=req.task)


@app.post("/sessions/{sid}/stop")
async def stop_session(sid: str):
    event = _STOP_EVENTS.get(sid)
    if event is None:
        raise HTTPException(404, "no running task for this session")
    event.set()
    hitl.reject_all_pending(sid)
    return {"ok": True}


@app.post("/research/sessions/{sid}/messages")
def post_human_directive(sid: str, body: schemas.HumanDirective):
    if not settings.session_dir(sid).exists():
        raise HTTPException(404, "unknown session")
    msg_id = bus.send(
        sid,
        sender="human",
        target="supervisor",
        kind="human_directive",
        payload={"text": body.text},
    )
    return {"ok": True, "msg_id": msg_id}


# ---------- library ----------

_LIBRARY_CHUNK = 1024 * 1024  # 1 MB streaming chunks


def _safe_library_name(raw: str) -> str:
    # Strip path separators and leading dots; refuse empty.
    name = Path(raw).name.lstrip(".")
    if not name or name in {".staging"}:
        raise HTTPException(400, f"invalid filename: {raw!r}")
    return name


def _append_library_event(kind: str, **fields) -> None:
    settings.LIBRARY_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "kind": kind, **fields}
    with open(settings.LIBRARY_EVENTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


@app.post("/library/files", response_model=schemas.LibraryUploadResponse)
async def upload_library_file(file: UploadFile = File(...)):
    """Stream a file into state/library/ via .staging/<uuid> then atomic rename.
    Overwrites on name collision (last-write-wins)."""
    settings.ensure_runtime_dirs()
    name = _safe_library_name(file.filename or "")
    final_path = settings.LIBRARY / name
    overwrote = final_path.exists()

    staging_path = settings.LIBRARY_STAGING / f"{uuid.uuid4().hex}.part"
    written = 0
    try:
        with open(staging_path, "wb") as out:
            while True:
                chunk = await file.read(_LIBRARY_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.LIBRARY_MAX_BYTES:
                    raise HTTPException(
                        413,
                        f"file exceeds LIBRARY_MAX_BYTES ({settings.LIBRARY_MAX_BYTES} bytes)",
                    )
                out.write(chunk)
        os.replace(staging_path, final_path)
    except HTTPException:
        staging_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(500, f"upload failed: {e}")

    _append_library_event(
        "library.upload", name=name, size=written, overwrote=overwrote
    )
    return schemas.LibraryUploadResponse(name=name, size=written, overwrote=overwrote)


@app.get("/library/files", response_model=list[schemas.LibraryFile])
def list_library_files():
    settings.ensure_runtime_dirs()
    out: list[schemas.LibraryFile] = []
    for p in settings.LIBRARY.iterdir():
        if p.name.startswith(".") or p.name == ".staging":
            continue
        try:
            stat = p.stat()  # follows symlinks; broken symlinks raise OSError
        except OSError:
            # Broken symlink — surface in /library/health, but keep it visible
            # in the listing with size=0 so users notice.
            try:
                size = 0
                mtime = p.lstat().st_mtime
                out.append(schemas.LibraryFile(name=p.name, size=size, mtime=mtime))
            except OSError:
                pass
            continue
        if stat.st_size == 0 and p.is_dir():
            continue
        out.append(
            schemas.LibraryFile(name=p.name, size=stat.st_size, mtime=stat.st_mtime)
        )
    out.sort(key=lambda f: f.mtime, reverse=True)
    return out


@app.delete("/library/files/{name}")
def delete_library_file(name: str):
    safe = _safe_library_name(name)
    target = settings.LIBRARY / safe
    if not target.exists() and not target.is_symlink():
        raise HTTPException(404, f"no such library file: {safe}")
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as e:
        raise HTTPException(500, f"delete failed: {e}")
    _append_library_event("library.delete", name=safe)
    return {"ok": True, "name": safe}


@app.get("/library/health", response_model=schemas.LibraryHealth)
def library_health():
    settings.ensure_runtime_dirs()
    file_count = 0
    total_bytes = 0
    broken: list[dict[str, str]] = []
    for p in settings.LIBRARY.iterdir():
        if p.name.startswith(".") or p.name == ".staging":
            continue
        if p.is_symlink():
            try:
                target = os.readlink(p)
            except OSError:
                target = "?"
            if not p.exists():  # broken symlink (target unreachable)
                broken.append({"name": p.name, "target": str(target)})
                continue
        try:
            stat = p.stat()
        except OSError:
            broken.append({"name": p.name, "target": "?"})
            continue
        file_count += 1
        total_bytes += stat.st_size

    staging_files = 0
    if settings.LIBRARY_STAGING.exists():
        staging_files = sum(1 for _ in settings.LIBRARY_STAGING.iterdir())

    disk_free = shutil.disk_usage(settings.STATE).free

    return schemas.LibraryHealth(
        file_count=file_count,
        total_bytes=total_bytes,
        disk_free_bytes=disk_free,
        staging_files=staging_files,
        broken_symlinks=broken,
    )


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
    out.append(
        schemas.RoleSummary(
            name=sup.name,
            description=sup.description,
            tools=sup.tools,
            can_spawn=sup.can_spawn,
            model=sup.model,
        )
    )
    for r in config_loader.list_subagent_roles():
        out.append(
            schemas.RoleSummary(
                name=r.name,
                description=r.description,
                tools=r.tools,
                can_spawn=r.can_spawn,
                model=r.model,
            )
        )
    return out


@app.get("/memory/{layer}", response_model=schemas.MemorySearchResult)
def search_memory(layer: str, q: str, k: int = 5, sid: str | None = None):
    if layer == "global":
        return schemas.MemorySearchResult(
            layer="global", hits=memory.recall_global(q, k)
        )
    if layer == "project":
        if not sid:
            raise HTTPException(400, "project memory requires sid")
        return schemas.MemorySearchResult(
            layer="project", hits=memory.recall_project(sid, q, k)
        )
    raise HTTPException(400, f"unknown layer {layer}")


@app.get("/sessions/{sid}/inbox/{agent}")
def peek_inbox(sid: str, agent: str):
    return bus.peek(sid, agent)
