"""FastAPI surface. The shared API authenticates users, then runs each agent
session from that user's private harness root."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
import uuid

import yaml
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from scaffold import sandbox, settings
from scaffold._atomic import append_jsonl, read_json, write_json

from . import schemas
from .auth import User, require_user
from .tenancy import UserContext, context_for


app = FastAPI(title="coscientist", version="0.1.0")


# Registry of running session subprocesses. Survives only while server is up.
# Keyed by (user_id, session_id), because session ids are only unique inside a
# user root.
_RUNNING: dict[tuple[str, str], asyncio.subprocess.Process] = {}


def _new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _ctx(user: User = Depends(require_user)) -> UserContext:
    try:
        return context_for(user)
    except Exception as e:
        raise HTTPException(500, f"failed to prepare user harness: {e}")


def _safe_sid(raw: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", raw or ""):
        raise HTTPException(400, f"invalid session id: {raw!r}")
    return raw


def _runtime_env(ctx: UserContext) -> dict[str, str]:
    """Environment for a runtime subprocess rooted at the user's harness."""
    env = dict(os.environ)
    root = str(ctx.root)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (os.pathsep + existing if existing else "")
    env["COSCIENTIST_ROOT"] = root
    # Per-user SDK transcript dir on the persistent state mount, so a later turn
    # can resume the conversation (R10) and transcripts never leak across users.
    # Overrides any CLAUDE_CONFIG_DIR inherited from the shared API process.
    env["CLAUDE_CONFIG_DIR"] = str(ctx.state / ".claude")
    user_key = _read_user_agent_key(ctx)
    if user_key:
        env["ANTHROPIC_API_KEY"] = user_key
    return env


def _agent_key_path(ctx: UserContext) -> Path:
    return ctx.state / "secrets" / "anthropic_api_key"


def _read_user_agent_key(ctx: UserContext) -> str | None:
    path = _agent_key_path(ctx)
    if not path.exists():
        return None
    key = path.read_text(encoding="utf-8").strip()
    return key or None


def _write_user_agent_key(ctx: UserContext, api_key: str) -> None:
    key = api_key.strip()
    if not key:
        raise HTTPException(400, "api_key must not be empty")
    path = _agent_key_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _ensure_session_dirs(ctx: UserContext, sid: str) -> Path:
    sd = ctx.session_dir(sid)
    for sub in (
        "inbox",
        "status",
        "control",
        "hitl/pending",
        "hitl/answered",
        "memory/agent",
        "memory/project",
        "results",
        "scratch",
    ):
        (sd / sub).mkdir(parents=True, exist_ok=True)
    return sd


def _append_event(ctx: UserContext, sid: str, actor: str, kind: str, **fields: Any) -> str:
    eid = uuid.uuid4().hex[:12]
    record = {
        "id": eid,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": sid,
        "actor": actor,
        "kind": kind,
        **fields,
    }
    append_jsonl(ctx.session_dir(sid) / "events.jsonl", record)
    return eid


def _tail_events(ctx: UserContext, sid: str, since_id: str | None = None) -> list[dict]:
    path = ctx.session_dir(sid) / "events.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    found = since_id is None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if found:
                out.append(rec)
            elif rec.get("id") == since_id:
                found = True
    return out


def _session_exists(ctx: UserContext, sid: str) -> bool:
    return ctx.session_dir(sid).exists()


def _stop_flag(ctx: UserContext, sid: str) -> Path:
    return ctx.session_dir(sid) / "control" / "stop"


def _clear_stop(ctx: UserContext, sid: str) -> None:
    _stop_flag(ctx, sid).unlink(missing_ok=True)


def _request_stop(ctx: UserContext, sid: str) -> None:
    p = _stop_flag(ctx, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def _set_autonomous(ctx: UserContext, sid: str) -> None:
    """Tenant-scoped mirror of ``hitl.set_autonomous`` (see scaffold/hitl.py)."""
    p = ctx.session_dir(sid) / "control" / "autonomous"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


def _pending_dir(ctx: UserContext, sid: str) -> Path:
    return ctx.session_dir(sid) / "hitl" / "pending"


def _answered_dir(ctx: UserContext, sid: str) -> Path:
    return ctx.session_dir(sid) / "hitl" / "answered"


def _reject_all_pending(ctx: UserContext, sid: str) -> None:
    pending = _pending_dir(ctx, sid)
    if not pending.exists():
        return
    for path in sorted(pending.glob("*.json")):
        rec = read_json(path, {})
        rid = rec.get("id", path.stem)
        rec["decision"] = "reject"
        rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rec["note"] = "stopped"
        write_json(_answered_dir(ctx, sid) / f"{rid}.json", rec)
        path.unlink(missing_ok=True)
        _append_event(ctx, sid, actor="system", kind="hitl.answer", ref=rid, decision="reject")


def _monitor_key(ctx: UserContext, sid: str) -> tuple[str, str]:
    return (ctx.user.user_id, sid)


async def _monitor_runtime(
    ctx: UserContext, sid: str, proc: "asyncio.subprocess.Process", log_fh
) -> None:
    """Await a runtime subprocess, then clean up and log abnormal exits."""
    try:
        rc = await proc.wait()
    finally:
        _RUNNING.pop(_monitor_key(ctx, sid), None)
        _clear_stop(ctx, sid)
        try:
            log_fh.close()
        except Exception:
            pass
    if rc != 0:
        _append_event(
            ctx,
            sid,
            actor="system",
            kind="session.crashed",
            error=f"runtime exited with code {rc}; see state/sessions/{sid}/runtime.log",
        )


def _read_sdk_session(ctx: UserContext, sid: str) -> str | None:
    """Return the persisted SDK conversation UUID for this session, or None.

    The runtime subprocess persists it (via ``settings.write_sdk_session``) under
    the user's own session dir, so a later turn can resume the same SDK
    conversation. Tenant-scoped mirror of ``settings.read_sdk_session``."""
    rec = read_json(ctx.session_dir(sid) / "sdk_session.json", default=None)
    if isinstance(rec, dict):
        return rec.get("sdk_session_id") or None
    return None


async def _spawn_research(
    ctx: UserContext, sid: str, message: str, resume_uuid: str | None = None
) -> None:
    """Spawn the research runtime as a fresh subprocess for one turn.

    The first turn passes ``resume_uuid=None``; every follow-up passes the SDK
    conversation UUID persisted by the previous turn so the supervisor keeps full
    context. A fresh subprocess per turn keeps the self-modification invariant
    (an approved evolution merge is picked up by the next turn without restarting
    the server). stdout+stderr land in runtime.log for diagnosis."""
    log_fh = open(ctx.session_dir(sid) / "runtime.log", "ab")
    argv = [
        sys.executable,
        "-m",
        "research.runtime",
        "--session",
        sid,
        "--task",
        message,
    ]
    if resume_uuid:
        argv += ["--resume", resume_uuid]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(ctx.root),
        env=_runtime_env(ctx),
        stdout=log_fh,
        stderr=log_fh,
    )
    _RUNNING[_monitor_key(ctx, sid)] = proc
    asyncio.create_task(_monitor_runtime(ctx, sid, proc, log_fh))


@app.get("/health")
def health():
    return {"ok": True, "root": str(settings.ROOT)}


@app.get("/auth/me", response_model=schemas.AuthMe)
def auth_me(ctx: UserContext = Depends(_ctx)):
    return schemas.AuthMe(
        user_id=ctx.user.user_id,
        display_name=ctx.user.display_name,
        root=str(ctx.root),
    )


@app.get("/auth/agent-key", response_model=schemas.AgentKeyStatus)
def agent_key_status(ctx: UserContext = Depends(_ctx)):
    return schemas.AgentKeyStatus(
        has_custom_key=_read_user_agent_key(ctx) is not None,
        default_available=bool(os.environ.get("ANTHROPIC_API_KEY")),
    )


@app.put("/auth/agent-key", response_model=schemas.AgentKeyStatus)
def set_agent_key(body: schemas.AgentKeyUpdate, ctx: UserContext = Depends(_ctx)):
    _write_user_agent_key(ctx, body.api_key)
    return agent_key_status(ctx)


@app.delete("/auth/agent-key", response_model=schemas.AgentKeyStatus)
def clear_agent_key(ctx: UserContext = Depends(_ctx)):
    _agent_key_path(ctx).unlink(missing_ok=True)
    return agent_key_status(ctx)


# ---------- research ----------


@app.post("/research/sessions", response_model=schemas.StartResearchResponse)
async def start_research(req: schemas.StartResearchRequest, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(req.session_id or _new_session_id())
    _ensure_session_dirs(ctx, sid)
    if req.autonomous:
        _set_autonomous(ctx, sid)
        _append_event(ctx, sid, actor="human", kind="session.autonomous")
    _append_event(ctx, sid, actor="human", kind="research.requested", task=req.task)
    _clear_stop(ctx, sid)

    # Spawn the first turn (resume_uuid=None).
    await _spawn_research(ctx, sid, req.task)
    return schemas.StartResearchResponse(session_id=sid, task=req.task)


@app.post("/sessions/{sid}/stop")
async def stop_session(sid: str, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(sid)
    if _monitor_key(ctx, sid) not in _RUNNING:
        raise HTTPException(404, "no running task for this session")
    _request_stop(ctx, sid)
    _reject_all_pending(ctx, sid)
    return {"ok": True}


@app.post("/research/sessions/{sid}/messages")
async def post_human_directive(
    sid: str, body: schemas.HumanDirective, ctx: UserContext = Depends(_ctx)
):
    """Send a follow-up message to an existing session — the multi-turn path.

    Spawns a fresh runtime turn that resumes the prior SDK conversation via the
    persisted UUID, so the supervisor keeps full context. Returns 404 if the
    session is unknown; 409 if a turn is still running (Stop it or wait) or there
    is no resumable prior turn yet."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    if _monitor_key(ctx, sid) in _RUNNING:
        raise HTTPException(409, "session is busy; stop the current turn or wait")

    resume_uuid = _read_sdk_session(ctx, sid)
    if not resume_uuid:
        raise HTTPException(
            409,
            "no resumable conversation yet — the first turn has not produced a"
            " session id (still starting, or it crashed before its first reply)",
        )

    _clear_stop(ctx, sid)
    _append_event(ctx, sid, actor="human", kind="message.received", text=body.text)
    await _spawn_research(ctx, sid, body.text, resume_uuid)
    return {"ok": True, "session_id": sid, "resumed": True}


@app.post("/research/sessions/{sid}/interject")
def interject(sid: str, body: schemas.HumanDirective, ctx: UserContext = Depends(_ctx)):
    """Send a message to a turn that is currently blocked on a HITL prompt, so
    the human can talk to the agent *before* deciding approve/deny.

    Unlike ``/messages`` this does not require the session to be idle: it queues
    the text into the session's chat channel for the running runtime's HITL wait
    loop to pick up (see ``hitl.drain_chat``). 404 if the session is unknown."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    chat_dir = ctx.session_dir(sid) / "control" / "chat"
    chat_dir.mkdir(parents=True, exist_ok=True)
    cid = f"{time.time():.6f}-{uuid.uuid4().hex[:8]}"
    write_json(chat_dir / f"{cid}.json", {
        "id": cid,
        "text": body.text,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _append_event(ctx, sid, actor="human", kind="message.received", text=body.text)
    return {"ok": True, "queued": True}


# ---------- sessions ----------


@app.get("/sessions", response_model=list[schemas.SessionSummary])
def list_sessions(ctx: UserContext = Depends(_ctx)):
    ctx.sessions.mkdir(parents=True, exist_ok=True)
    out: list[schemas.SessionSummary] = []
    for sd in sorted((p for p in ctx.sessions.iterdir() if p.is_dir()), reverse=True):
        events_path = sd / "events.jsonl"
        task = None
        last_kind = None
        last_ts = None
        if events_path.exists():
            try:
                lines = [line.strip() for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                for line in lines:
                    rec = json.loads(line)
                    if task is None and rec.get("task"):
                        task = rec.get("task")
                    last_kind = rec.get("kind")
                    last_ts = rec.get("ts")
            except Exception:
                last_kind = "unreadable"
        out.append(
            schemas.SessionSummary(
                session_id=sd.name,
                task=task,
                mtime=sd.stat().st_mtime,
                last_ts=last_ts,
                last_kind=last_kind,
                running=_monitor_key(ctx, sd.name) in _RUNNING,
            )
        )
    return out


@app.get("/sessions/{sid}/events")
def session_events(sid: str, limit: int = 100, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    if limit < 1 or limit > 1000:
        raise HTTPException(400, "limit must be between 1 and 1000")
    return _tail_events(ctx, sid)[-limit:]


# Subdirs of a session that hold agent-generated, user-facing output.
_SESSION_OUTPUT_DIRS = ("results", "scratch")


def _resolve_session_file(ctx: UserContext, sid: str, relpath: str) -> Path:
    """Resolve ``relpath`` to a real file inside the session's output dirs,
    rejecting anything that escapes them (path traversal)."""
    base = ctx.session_dir(sid)
    target = (base / relpath).resolve()
    roots = [(base / d).resolve() for d in _SESSION_OUTPUT_DIRS]
    for root in roots:
        if target == root or root in target.parents:
            return target
    raise HTTPException(400, "path is outside the session's output directories")


@app.get("/sessions/{sid}/files", response_model=list[schemas.SessionFile])
def list_session_files(sid: str, ctx: UserContext = Depends(_ctx)):
    """List files the agent generated for this session (results/ and scratch/),
    newest first, so the UI can show and download them."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    base = ctx.session_dir(sid)
    out: list[schemas.SessionFile] = []
    for sub in _SESSION_OUTPUT_DIRS:
        d = base / sub
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            out.append(
                schemas.SessionFile(
                    path=str(p.relative_to(base)), size=stat.st_size, mtime=stat.st_mtime
                )
            )
    out.sort(key=lambda f: f.mtime, reverse=True)
    return out


@app.get("/sessions/{sid}/files/download")
def download_session_file(sid: str, path: str, ctx: UserContext = Depends(_ctx)):
    """Download one generated file from this session by its relative path
    (e.g. ``results/summary.md``). Tenant-scoped + traversal-guarded."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    target = _resolve_session_file(ctx, sid, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"no such file: {path}")
    return FileResponse(target, filename=target.name)


@app.delete("/sessions/{sid}")
def delete_session(sid: str, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(sid)
    if _monitor_key(ctx, sid) in _RUNNING:
        raise HTTPException(409, "session is running; stop it before deleting")
    target = ctx.session_dir(sid)
    if not target.exists():
        raise HTTPException(404, "unknown session")
    shutil.rmtree(target)
    return {"ok": True, "session_id": sid}


# ---------- library ----------

_LIBRARY_CHUNK = 1024 * 1024  # 1 MB streaming chunks


def _safe_library_name(raw: str) -> str:
    name = Path(raw).name.lstrip(".")
    if not name or name in {".staging"}:
        raise HTTPException(400, f"invalid filename: {raw!r}")
    return name


def _append_library_event(ctx: UserContext, kind: str, **fields) -> None:
    ctx.library_events.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "kind": kind, **fields}
    with open(ctx.library_events, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


@app.post("/library/files", response_model=schemas.LibraryUploadResponse)
async def upload_library_file(
    file: UploadFile = File(...), ctx: UserContext = Depends(_ctx)
):
    ctx.library_staging.mkdir(parents=True, exist_ok=True)
    name = _safe_library_name(file.filename or "")
    final_path = ctx.library / name
    overwrote = final_path.exists()
    staging_path = ctx.library_staging / f"{uuid.uuid4().hex}.part"
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

    _append_library_event(ctx, "library.upload", name=name, size=written, overwrote=overwrote)
    return schemas.LibraryUploadResponse(name=name, size=written, overwrote=overwrote)


@app.get("/library/files", response_model=list[schemas.LibraryFile])
def list_library_files(ctx: UserContext = Depends(_ctx)):
    ctx.library_staging.mkdir(parents=True, exist_ok=True)
    out: list[schemas.LibraryFile] = []
    for p in ctx.library.iterdir():
        if p.name.startswith(".") or p.name == ".staging":
            continue
        try:
            stat = p.stat()
        except OSError:
            try:
                out.append(schemas.LibraryFile(name=p.name, size=0, mtime=p.lstat().st_mtime))
            except OSError:
                pass
            continue
        if stat.st_size == 0 and p.is_dir():
            continue
        out.append(schemas.LibraryFile(name=p.name, size=stat.st_size, mtime=stat.st_mtime))
    out.sort(key=lambda f: f.mtime, reverse=True)
    return out


@app.delete("/library/files/{name}")
def delete_library_file(name: str, ctx: UserContext = Depends(_ctx)):
    safe = _safe_library_name(name)
    target = ctx.library / safe
    if not target.exists() and not target.is_symlink():
        raise HTTPException(404, f"no such library file: {safe}")
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as e:
        raise HTTPException(500, f"delete failed: {e}")
    _append_library_event(ctx, "library.delete", name=safe)
    return {"ok": True, "name": safe}


@app.get("/library/health", response_model=schemas.LibraryHealth)
def library_health(ctx: UserContext = Depends(_ctx)):
    ctx.library_staging.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_bytes = 0
    broken: list[dict[str, str]] = []
    for p in ctx.library.iterdir():
        if p.name.startswith(".") or p.name == ".staging":
            continue
        if p.is_symlink():
            try:
                target = os.readlink(p)
            except OSError:
                target = "?"
            if not p.exists():
                broken.append({"name": p.name, "target": str(target)})
                continue
        try:
            stat = p.stat()
        except OSError:
            broken.append({"name": p.name, "target": "?"})
            continue
        file_count += 1
        total_bytes += stat.st_size

    staging_files = sum(1 for _ in ctx.library_staging.iterdir()) if ctx.library_staging.exists() else 0
    disk_free = shutil.disk_usage(ctx.state).free
    return schemas.LibraryHealth(
        file_count=file_count,
        total_bytes=total_bytes,
        disk_free_bytes=disk_free,
        staging_files=staging_files,
        broken_symlinks=broken,
    )


# ---------- hitl ----------


@app.get("/hitl/{sid}/pending")
def hitl_pending(sid: str, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    pending = _pending_dir(ctx, sid)
    answered = _answered_dir(ctx, sid)
    result = []
    if not pending.exists():
        return result
    for path in sorted(pending.glob("*.json")):
        rec = read_json(path, {})
        rid = rec.get("id", path.stem)
        if (answered / f"{rid}.json").exists():
            path.unlink(missing_ok=True)
            continue
        result.append(rec)
    return result


@app.post("/hitl/{sid}/{request_id}/answer")
def hitl_answer(
    sid: str, request_id: str, body: schemas.HitlAnswer, ctx: UserContext = Depends(_ctx)
):
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    pending = _pending_dir(ctx, sid) / f"{request_id}.json"
    if not pending.exists():
        raise HTTPException(404, f"no pending HITL request {request_id}")
    rec = read_json(pending, {})
    rec["decision"] = body.decision
    rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec["note"] = body.note
    write_json(_answered_dir(ctx, sid) / f"{request_id}.json", rec)
    pending.unlink()
    _append_event(ctx, sid, actor="human", kind="hitl.answer", ref=request_id, decision=body.decision)
    return {"ok": True}


# ---------- events (SSE) ----------


@app.get("/events/{sid}")
async def events(sid: str, since: str | None = None, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")

    async def generator():
        last = since
        while True:
            recs = _tail_events(ctx, sid, since_id=last)
            for rec in recs:
                last = rec["id"]
                yield f"data: {json.dumps(rec)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generator(), media_type="text/event-stream")


# ---------- introspection ----------


@app.get("/roles", response_model=list[schemas.RoleSummary])
def list_roles(ctx: UserContext = Depends(_ctx)):
    out: list[schemas.RoleSummary] = []
    role_paths = [ctx.root / "roles" / "supervisor.yaml"]
    role_paths.extend(sorted((ctx.root / "roles" / "subagents").glob("*.yaml")))
    for path in role_paths:
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        out.append(
            schemas.RoleSummary(
                name=raw["name"],
                description=raw.get("description", ""),
                tools=list(raw.get("tools", [])),
                can_spawn=bool(raw.get("can_spawn", False)),
                model=raw.get("model", ""),
            )
        )
    return out


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", (text or "").lower()))


def _recall_jsonl(path: Path, query: str, k: int) -> list[dict]:
    if not path.exists():
        return []
    q = _tokenize(query)
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            score = len(q & _tokenize(rec.get("text", ""))) if q else 1
            rows.append((score, rec))
    rows.sort(key=lambda item: item[0], reverse=True)
    return [rec for score, rec in rows[:k] if score > 0]


@app.get("/memory/{layer}", response_model=schemas.MemorySearchResult)
def search_memory(layer: str, q: str, k: int = 5, sid: str | None = None, ctx: UserContext = Depends(_ctx)):
    if layer == "global":
        return schemas.MemorySearchResult(
            layer="global", hits=_recall_jsonl(ctx.state / "memory" / "global.jsonl", q, k)
        )
    if layer == "project":
        if not sid:
            raise HTTPException(400, "project memory requires sid")
        sid = _safe_sid(sid)
        if not _session_exists(ctx, sid):
            raise HTTPException(404, "unknown session")
        return schemas.MemorySearchResult(
            layer="project",
            hits=_recall_jsonl(ctx.session_dir(sid) / "memory" / "project" / "shared.jsonl", q, k),
        )
    raise HTTPException(400, f"unknown layer {layer}")


@app.get("/sessions/{sid}/inbox/{agent}")
def peek_inbox(sid: str, agent: str, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    inbox = ctx.session_dir(sid) / "inbox" / Path(agent).name
    return [read_json(p) for p in sorted(inbox.glob("*.json"))]


# ---------- git history (read-only) ----------


@app.get("/git/history", response_model=schemas.GitHistory)
def git_history(limit: int = 200):
    """Full branch graph (all refs) for the UI to render. Read-only.

    The UI container has no ``.git`` mount, so it fetches this over HTTP from the
    API container, which does. Reflects the shared system repo's evolution
    history."""
    try:
        return sandbox.history(limit=limit)
    except sandbox.GitError as e:
        raise HTTPException(500, f"git history failed: {e}")
