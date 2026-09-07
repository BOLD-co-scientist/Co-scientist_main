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
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from scaffold import archive, contract, sandbox, settings
from scaffold._atomic import append_jsonl, read_json, write_json

from . import evo_store, llm, llm_sdk
from . import goals as _goals
from . import judge as _judge
from . import onboarding as _onboarding
from . import planner as _planner
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


def _research_active_dir(ctx: UserContext) -> Path:
    # Cross-process signal (the evolution subprocess can't read _RUNNING): one
    # marker file per running research session, holding its PID so the evolution
    # merge guard can verify liveness and ignore stale markers from a crash.
    return ctx.state / "control" / "research_active"


def _mark_research_active(ctx: UserContext, sid: str, pid: int) -> None:
    d = _research_active_dir(ctx)
    d.mkdir(parents=True, exist_ok=True)
    (d / sid).write_text(str(pid), encoding="utf-8")


def _clear_research_active(ctx: UserContext, sid: str) -> None:
    (_research_active_dir(ctx) / sid).unlink(missing_ok=True)


# ---- version-switch guard (R17) --------------------------------------------
# Switching the active version is a git checkout of the tenant's agent-layer code
# that per-turn subprocesses read. Two guarantees (see docs/plans/R17): (1) never
# swap code while a turn runs — refuse if the tenant is busy; (2) block NEW spawns
# for the duration of the checkout — a `switch_lock` marker the spawn paths honour,
# so a turn can't start mid-checkout and read a half-updated tree. Long jobs (R12)
# are async/independent and are deliberately NOT waited on.
def _switch_lock_path(ctx: UserContext) -> Path:
    return ctx.state / "control" / "switch_lock"


def _is_switch_locked(ctx: UserContext) -> bool:
    return _switch_lock_path(ctx).exists()


def _tenant_busy(ctx: UserContext) -> bool:
    """True if any research/evolution runtime subprocess is live for this tenant."""
    uid = ctx.user.user_id
    return any(key[0] == uid for key in _RUNNING)


def _guard_not_switching(ctx: UserContext) -> None:
    if _is_switch_locked(ctx):
        raise HTTPException(409, "a version switch is in progress — retry in a moment")


async def _monitor_runtime(
    ctx: UserContext, sid: str, proc: "asyncio.subprocess.Process", log_fh
) -> None:
    """Await a runtime subprocess, then clean up and log abnormal exits."""
    try:
        rc = await proc.wait()
    finally:
        _RUNNING.pop(_monitor_key(ctx, sid), None)
        _clear_research_active(ctx, sid)
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
    elif not sid.startswith("evo-") and settings.EVO_REFLECT:
        # R16: a research turn finished cleanly → kick the read-only reflection
        # pass in its own detached subprocess so it never delays or blocks the
        # turn, and stays a separate node from the (heavy) evolution modifier.
        await _spawn_reflection(ctx, sid)
    # R19: close the guided-search loop. An evolution session links its
    # proposal to the outcome and drains the approved queue; a clean research
    # turn records adoption and re-plans for its brief.
    if sid.startswith("evo-"):
        await _after_evolution(ctx, sid)
    elif rc == 0:
        _record_adoption(ctx, sid)
        _schedule_plan_for_session(ctx, sid, trigger="research.complete")


async def _reap_reflection(proc: "asyncio.subprocess.Process", log_fh) -> None:
    try:
        await proc.wait()
    finally:
        try:
            log_fh.close()
        except Exception:
            pass


async def _spawn_reflection(ctx: UserContext, sid: str) -> None:
    """Kick the R16 reflection runner (read-only) as a detached subprocess.
    Advisory only — any failure here must never affect the research session."""
    try:
        log_fh = open(ctx.session_dir(sid) / "reflect.log", "ab")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "research.reflect", "--session", sid,
            cwd=str(ctx.root),
            env=_runtime_env(ctx),
            stdout=log_fh,
            stderr=log_fh,
        )
        asyncio.create_task(_reap_reflection(proc, log_fh))
    except Exception:
        pass


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
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(ctx.root),
            env=_runtime_env(ctx),
            stdout=log_fh,
            stderr=log_fh,
        )
    except Exception:
        # e.g. E2BIG when the message exceeds the kernel's per-argv limit.
        # Don't leak the log handle; let the caller decide what to clean up.
        log_fh.close()
        raise
    _RUNNING[_monitor_key(ctx, sid)] = proc
    # Research only: signals the evolution merge guard to wait. Evolution spawns
    # deliberately do NOT mark, so a merge never waits on its own subprocess.
    _mark_research_active(ctx, sid, proc.pid)
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
    _guard_not_switching(ctx)
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


@app.post("/sessions/{sid}/fork")
def fork_session(sid: str, ctx: UserContext = Depends(_ctx)):
    """R17: branch a session into a new one continuable on the CURRENT active
    version. Copies the session dir and drops the schema stamp (re-stamped on the
    next turn at the current version), so a read-only session — one created by a
    newer version than the one now active — can be continued after a fork. The
    original is left untouched (the read-only guard keeps it safe)."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    new_sid = _safe_sid(_new_session_id())
    src = ctx.session_dir(sid)
    dst = ctx.session_dir(new_sid)
    if dst.exists():
        raise HTTPException(409, "fork target already exists")
    shutil.copytree(src, dst)
    (dst / "schema.json").unlink(missing_ok=True)
    _append_event(ctx, new_sid, actor="system", kind="session.forked", parent=sid)
    return {"session_id": new_sid, "parent": sid}


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
    _guard_not_switching(ctx)
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
        # O1: a session launched from a problem brief carries a frozen copy of
        # it; surface the title + working hypothesis so the UI can anchor on them.
        brief = _read_session_brief(ctx, sd.name)
        hyp = brief.get("hypothesis") if brief else None
        # R17: a session stamped by a newer schema than this version can read is
        # read-only here (the runtime guard opens it read-only). Surface it so the
        # UI can show a banner + offer fork-to-continue.
        _schema = read_json(sd / "schema.json", default=None)
        readonly = isinstance(_schema, dict) and int(_schema.get("schema_version", 1)) > contract.SCHEMA_VERSION
        out.append(
            schemas.SessionSummary(
                session_id=sd.name,
                task=task,
                mtime=sd.stat().st_mtime,
                last_ts=last_ts,
                last_kind=last_kind,
                running=_monitor_key(ctx, sd.name) in _RUNNING,
                has_brief=brief is not None,
                brief_title=(brief.get("title") or None) if brief else None,
                hypothesis=(hyp.get("statement") or None) if isinstance(hyp, dict) else None,
                readonly=readonly,
            )
        )
    return out


def _read_session_brief(ctx: UserContext, sid: str) -> dict | None:
    # A corrupt/truncated brief.json must never take GET /sessions down for the
    # whole tenant — treat it as "no brief".
    try:
        rec = read_json(ctx.session_dir(sid) / _onboarding.BRIEF_FILENAME, None)
    except Exception:
        return None
    return rec if isinstance(rec, dict) else None


@app.get("/sessions/{sid}/brief")
def session_brief(sid: str, ctx: UserContext = Depends(_ctx)):
    """The problem brief this session was launched from (O1), frozen at launch.
    404 for sessions started from a free-form task."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    rec = _read_session_brief(ctx, sid)
    if rec is None:
        raise HTTPException(404, "this session was not started from a problem brief")
    rec = dict(rec)
    rec["text"] = _onboarding.render_brief(rec, ctx.library)
    return rec


@app.get("/sessions/{sid}/events")
def session_events(sid: str, limit: int = 100, ctx: UserContext = Depends(_ctx)):
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    if limit < 1 or limit > 1000:
        raise HTTPException(400, "limit must be between 1 and 1000")
    return _tail_events(ctx, sid)[-limit:]


@app.get("/sessions/{sid}/reflection")
def session_reflection(sid: str, ctx: UserContext = Depends(_ctx)):
    """R16: the per-session reflection + evolution proposals, if any. Powers the
    research-conversation nudge card and the Evolution-tab suggestion cards.
    Returns empty proposals when no reflection has run (or none was warranted)."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    rec = read_json(ctx.session_dir(sid) / "reflection.json", default=None)
    if not isinstance(rec, dict):
        return {"reflection": "", "proposals": []}
    return {"reflection": rec.get("reflection", ""), "proposals": rec.get("proposals") or []}


@app.post("/sessions/{sid}/reflect")
async def session_reflect(sid: str, ctx: UserContext = Depends(_ctx)):
    """Re-run the R16 reflection pass on demand (e.g. the human wants suggestions
    even though the auto pass was skipped). Fire-and-forget; poll the reflection
    endpoint / watch for the ``reflection.ready`` event."""
    sid = _safe_sid(sid)
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    if sid.startswith("evo-"):
        raise HTTPException(400, "reflection is for research sessions")
    await _spawn_reflection(ctx, sid)
    return {"ok": True, "queued": True}


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
    # O1: a session launched from a brief owns that brief's "launched" status.
    # Deleting the session hands the brief back as a draft so it can be edited
    # and relaunched instead of being stranded (frozen, hidden from drafts).
    brief = _read_session_brief(ctx, sid)
    shutil.rmtree(target)
    if brief and brief.get("id"):
        draft = _onboarding.load_brief(ctx.state, str(brief["id"]))
        if draft and draft.get("session_id") == sid:
            draft.update({"status": "draft", "session_id": None})
            _onboarding.save_brief(ctx.state, draft)
    return {"ok": True, "session_id": sid}


# ---------- library ----------

_LIBRARY_CHUNK = 1024 * 1024  # 1 MB streaming chunks


def _safe_library_name(raw: str) -> str:
    name = Path(raw).name.lstrip(".")
    if not name or name in {".staging"}:
        raise HTTPException(400, f"invalid filename: {raw!r}")
    return name


def _safe_library_relpath(raw: str) -> Path:
    """Sanitize a *relative* library path (folder upload), guarding traversal.

    Accepts forward-slash separated paths like ``data/panel/ic50.csv``. Each
    segment is validated (no ``..``, no absolute, no ``.staging``, no leading
    dots) and the result is confirmed to resolve inside the library root. The
    guard is enforced here, at the tool layer, not in the caller."""
    if "\x00" in raw or len(raw) > 4096:
        raise HTTPException(400, "invalid path: NUL byte or too long")
    parts = [seg for seg in Path(raw.replace("\\", "/")).as_posix().split("/") if seg]
    if not parts:
        raise HTTPException(400, f"invalid path: {raw!r}")
    for seg in parts:
        if seg in {"", ".", "..", ".staging"} or seg.startswith("."):
            raise HTTPException(400, f"invalid path segment {seg!r} in {raw!r}")
        if len(seg) > 255:
            raise HTTPException(400, f"path segment too long in {raw!r}")
    rel = Path(*parts)
    # Belt-and-suspenders: confirm it stays inside the library after resolving.
    return rel


def _resolve_library_path(ctx: UserContext, rel: Path) -> Path:
    base = ctx.library.resolve()
    target = (ctx.library / rel).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(400, "path escapes the library")
    return target


def _append_library_event(ctx: UserContext, kind: str, **fields) -> None:
    ctx.library_events.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "kind": kind, **fields}
    with open(ctx.library_events, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


@app.post("/library/files", response_model=schemas.LibraryUploadResponse)
async def upload_library_file(
    file: UploadFile = File(...),
    relpath: str | None = Form(default=None),
    ctx: UserContext = Depends(_ctx),
):
    """Upload one file to the shared library.

    For a plain file upload, ``relpath`` is omitted and the file lands at the
    library root under its basename. For a *folder* upload the client sends each
    file with its ``relpath`` (the browser's ``webkitRelativePath``), preserving
    the directory structure — guarded against traversal by
    ``_safe_library_relpath``."""
    ctx.library_staging.mkdir(parents=True, exist_ok=True)
    if relpath:
        rel = _safe_library_relpath(relpath)
        name = rel.as_posix()
        final_path = _resolve_library_path(ctx, rel)
    else:
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
        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_path, final_path)
    except HTTPException:
        staging_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(500, f"upload failed: {e}")

    _append_library_event(ctx, "library.upload", name=name, size=written, overwrote=overwrote)
    return schemas.LibraryUploadResponse(name=name, size=written, overwrote=overwrote)


def _iter_library_files(ctx: UserContext):
    """Yield every real file in the library, recursively, skipping the staging
    area and dotfiles. Each is paired with its library-relative POSIX path so
    the UI can reconstruct the folder tree."""
    root = ctx.library
    if not root.exists():
        return
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(seg == ".staging" or seg.startswith(".") for seg in rel.parts):
            continue
        yield rel.as_posix(), p


@app.get("/library/files", response_model=list[schemas.LibraryFile])
def list_library_files(ctx: UserContext = Depends(_ctx)):
    ctx.library_staging.mkdir(parents=True, exist_ok=True)
    out: list[schemas.LibraryFile] = []
    for relname, p in _iter_library_files(ctx):
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append(schemas.LibraryFile(name=relname, size=stat.st_size, mtime=stat.st_mtime))
    out.sort(key=lambda f: f.mtime, reverse=True)
    return out


@app.delete("/library/files/{name:path}")
def delete_library_file(name: str, ctx: UserContext = Depends(_ctx)):
    """Delete a library file or folder by its library-relative path. A folder
    path removes the subtree; empty parent folders left behind are pruned."""
    rel = _safe_library_relpath(name)
    target = _resolve_library_path(ctx, rel)
    if not target.exists() and not target.is_symlink():
        raise HTTPException(404, f"no such library file: {rel.as_posix()}")
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        # Prune now-empty parent directories, up to (not including) the library.
        parent = target.parent
        while parent != ctx.library and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except OSError as e:
        raise HTTPException(500, f"delete failed: {e}")
    _append_library_event(ctx, "library.delete", name=rel.as_posix())
    return {"ok": True, "name": rel.as_posix()}


@app.get("/library/health", response_model=schemas.LibraryHealth)
def library_health(ctx: UserContext = Depends(_ctx)):
    ctx.library_staging.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_bytes = 0
    broken: list[dict[str, str]] = []
    for relname, p in _iter_library_files(ctx):
        if p.is_symlink() and not p.exists():
            try:
                target = os.readlink(p)
            except OSError:
                target = "?"
            broken.append({"name": relname, "target": str(target)})
            continue
        try:
            stat = p.stat()
        except OSError:
            broken.append({"name": relname, "target": "?"})
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
        # R19: the judge's gate-2 review, when it has run for this request.
        judge_rec = read_json(_judge_dir(ctx, sid) / f"{rid}.json", default=None)
        if isinstance(judge_rec, dict):
            rec["judge"] = judge_rec
        result.append(rec)
    return result


@app.post("/hitl/{sid}/{request_id}/answer")
def hitl_answer(
    sid: str, request_id: str, body: schemas.HitlAnswer, ctx: UserContext = Depends(_ctx)
):
    sid = _safe_sid(sid)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", request_id or ""):
        raise HTTPException(400, f"invalid request id: {request_id!r}")
    if not _session_exists(ctx, sid):
        raise HTTPException(404, "unknown session")
    _answer_pending(ctx, sid, request_id, body.decision, body.note, actor="human")
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
def git_history(limit: int = 200, ctx: UserContext = Depends(_ctx)):
    """Branch graph of the authenticated researcher's *own* harness repo.

    Tenant-scoped: reflects only this user's evolution lineage — their
    ``evo/*`` branches and merges into their own ``main`` — not the shared
    development repo or any other tenant's history. A freshly bootstrapped
    root with no self-modifications yet returns an empty graph."""
    try:
        return sandbox.history(limit=limit, repo=ctx.root)
    except sandbox.GitError as e:
        raise HTTPException(500, f"git history failed: {e}")


@app.get("/versions")
def list_versions(ctx: UserContext = Depends(_ctx)):
    """R17: the tenant's evolution version DAG — every ``ver/<id>`` node (a
    merged evolution, made switchable) joined with its archive metadata
    (summary, rationale, owner, smoke, status), plus which node is currently
    active (== HEAD). Read-only, tenant-scoped; safe on every UI refresh."""
    return archive.list_versions(ctx.root)


@app.post("/versions/{version_id}/activate")
def activate_version(version_id: str, ctx: UserContext = Depends(_ctx)):
    """R17: switch this tenant's active version — check out ``ver/<version_id>``
    (or a bare commit sha, e.g. the bootstrap root) into the working tree.

    Idle-guarded: refuses (409) if any research/evolution turn is running, and
    holds a ``switch_lock`` for the duration so no new turn can spawn mid-checkout
    and read a half-swapped tree. Long jobs (R12) are async/independent and are
    NOT waited on. Returns the refreshed version DAG (with the new active node)."""
    if not re.fullmatch(r"[0-9A-Za-z._][0-9A-Za-z._-]{0,119}", version_id or ""):
        raise HTTPException(400, f"invalid version id: {version_id!r}")
    # (1) block new spawns FIRST, then (2) verify nothing is in flight, then swap.
    lock = _switch_lock_path(ctx)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(version_id, encoding="utf-8")
    try:
        if _tenant_busy(ctx):
            raise HTTPException(
                409, "stop the running research/evolution turn before switching versions"
            )
        try:
            archive.activate_version(ctx.root, version_id)
        except archive.ArchiveError as e:
            raise HTTPException(409, str(e))
    finally:
        lock.unlink(missing_ok=True)
    return archive.list_versions(ctx.root)


@app.get("/git/commit/{sha}")
def git_commit(sha: str, ctx: UserContext = Depends(_ctx)):
    """What's *in* a commit of the researcher's own harness repo — metadata plus
    the files it changed. Powers the clickable node detail in the evolution
    graph. Read-only, tenant-scoped."""
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", sha or ""):
        raise HTTPException(400, f"invalid commit sha: {sha!r}")
    try:
        return sandbox.commit_detail(sha, repo=ctx.root)
    except sandbox.GitError as e:
        raise HTTPException(404, f"commit not found: {e}")


# ---------- evolution (spawn a self-modification from a chosen parent) ----------


async def _spawn_evolution(ctx: UserContext, sid: str, command: str, base: str | None, *, scope: str = "harness") -> None:
    """Spawn the evolution runtime as a fresh subprocess. Mirrors
    ``_spawn_research``: the runtime creates an ``evo/*`` worktree off ``base``
    (a commit the human picked in the graph, or HEAD), runs the SDK evolution
    agent, and proposes a merge through the same HITL gate. stdout+stderr → the
    session runtime.log.

    R19 ``scope="platform"``: the worktree branches from the shared platform
    checkout (``settings.PLATFORM_REPO``: ui3/ + api/) instead of the tenant
    harness, using the platform's own evolution runtime code; events and state
    stay in the tenant session."""
    log_fh = open(ctx.session_dir(sid) / "runtime.log", "ab")
    argv = [sys.executable, "-m", "evolution.runtime", "--session", sid, "--command", command]
    if base:
        argv += ["--base", base]
    env = _runtime_env(ctx)
    cwd = str(ctx.root)
    if scope == "platform":
        argv += ["--scope", "platform"]
        env["COSCIENTIST_REPO"] = str(settings.PLATFORM_REPO)
        env["PYTHONPATH"] = str(settings.PLATFORM_REPO) + os.pathsep + env.get("PYTHONPATH", "")
        cwd = str(settings.PLATFORM_REPO)
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env, stdout=log_fh, stderr=log_fh
    )
    _RUNNING[_monitor_key(ctx, sid)] = proc
    asyncio.create_task(_monitor_runtime(ctx, sid, proc, log_fh))


@app.post("/evolution/commands", response_model=schemas.StartEvolutionResponse)
async def start_evolution(req: schemas.StartEvolutionRequest, ctx: UserContext = Depends(_ctx)):
    """Spawn a self-modification: the evolution agent branches an ``evo/*``
    worktree from ``req.base`` (a commit sha the human picked in the graph, or
    HEAD if omitted), attempts the change described by ``req.command``, and
    proposes a merge for human approval. Its ``evolution.*`` lifecycle streams
    into the returned session and the merge lands in the lineage graph."""
    command = (req.command or "").strip()
    if not command:
        raise HTTPException(400, "command must not be empty")
    if _evo_running(ctx):
        raise HTTPException(409, "an evolution is already running; wait for it to finish (one at a time per harness)")
    proposal = None
    if req.proposal_id:
        proposal = evo_store.load_proposal(ctx.state, req.proposal_id)
        if proposal is None:
            raise HTTPException(404, "unknown proposal")
        if proposal.get("status") not in ("proposed", "queued"):
            raise HTTPException(409, f"proposal is already {proposal.get('status')}")
        proposal["human"] = {"decision": "pick", "note": "", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "edited": proposal.get("command") != command}
        evo_store.save_proposal(ctx.state, proposal)
        evo_store.record_decision(ctx.state, proposal_id=proposal["id"], actor="human", stage="proposal", decision="pick", note="edited command" if proposal["human"]["edited"] else "")
    sid = await _launch_evolution(ctx, command, getattr(req, "base", None), proposal=proposal, scope=(req.scope or "harness"), actor="human")
    return schemas.StartEvolutionResponse(session_id=sid, command=command)
# ---------- hypothesis engine (interim: parallel-set generation via Fable) ----------
#
# The human enters a goal, gets a few PARALLEL hypotheses, and either selects one
# or refines from one (spawning a fresh parallel set). No tree, no tournament.
# TODO(H1): replace with the Google AI co-scientist protocol — contract + plan in
# docs/plans/H1-hypothesis-coscientist.md. This is the placeholder backend.

from . import hypothesis as _hypothesis  # noqa: E402  (kept local to this section)


def _hyp_dir(ctx: UserContext) -> Path:
    d = ctx.state / "hypotheses"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hyp_path(ctx: UserContext, hid: str) -> Path:
    return _hyp_dir(ctx) / f"{_safe_sid(hid)}.json"


def _new_hyp_id(hyps: list[dict], round_no: int) -> None:
    for i, h in enumerate(hyps):
        h["id"] = f"h{round_no}_{i}_{uuid.uuid4().hex[:6]}"
        h["round"] = round_no


def _hyp_summary(rec: dict) -> dict:
    rounds = rec.get("rounds", [])
    latest = rounds[-1] if rounds else {}
    return {
        "id": rec.get("id"),
        "goal": rec.get("goal"),
        "created": rec.get("created"),
        "rounds": len(rounds),
        "latest_count": len(latest.get("hypotheses", [])),
        "selected_id": rec.get("selected_id"),
        "brief_id": rec.get("brief_id"),  # O1: set when seeded from a problem brief
    }


@app.get("/hypothesis/sessions")
def list_hypothesis_sessions(ctx: UserContext = Depends(_ctx)):
    out = []
    for p in sorted(_hyp_dir(ctx).glob("*.json"), reverse=True):
        rec = read_json(p, {})
        if rec:
            out.append(_hyp_summary(rec))
    out.sort(key=lambda s: s.get("created") or "", reverse=True)
    return out


@app.get("/hypothesis/{hid}")
def get_hypothesis(hid: str, ctx: UserContext = Depends(_ctx)):
    rec = read_json(_hyp_path(ctx, hid), None)
    if not rec:
        raise HTTPException(404, "unknown hypothesis session")
    return rec


def _create_hyp_session(
    ctx: UserContext, goal: str, hyps: list[dict], served: str, **extra: Any
) -> dict:
    """Persist a new hypothesis session (round 0). Shared by the free-form
    Hypotheses page and the onboarding brief (O1), which adds ``brief_id``."""
    _new_hyp_id(hyps, 0)
    hid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rec = {
        "id": hid,
        "goal": goal,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "selected_id": None,
        "rounds": [
            {"round": 0, "parent_id": None, "feedback": None, "served_by": served, "hypotheses": hyps}
        ],
        **extra,
    }
    write_json(_hyp_path(ctx, hid), rec)
    return rec


def _find_hyp_card(rec: dict, hyp_id: str | None) -> dict | None:
    if not hyp_id:
        return None
    for rnd in rec.get("rounds", []):
        for h in rnd.get("hypotheses", []):
            if h.get("id") == hyp_id:
                return h
    return None


@app.post("/hypothesis/sessions")
async def start_hypothesis(req: schemas.StartHypothesisRequest, ctx: UserContext = Depends(_ctx)):
    goal = (req.goal or "").strip()
    if not goal:
        raise HTTPException(400, "goal must not be empty")
    n = (req.config.n_initial if req.config and req.config.n_initial else None) or 4
    n = max(2, min(6, n))
    hyps, served = await _hypothesis.generate(goal, n=n)
    if not hyps:
        raise HTTPException(502, "hypothesis generation returned nothing; try rephrasing the goal")
    return _create_hyp_session(ctx, goal, hyps, served)


@app.post("/hypothesis/{hid}/refine")
async def refine_hypothesis(
    hid: str, req: schemas.RefineHypothesisRequest, ctx: UserContext = Depends(_ctx)
):
    rec = read_json(_hyp_path(ctx, hid), None)
    if not rec:
        raise HTTPException(404, "unknown hypothesis session")
    parent = None
    for rnd in rec.get("rounds", []):
        for h in rnd.get("hypotheses", []):
            if h.get("id") == req.parent_id:
                parent = h
                break
        if parent:
            break
    if parent is None:
        raise HTTPException(404, f"no hypothesis {req.parent_id} in this session")
    n = max(2, min(6, req.n or 4))
    # A brief-seeded search (O1) carries its brief context; keep it for refines.
    hyps, served = await _hypothesis.generate(
        rec["goal"], parent=parent, feedback=req.feedback, n=n, context=rec.get("context")
    )
    if not hyps:
        raise HTTPException(502, "hypothesis generation returned nothing; try different feedback")
    round_no = len(rec["rounds"])
    _new_hyp_id(hyps, round_no)
    for h in hyps:
        h["parent_id"] = req.parent_id
    rec["rounds"].append(
        {
            "round": round_no,
            "parent_id": req.parent_id,
            "feedback": (req.feedback or "").strip() or None,
            "served_by": served,
            "hypotheses": hyps,
        }
    )
    write_json(_hyp_path(ctx, hid), rec)
    return rec


@app.post("/hypothesis/{hid}/select")
def select_hypothesis(
    hid: str, req: schemas.SelectHypothesisRequest, ctx: UserContext = Depends(_ctx)
):
    path = _hyp_path(ctx, hid)
    rec = read_json(path, None)
    if not rec:
        raise HTTPException(404, "unknown hypothesis session")
    card = _find_hyp_card(rec, req.hypothesis_id)
    if card is None:
        raise HTTPException(404, f"no hypothesis {req.hypothesis_id} in this session")
    rec["selected_id"] = req.hypothesis_id
    rec["select_note"] = (req.note or "").strip() or None
    write_json(path, rec)
    # O1: a search seeded from a problem brief keeps the brief's working
    # hypothesis in sync, server-side, so the UI never has to copy it across.
    _sync_brief_hypothesis(ctx, rec)
    return rec


def _sync_brief_hypothesis(ctx: UserContext, hyp_rec: dict) -> None:
    bid = hyp_rec.get("brief_id")
    if not bid:
        return
    brief = _onboarding.load_brief(ctx.state, bid)
    if brief is None or brief.get("status") == "launched":
        return
    if brief.get("hypothesis_session_id") != hyp_rec.get("id"):
        return  # the brief has since been re-seeded; this search is stale
    card = _find_hyp_card(hyp_rec, hyp_rec.get("selected_id"))
    brief["hypothesis"] = (
        {
            "id": card.get("id"),
            "statement": card.get("statement", ""),
            "rationale": card.get("rationale", ""),
            "note": hyp_rec.get("select_note"),
        }
        if card
        else None
    )
    _onboarding.save_brief(ctx.state, brief)


# ---------- onboarding (O1): problem brief → data → hypothesis search → launch ----------
#
# The human defines the problem in a fixed format, attaches library files, and
# runs the hypothesis search BEFORE the research session exists. Launch freezes
# the brief into the session dir and hands it to the supervisor as its first
# turn. Rendering/validation live in api/onboarding.py; plan in
# docs/plans/O1-onboarding.md.


def _safe_bid(raw: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", raw or ""):
        raise HTTPException(400, f"invalid brief id: {raw!r}")
    return raw


def _load_brief_or_404(ctx: UserContext, bid: str) -> dict:
    rec = _onboarding.load_brief(ctx.state, _safe_bid(bid))
    if rec is None:
        raise HTTPException(404, "unknown brief")
    return rec


def _resolve_brief_data(ctx: UserContext, rec: dict) -> list[str]:
    """Validate every attached data path against the library (traversal-guarded)
    and stamp sizes. Returns a list of problems (empty when all resolve)."""
    problems: list[str] = []
    for item in rec.get("data") or []:
        raw = item.get("path", "")
        try:
            rel = _safe_library_relpath(raw)
            target = _resolve_library_path(ctx, rel)
            exists = target.exists()
        except HTTPException as e:
            problems.append(f"{raw}: {e.detail}")
            continue
        except (OSError, ValueError) as e:
            # NUL bytes, over-long names, unreadable mounts: a data problem the
            # human can fix, never a 500.
            problems.append(f"{raw}: invalid path ({e.__class__.__name__})")
            continue
        if not exists:
            problems.append(f"{raw}: not found in the library")
            continue
        item["path"] = rel.as_posix()
        if target.is_file():
            try:
                item["size"] = target.stat().st_size
            except OSError:
                item["size"] = None
        else:
            # A folder: size is the sum of its files (folder uploads keep
            # structure); keep a bounded inner listing for the brief. Skip
            # dotfiles/hidden dirs, matching what the library view shows and
            # what fs_read will serve.
            total = 0
            count = 0
            files: list[str] = []
            for p in sorted(target.rglob("*")):
                if not p.is_file():
                    continue
                inner = p.relative_to(target)
                if any(seg.startswith(".") for seg in inner.parts):
                    continue
                count += 1
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
                if len(files) < _onboarding.FOLDER_LISTING_LIMIT:
                    files.append(inner.as_posix())
            item["size"] = total
            item["is_dir"] = True
            item["file_count"] = count
            item["files"] = files
    return problems


def _apply_brief_patch(rec: dict, fields: dict) -> None:
    try:
        _onboarding.merge_fields(rec, fields)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/onboarding/briefs", response_model=schemas.BriefRecord)
def create_brief(body: schemas.ProblemBrief, ctx: UserContext = Depends(_ctx)):
    rec = _onboarding.new_record({})
    _apply_brief_patch(rec, body.model_dump())
    _onboarding.save_brief(ctx.state, rec)
    return rec


@app.get("/onboarding/briefs", response_model=list[schemas.BriefSummary])
def list_briefs(ctx: UserContext = Depends(_ctx)):
    return [_onboarding.summary(r) for r in _onboarding.list_briefs(ctx.state)]


@app.get("/onboarding/briefs/{bid}", response_model=schemas.BriefRecord)
def get_brief(bid: str, ctx: UserContext = Depends(_ctx)):
    return _load_brief_or_404(ctx, bid)


@app.put("/onboarding/briefs/{bid}", response_model=schemas.BriefRecord)
def update_brief(bid: str, body: schemas.BriefPatch, ctx: UserContext = Depends(_ctx)):
    rec = _load_brief_or_404(ctx, bid)
    if rec.get("status") == "launched":
        raise HTTPException(409, "brief already launched; it is frozen with its session")
    _apply_brief_patch(rec, body.model_dump(exclude_unset=True))
    _onboarding.save_brief(ctx.state, rec)
    return rec


@app.delete("/onboarding/briefs/{bid}")
def delete_brief(bid: str, ctx: UserContext = Depends(_ctx)):
    _load_brief_or_404(ctx, bid)
    _onboarding.brief_path(ctx.state, _safe_bid(bid)).unlink(missing_ok=True)
    return {"ok": True, "brief_id": bid}


@app.get("/onboarding/briefs/{bid}/preview", response_model=schemas.BriefPreview)
def preview_brief(bid: str, ctx: UserContext = Depends(_ctx)):
    """Exactly what the supervisor will receive as its first turn, plus which
    required fields are still empty — so the human reviews the real thing."""
    rec = _load_brief_or_404(ctx, bid)
    # Resolve data on a copy so the preview shows sizes exactly as launch will,
    # and surfaces unresolvable attachments before the human hits Launch.
    probe = json.loads(json.dumps(rec))
    problems = _resolve_brief_data(ctx, probe)
    text = _onboarding.opening_message(probe, ctx.library)
    size = len(text.encode("utf-8"))
    return schemas.BriefPreview(
        text=text,
        missing=_onboarding.missing_required(rec),
        data_problems=problems,
        size_bytes=size,
        max_bytes=_onboarding.OPENING_MESSAGE_MAX_BYTES,
        too_large=size > _onboarding.OPENING_MESSAGE_MAX_BYTES,
    )


@app.post("/onboarding/briefs/{bid}/hypotheses")
async def brief_hypotheses(
    bid: str, req: schemas.BriefHypothesesRequest, ctx: UserContext = Depends(_ctx)
):
    """Run the hypothesis search seeded from the whole brief (not a one-liner)
    and link the resulting session to the brief. Refine/select through the
    regular ``/hypothesis/{hid}/*`` routes; a select syncs back into the brief."""
    rec = _load_brief_or_404(ctx, bid)
    if rec.get("status") == "launched":
        raise HTTPException(409, "brief already launched")
    goal, context = _onboarding.brief_goal(rec)
    if not goal:
        raise HTTPException(422, "fill in the research question (or title) before generating hypotheses")
    n = max(2, min(6, req.n or 4))
    hyps, served = await _hypothesis.generate(goal, n=n, context=context)
    if not hyps:
        raise HTTPException(502, "hypothesis generation returned nothing; try sharpening the research question")
    # Persist the brief context on the search so refine rounds stay grounded
    # in the same problem (and survive later edits/deletion of the brief).
    hyp_rec = _create_hyp_session(ctx, goal, hyps, served, brief_id=rec["id"], context=context)
    rec["hypothesis_session_id"] = hyp_rec["id"]
    rec["hypothesis"] = None  # a fresh search resets the selection
    _onboarding.save_brief(ctx.state, rec)
    return hyp_rec


# Briefs whose launch is in flight (the model-free part is quick, but the spawn
# awaits). Two concurrent launches of one brief must not spawn two runtimes.
_LAUNCHING: set[tuple[str, str]] = set()


@app.post("/onboarding/briefs/{bid}/launch", response_model=schemas.LaunchBriefResponse)
async def launch_brief(
    bid: str, req: schemas.LaunchBriefRequest, ctx: UserContext = Depends(_ctx)
):
    """Freeze the brief into a new research session and start its first turn."""
    key = (ctx.user.user_id, _safe_bid(bid))
    if key in _LAUNCHING:
        raise HTTPException(409, "this brief is already being launched")
    _LAUNCHING.add(key)
    try:
        return await _launch_brief(bid, req, ctx)
    finally:
        _LAUNCHING.discard(key)


async def _launch_brief(bid: str, req: schemas.LaunchBriefRequest, ctx: UserContext):
    rec = _load_brief_or_404(ctx, bid)
    if rec.get("status") == "launched" and rec.get("session_id"):
        raise HTTPException(409, f"brief already launched as session {rec['session_id']}")
    missing = _onboarding.missing_required(rec)
    if missing:
        raise HTTPException(422, {"missing": missing, "detail": "required fields are empty"})
    problems = _resolve_brief_data(ctx, rec)
    if problems:
        raise HTTPException(422, {"data": problems, "detail": "attached data could not be resolved"})

    # The working hypothesis is server-authoritative: re-read the linked search
    # so a selection made on the Hypotheses page after the last sync still lands.
    hid = rec.get("hypothesis_session_id")
    if hid:
        hyp_rec = read_json(_hyp_path(ctx, hid), None)
        if isinstance(hyp_rec, dict):
            card = _find_hyp_card(hyp_rec, hyp_rec.get("selected_id"))
            if card:
                rec["hypothesis"] = {
                    "id": card.get("id"),
                    "statement": card.get("statement", ""),
                    "rationale": card.get("rationale", ""),
                    "note": hyp_rec.get("select_note"),
                }

    sid = _new_session_id()
    title = rec["title"].strip()
    frozen = dict(rec)
    frozen.update({"status": "launched", "session_id": sid, "launched": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    message = _onboarding.opening_message(frozen, ctx.library)
    # The message is one argv element; Linux caps those at 128 KiB (E2BIG).
    # Refuse before touching any state rather than half-creating a session.
    size = len(message.encode("utf-8"))
    if size > _onboarding.OPENING_MESSAGE_MAX_BYTES:
        raise HTTPException(
            422,
            {
                "detail": f"brief too large to hand to the agent ({size} bytes > {_onboarding.OPENING_MESSAGE_MAX_BYTES}); shorten the free-text fields or put long material in an attached file",
                "size_bytes": size,
                "max_bytes": _onboarding.OPENING_MESSAGE_MAX_BYTES,
            },
        )

    _ensure_session_dirs(ctx, sid)
    write_json(ctx.session_dir(sid) / _onboarding.BRIEF_FILENAME, frozen)

    if req.autonomous:
        _set_autonomous(ctx, sid)
        _append_event(ctx, sid, actor="human", kind="session.autonomous")
    # `task` is the short title so the session list stays readable; the full
    # brief rides in the first-turn message and in the session.brief event.
    _append_event(ctx, sid, actor="human", kind="research.requested", task=title, brief_id=rec["id"])
    hyp = frozen.get("hypothesis") or None
    _append_event(
        ctx,
        sid,
        actor="human",
        kind="session.brief",
        brief_id=rec["id"],
        title=title,
        research_question=frozen.get("research_question", ""),
        hypothesis=hyp.get("statement") if isinstance(hyp, dict) else None,
        data=[d.get("path") for d in frozen.get("data") or []],
        text=_onboarding.render_brief(frozen, ctx.library),
    )
    _clear_stop(ctx, sid)
    try:
        await _spawn_research(ctx, sid, message)
    except Exception as e:
        # Spawn failed (E2BIG, missing interpreter, ...): remove the half-made
        # session so nothing orphaned shows up in the list, keep the brief a
        # draft so the human can fix and retry, and say why.
        shutil.rmtree(ctx.session_dir(sid), ignore_errors=True)
        raise HTTPException(500, f"could not start the research runtime: {e}")

    rec.update({"status": "launched", "session_id": sid})
    _onboarding.save_brief(ctx.state, rec)
    # R19: derive the goal ledger (if not yet) and propose what the first
    # subgoals will need — proactively, before the session runs into the gap.
    _schedule_plan(ctx, rec["id"], trigger="launch", delay=0)
    return schemas.LaunchBriefResponse(session_id=sid, task=title, brief_id=rec["id"])


# ---------- R19: guided evolution search (goal ledger, planner, judge, modes) ----------
#
# HyperAgents' loop with the human (or, in automatic mode, an independent LLM
# judge on another provider) as the selector: the planner proposes the next
# modification from the researcher's sequential goals + the eval folder; a
# decision at gate 1 launches the unchanged evolution agent; the judge reviews
# the merge request at gate 2 (recommendation in manual mode, the answer in
# automatic mode); the outcome and later adoption feed the next planner run.
# See docs/plans/R19-guided-evolution-search.md.

_LLM_COMPLETE = llm_sdk.complete_with_sdk_fallback   # direct API, then the Agent SDK; monkeypatched by tests (offline)
_PLAN_TASKS: set[asyncio.Task] = set()
_GATE_WATCHERS: set[asyncio.Task] = set()


def _judge_dir(ctx: UserContext, sid: str) -> Path:
    return ctx.session_dir(sid) / "hitl" / "judge"


def _judge_for(ctx: UserContext) -> _judge.Judge:
    """One judge per call; tests monkeypatch this to inject a fake model call."""
    return _judge.Judge()


def _evo_running(ctx: UserContext) -> bool:
    return any(k[0] == ctx.user.user_id and k[1].startswith("evo-") for k in list(_RUNNING))


def _spawn_task(registry: set[asyncio.Task], coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    registry.add(task)
    task.add_done_callback(registry.discard)
    return task


def _answer_pending(ctx: UserContext, sid: str, request_id: str, decision: str, note: str | None, *, actor: str, record: bool = True) -> dict:
    """Answer a pending HITL request through the same file protocol the UI uses.
    ``actor`` is "human" (the route) or "judge" (automatic mode, gate 2)."""
    pending = _pending_dir(ctx, sid) / f"{request_id}.json"
    if not pending.exists():
        raise HTTPException(404, f"no pending HITL request {request_id}")
    rec = read_json(pending, {})
    rec["decision"] = decision
    rec["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec["note"] = note
    if actor != "human":
        rec["auto"] = True
        rec["answered_by"] = actor
    write_json(_answered_dir(ctx, sid) / f"{request_id}.json", rec)
    pending.unlink(missing_ok=True)
    if actor == "human":
        _append_event(ctx, sid, actor="human", kind="hitl.answer", ref=request_id, decision=decision)
    else:
        _append_event(ctx, sid, actor=actor, kind="hitl.auto_answer", ref=request_id, decision=decision, note=note or "")
    # A human decision on an evolution merge gate is part of the eval folder the
    # planner and the judge read next time (the judge's own is recorded by its caller).
    if record and rec.get("kind") == "evolution_merge" and actor == "human":
        prop = evo_store.proposal_for_session(ctx.state, sid)
        if prop:
            evo_store.record_decision(
                ctx.state, proposal_id=prop["id"], actor="human", stage="merge",
                decision="approve" if decision == "approve" else "reject", note=note or "",
            )
    return rec


def _resolve_base_for(ctx: UserContext, parent_version: str | None) -> str | None:
    """The commit to branch a proposal's evolution from: its parent version's sha
    when that is not already HEAD (so 'explore both' children are siblings of the
    same parent), else None (= HEAD)."""
    if not parent_version:
        return None
    try:
        vl = archive.list_versions(ctx.root)
    except Exception:  # noqa: BLE001
        return None
    for v in vl.get("versions") or []:
        if v.get("id") == parent_version:
            return None if v.get("sha") == vl.get("head") else v.get("sha")
    return None


async def _launch_evolution(
    ctx: UserContext, command: str, base: str | None, *, proposal: dict | None = None,
    scope: str = "harness", actor: str = "human", why: str | None = None,
) -> str:
    """The one place an evolution session is created (the route, a human pick,
    or the judge in automatic mode all come through here)."""
    _guard_not_switching(ctx)
    command = (command or "").strip()
    if not command:
        raise HTTPException(400, "command must not be empty")
    if scope not in ("harness", "platform"):
        raise HTTPException(400, f"invalid scope: {scope!r}")
    if base and not re.fullmatch(r"[0-9a-fA-F]{4,40}", base):
        raise HTTPException(400, f"invalid base sha: {base!r}")
    if scope == "platform" and not _platform_evolution_available():
        raise HTTPException(501, "platform-scope evolutions need COSCIENTIST_PLATFORM_REPO to point at a git checkout of the shared platform")
    sid = "evo-" + _new_session_id()
    _ensure_session_dirs(ctx, sid)
    _append_event(
        ctx, sid, actor=actor, kind="evolution.requested", command=command, base=base or "HEAD",
        scope=scope, proposal_id=(proposal or {}).get("id"),
    )
    if actor == "judge" and why:
        _append_event(ctx, sid, actor="judge", kind="evolution.judge_approved", why=why, proposal_id=(proposal or {}).get("id"))
    _clear_stop(ctx, sid)
    await _spawn_evolution(ctx, sid, command, base, scope=scope)
    if proposal:
        evo_store.set_status(ctx.state, proposal, "implementing", session_id=sid, launched_by=actor)
        evo_store.event(ctx.state, "proposal.launched", proposal_id=proposal["id"], session_id=sid, actor=actor, brief_id=proposal.get("brief_id"), title=proposal.get("title"), why=why or "")
    _spawn_task(_GATE_WATCHERS, _watch_merge_gate(ctx, sid))
    return sid


def _platform_evolution_available() -> bool:
    return bool(settings.PLATFORM_EVOLUTION) and (settings.PLATFORM_REPO / ".git").exists()


async def _launch_proposal(ctx: UserContext, prop: dict, *, actor: str, why: str | None = None) -> str:
    base = _resolve_base_for(ctx, prop.get("parent_version"))
    return await _launch_evolution(ctx, prop["command"], base, proposal=prop, scope=prop.get("scope") or "harness", actor=actor, why=why)


async def _launch_next_queued(ctx: UserContext) -> str | None:
    """Drain the approved queue one at a time (a tenant runs one evolution at a
    time so merges never race). Queued = approved by a human or the judge."""
    if _evo_running(ctx) or _is_switch_locked(ctx):
        return None
    queued = evo_store.list_proposals(ctx.state, statuses=("queued",))
    if not queued:
        return None
    queued.sort(key=lambda p: (-float((p.get("judge") or {}).get("score") or 0.0), p.get("created") or ""))
    prop = queued[0]
    actor = (prop.get("human") or {}).get("decision") and "human" or "judge"
    try:
        return await _launch_proposal(ctx, prop, actor=actor, why=((prop.get("judge") or {}).get("why") if actor == "judge" else None))
    except HTTPException as e:
        evo_store.event(ctx.state, "proposal.launch_failed", proposal_id=prop["id"], error=str(e.detail))
        evo_store.set_status(ctx.state, prop, "ended", error=str(e.detail))
        return None


async def _auto_launch(ctx: UserContext, brief_id: str | None = None) -> None:
    """Automatic mode: every judge-approved proposal launches (one at a time;
    the rest queue), and the judge's reason is written where the human sees it."""
    if evo_store.load_settings(ctx.state)["mode"] != "automatic":
        return
    cands = [
        p for p in evo_store.list_proposals(ctx.state, brief_id=brief_id, statuses=("proposed",))
        if (p.get("judge") or {}).get("verdict") == "approve"
    ]
    cands.sort(key=lambda p: (-float((p.get("judge") or {}).get("score") or 0.0), p.get("created") or ""))
    for p in cands:
        j = p.get("judge") or {}
        evo_store.set_status(ctx.state, p, "queued", auto=True)
        evo_store.event(ctx.state, "proposal.auto_approved", proposal_id=p["id"], brief_id=p.get("brief_id"), title=p.get("title"), why=j.get("why", ""), score=j.get("score"), recommendation=j.get("recommendation", ""))
    await _launch_next_queued(ctx)


def _schedule_plan(ctx: UserContext, bid: str, *, trigger: str, delay: float = 0.0) -> None:
    if not settings.EVO_PLAN:
        return

    async def _task() -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            res = await _planner.run(
                ctx, bid, trigger=trigger, judge=_judge_for(ctx), complete=_LLM_COMPLETE,
                api_key=_read_user_agent_key(ctx), platform_root=settings.PLATFORM_REPO,
            )
            if res.status == "ok":
                await _auto_launch(ctx, bid)
        except Exception:  # noqa: BLE001 — advisory background work; never crash the server
            pass

    _spawn_task(_PLAN_TASKS, _task())


def _schedule_plan_for_session(ctx: UserContext, sid: str, *, trigger: str) -> None:
    brief = _read_session_brief(ctx, sid)
    bid = (brief or {}).get("id") or (brief or {}).get("brief_id")
    if bid and evo_store.safe_id(bid):
        _schedule_plan(ctx, bid, trigger=trigger, delay=float(settings.EVO_PLAN_DELAY_S))


def _record_adoption(ctx: UserContext, sid: str) -> None:
    """After a research turn: which tools were used, on which version — the
    cheap 'did the evolution get adopted?' signal for the planner."""
    try:
        counts: dict[str, int] = {}
        for e in _tail_events(ctx, sid):
            if e.get("kind") == "tool.use":
                t = str(e.get("tool") or "?")
                counts[t] = counts.get(t, 0) + 1
        if not counts:
            return
        try:
            active = archive.list_versions(ctx.root).get("active") or "bootstrap"
        except Exception:  # noqa: BLE001
            active = "bootstrap"
        evo_store.record_session_adoption(ctx.state, active, counts, sid)
    except Exception:  # noqa: BLE001
        pass


async def _after_evolution(ctx: UserContext, sid: str) -> None:
    """Close the loop for an evolution session that just exited: link the
    proposal to its outcome, re-plan after a merge, drain the queue."""
    prop = evo_store.proposal_for_session(ctx.state, sid)
    if prop and prop.get("status") == "implementing":
        events = _tail_events(ctx, sid)
        kinds = [e.get("kind") for e in events]
        version_id = next((e.get("version") for e in events if e.get("kind") == "version.recorded"), None)
        if "evolution.merged" in kinds or version_id:
            evo_store.set_status(ctx.state, prop, "merged", version_id=version_id)
            _schedule_plan(ctx, prop["brief_id"], trigger="evolution.merged", delay=0)
        elif "evolution.rejected" in kinds or "evolution.auto_reject" in kinds:
            evo_store.set_status(ctx.state, prop, "rejected")
        else:
            evo_store.set_status(ctx.state, prop, "ended")
    await _launch_next_queued(ctx)


async def _judge_merge_request(ctx: UserContext, sid: str, rid: str, rec: dict, counter: dict) -> None:
    judge = _judge_for(ctx)
    mode = evo_store.load_settings(ctx.state)["mode"]
    prop = evo_store.proposal_for_session(ctx.state, sid)
    ledger = evo_store.load_goals(ctx.state, prop["brief_id"]) if prop and prop.get("brief_id") else None
    verdict = await judge.judge_merge(
        rec.get("payload") or {}, proposal=prop,
        goals=_goals.render_for_prompt(ledger) if ledger else "",
        inventory=_goals.render_inventory(_goals.harness_inventory(ctx.root)),
        tree=_planner.tree_text(ctx.root, ctx.state),
        history=_judge.history_block(evo_store.decisions(ctx.state, limit=200), {p["id"]: p for p in evo_store.list_proposals(ctx.state)}),
        mode=mode,
    )
    jrec = verdict.to_record("merge")
    jrec.update({"request_id": rid, "mode": mode})
    write_json(_judge_dir(ctx, sid) / f"{rid}.json", jrec)
    _append_event(ctx, sid, actor="judge", kind="judge.review", ref=rid, verdict=verdict.verdict, score=verdict.score, why=verdict.why, recommendation=verdict.recommendation, error=verdict.error)
    if prop and verdict.ok:
        evo_store.record_decision(ctx.state, proposal_id=prop["id"], actor="judge", stage="merge", decision="approve" if verdict.approved else "reject", note=verdict.why, score=verdict.score, served_by=verdict.served_by)
    if mode != "automatic" or not verdict.ok:
        return
    if verdict.approved:
        _answer_pending(ctx, sid, rid, "approve", f"Judge approved: {verdict.why}", actor="judge", record=False)
    elif counter["rejects"] < settings.JUDGE_MAX_AUTO_REJECTS:
        counter["rejects"] += 1
        risks = ("; ".join(verdict.risks)) if verdict.risks else ""
        _answer_pending(ctx, sid, rid, "reject", f"Judge requests changes: {verdict.why}" + (f" Risks: {risks}" if risks else ""), actor="judge", record=False)
    else:
        _append_event(ctx, sid, actor="judge", kind="judge.deferred_to_human", ref=rid, why=f"declined {counter['rejects']} times; a human must decide")


async def _watch_merge_gate(ctx: UserContext, sid: str) -> None:
    """While an evolution session runs, review every ``evolution_merge`` request
    it opens: a recommendation beside the approval card (manual) or the answer
    itself (automatic). Errors never escape; the human gate still works without it."""
    key = _monitor_key(ctx, sid)
    seen: set[str] = set()
    counter = {"rejects": 0}
    while key in _RUNNING:
        try:
            pdir = _pending_dir(ctx, sid)
            for path in (sorted(pdir.glob("*.json")) if pdir.exists() else []):
                rec = read_json(path, {})
                rid = str(rec.get("id") or path.stem)
                if rec.get("kind") != "evolution_merge" or rid in seen:
                    continue
                seen.add(rid)
                await _judge_merge_request(ctx, sid, rid, rec, counter)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(settings.JUDGE_MERGE_POLL_S)


def _ledger_or_404(ctx: UserContext, bid: str) -> tuple[dict, dict | None]:
    bid = evo_store.safe_id(bid) or ""
    if not bid:
        raise HTTPException(400, "invalid brief id")
    brief = _onboarding.load_brief(ctx.state, bid)
    ledger = evo_store.load_goals(ctx.state, bid)
    if ledger is None:
        if brief is None:
            raise HTTPException(404, "unknown brief")
        ledger = _goals.empty_ledger(bid, brief)
    return ledger, brief


@app.get("/evolution/mode")
def evo_mode(ctx: UserContext = Depends(_ctx)):
    rec = evo_store.load_settings(ctx.state)
    j = _judge_for(ctx)
    return {"mode": rec["mode"], "updated": rec.get("updated"), "judge": {"available": j.available(), "model": j.model}}


@app.put("/evolution/mode")
async def set_evo_mode(body: schemas.EvoModeUpdate, ctx: UserContext = Depends(_ctx)):
    if body.mode not in evo_store.MODES:
        raise HTTPException(400, f"mode must be one of {list(evo_store.MODES)}")
    if body.mode == "automatic" and not _judge_for(ctx).available():
        raise HTTPException(409, "automatic mode needs the LLM judge; configure COSCIENTIST_JUDGE_OPENAI_API_KEY (or state/secrets/judge_openai_key) first")
    rec = evo_store.set_mode(ctx.state, body.mode)
    if body.mode == "automatic":
        await _auto_launch(ctx)  # already-approved proposals go now
    j = _judge_for(ctx)
    return {"mode": rec["mode"], "updated": rec.get("updated"), "judge": {"available": j.available(), "model": j.model}}


@app.get("/evolution/judge")
def evo_judge(ctx: UserContext = Depends(_ctx)):
    j = _judge_for(ctx)
    return {"available": j.available(), "model": j.model, "calibration": _judge.calibration(evo_store.decisions(ctx.state))}


@app.get("/evolution/goals")
def evo_goals_list(ctx: UserContext = Depends(_ctx)):
    return [_goals.summary(l) for l in evo_store.list_goals(ctx.state)]


@app.get("/evolution/goals/{bid}")
def evo_goals_get(bid: str, ctx: UserContext = Depends(_ctx)):
    ledger, _brief = _ledger_or_404(ctx, bid)
    return ledger


@app.post("/evolution/goals/{bid}/derive")
async def evo_goals_derive(bid: str, body: schemas.GoalsDeriveRequest | None = None, ctx: UserContext = Depends(_ctx)):
    ledger, brief = _ledger_or_404(ctx, bid)
    if brief is None:
        raise HTTPException(404, "unknown brief")
    if body and body.approach_hints is not None:
        ledger["approach_hints"] = body.approach_hints[:4000]
    inv = _goals.harness_inventory(ctx.root)
    ledger = await _goals.derive(
        ledger, _onboarding.render_brief(brief, ctx.library), inv,
        complete=_LLM_COMPLETE, api_key=_read_user_agent_key(ctx),
        keep_subgoals=body.keep_subgoals if body else None,
    )
    _goals.refresh_wishlist_status(ledger, inv, evo_store.list_proposals(ctx.state, brief_id=bid))
    evo_store.save_goals(ctx.state, ledger)
    evo_store.event(ctx.state, "goal.derived", brief_id=bid, subgoals=len(ledger.get("subgoals", [])), served_by=(ledger.get("derived") or {}).get("served_by"), error=(ledger.get("derived") or {}).get("error"))
    return ledger


@app.put("/evolution/goals/{bid}")
def evo_goals_patch(bid: str, body: schemas.GoalsPatch, ctx: UserContext = Depends(_ctx)):
    ledger, _brief = _ledger_or_404(ctx, bid)
    patch = body.model_dump(exclude_unset=True)
    if patch.get("subgoals") is not None:
        patch["subgoals"] = [s if isinstance(s, dict) else s for s in patch["subgoals"]]
    try:
        ledger = _goals.apply_patch(ledger, patch)
    except ValueError as e:
        raise HTTPException(422, str(e))
    evo_store.save_goals(ctx.state, ledger)
    evo_store.event(ctx.state, "goal.updated", brief_id=bid, current=ledger.get("current"), subgoals=len(ledger.get("subgoals", [])))
    return ledger


@app.post("/evolution/goals/{bid}/drift/answer")
def evo_goals_drift_answer(bid: str, body: schemas.DriftAnswer, ctx: UserContext = Depends(_ctx)):
    ledger, _brief = _ledger_or_404(ctx, bid)
    try:
        ledger = _goals.answer_drift(ledger, body.changed, body.note or "")
    except ValueError as e:
        raise HTTPException(409, str(e))
    evo_store.save_goals(ctx.state, ledger)
    evo_store.event(ctx.state, "goal.drift_answered", brief_id=bid, changed=body.changed, current=ledger.get("current"))
    return ledger


@app.post("/evolution/plan")
async def evo_plan(body: schemas.PlanRequest, ctx: UserContext = Depends(_ctx)):
    bid = evo_store.safe_id(body.brief_id) or ""
    if not bid or _onboarding.load_brief(ctx.state, bid) is None:
        raise HTTPException(404, "unknown brief")
    if _planner.is_running(ctx.state, bid):
        raise HTTPException(409, "a planner run is already in progress for this brief")
    trigger = re.sub(r"[^a-z0-9_.-]", "", (body.trigger or "manual").lower())[:40] or "manual"
    _schedule_plan(ctx, bid, trigger=trigger, delay=0)
    return {"ok": True, "status": "started", "brief_id": bid, "trigger": trigger}


@app.get("/evolution/proposals")
def evo_proposals(brief_id: str | None = None, ctx: UserContext = Depends(_ctx)):
    bid = evo_store.safe_id(brief_id) if brief_id else None
    props = evo_store.list_proposals(ctx.state, brief_id=bid)
    latest = evo_store.latest_run(ctx.state, bid) if bid else None
    return {
        "proposals": props,
        "latest_run": {k: latest.get(k) for k in ("id", "created", "trigger", "served_by", "error", "phase", "drift_opened")} if latest else None,
        "running": _planner.is_running(ctx.state, bid) if bid else False,
        "mode": evo_store.load_settings(ctx.state)["mode"],
        "evolution_running": _evo_running(ctx),
    }


@app.post("/evolution/proposals/{pid}/decide")
async def evo_decide(pid: str, body: schemas.ProposalDecision, ctx: UserContext = Depends(_ctx)):
    prop = evo_store.load_proposal(ctx.state, pid)
    if prop is None:
        raise HTTPException(404, "unknown proposal")
    if prop.get("status") not in ("proposed", "queued"):
        raise HTTPException(409, f"proposal is already {prop.get('status')}")
    note = (body.note or "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if body.decision == "decline":
        prop["human"] = {"decision": "decline", "note": note, "at": now}
        evo_store.set_status(ctx.state, prop, "declined")
        evo_store.record_decision(ctx.state, proposal_id=pid, actor="human", stage="proposal", decision="decline", note=note)
        evo_store.event(ctx.state, "proposal.declined", proposal_id=pid, brief_id=prop.get("brief_id"), note=note)
        return {"ok": True, "status": "declined", "launched": [], "queued": []}
    if body.decision not in ("pick", "both"):
        raise HTTPException(400, "decision must be pick, both or decline")
    targets = [prop]
    if body.decision == "both":
        second = evo_store.load_proposal(ctx.state, body.with_id or "")
        if second is None:
            raise HTTPException(400, "'both' needs with_id: the second proposal to explore")
        if second["id"] == prop["id"] or second.get("status") not in ("proposed", "queued"):
            raise HTTPException(409, "the second proposal is not open")
        targets.append(second)
    launched: list[dict] = []
    queued: list[str] = []
    for p in targets:
        p["human"] = {"decision": body.decision, "note": note, "at": now}
        evo_store.save_proposal(ctx.state, p)
        evo_store.record_decision(ctx.state, proposal_id=p["id"], actor="human", stage="proposal", decision=body.decision, note=note)
        if not launched and not _evo_running(ctx):
            sid = await _launch_proposal(ctx, p, actor="human")
            launched.append({"proposal_id": p["id"], "session_id": sid})
        else:
            evo_store.set_status(ctx.state, p, "queued")
            queued.append(p["id"])
    return {"ok": True, "status": "launched" if launched else "queued", "launched": launched, "queued": queued}


@app.get("/evolution/events")
def evo_events(limit: int = 100, ctx: UserContext = Depends(_ctx)):
    return {"events": evo_store.read_events(ctx.state, limit=max(1, min(int(limit), 1000)))}


# ui3 is the default UI: serve the built SPA from the API origin so the
# browser's fetches to /research, /hitl, ... need no proxy or CORS. Mounted
# last, so every API route above wins over the static catch-all.
_UI3_DIST = settings.ROOT / "ui3" / "dist"
if _UI3_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_UI3_DIST, html=True), name="ui3")
