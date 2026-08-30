"""Propose a merge: produces a diff, archives it, raises an HITL request,
blocks until decided, then merges or discards."""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import archive, eventlog, hitl, sandbox, settings
from scaffold._atomic import write_json


_STRICT_TRIGGERS = ("scaffold/", "evolution/", "pyproject.toml", "Dockerfile", "docker-compose.yml")


def _is_strict(diff: str) -> bool:
    return any(trig in diff for trig in _STRICT_TRIGGERS)


async def _run_smoke(wt: sandbox.Worktree) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_shell(
        # R17: the contract compat test rides the smoke gate — an evolution that
        # can no longer read old durable data (golden fixtures) is auto-rejected.
        "python -m pytest -q tests/test_smoke_v0.py tests/test_contract_compat.py",
        cwd=str(wt.path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "smoke timeout"
    body = (out + err).decode("utf-8", errors="replace")
    return proc.returncode == 0, body[-4000:]


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    except OSError:
        return False
    return True


def _live_research_sessions() -> list[str]:
    """Session ids with a live research subprocess, from the marker files
    api.server writes (one per running research turn, holding its PID). Stale
    markers (dead PID) are cleaned so a crashed session never blocks forever."""
    d = settings.STATE / "control" / "research_active"
    if not d.exists():
        return []
    live: list[str] = []
    for f in sorted(d.glob("*")):
        try:
            pid = int((f.read_text(encoding="utf-8").strip() or "0"))
        except (ValueError, OSError):
            f.unlink(missing_ok=True)
            continue
        if pid > 0 and _pid_alive(pid):
            live.append(f.name)
        else:
            f.unlink(missing_ok=True)
    return live


async def _wait_for_sessions_idle(session_id: str) -> bool:
    """Hold a merge until NO research session is running for this user, so code
    never changes under a running turn. Returns True when idle, False on timeout
    (the caller must NOT merge on False). Modeled on Claude Code's Monitor:
    poll on an interval with a long backstop, and DON'T force on timeout — the
    human stops sessions to unblock. Single-directional (evolution waits on
    research, never the reverse) → no deadlock cycle is possible; stale-marker
    cleanup + the timeout prevent an indefinite hang."""
    wait_s = settings.EVOLUTION_MERGE_WAIT_S
    if wait_s <= 0:
        return True
    poll_s = max(1, settings.EVOLUTION_MERGE_POLL_S)
    waited = 0
    last_note = -10_000
    while True:
        live = _live_research_sessions()
        if not live:
            return True
        if last_note < 0 or waited - last_note >= 30:
            eventlog.append(
                session_id, actor="evolution", kind="evolution.note",
                note=(f"Waiting for {len(live)} research session(s) to finish before "
                      f"merging: {', '.join(live)}. Stop them to merge now."),
            )
            last_note = waited
        if waited >= wait_s:
            return False
        await asyncio.sleep(poll_s)
        waited += poll_s


def make_server(session_id: str, wt: sandbox.Worktree):
    @tool("propose_merge", "Snapshot edits, run smoke for sensitive changes, ask the human to merge.", {
        "rationale": str,
        "summary": str,
    })
    async def propose(args):
        rationale = args.get("rationale", "")
        summary = args.get("summary", "")
        commit_sha = sandbox.commit_all(wt, message=f"evo: {summary or 'proposal'}")
        if not commit_sha:
            return {"content": [{"type": "text", "text": "ERROR: no changes to merge"}], "isError": True}
        diff = sandbox.diff(wt)
        ts = time.strftime("%Y%m%d-%H%M%S")
        slug = wt.branch.split("/", 1)[-1]
        archive_dir = settings.EVOLUTIONS_ARCHIVE / f"{ts}__{slug}"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "diff.patch").write_text(diff, encoding="utf-8")
        (archive_dir / "rationale.md").write_text(
            f"# {summary}\n\n{rationale}\n\nbranch: `{wt.branch}`\nbase: `{wt.base}`\n", encoding="utf-8"
        )
        write_json(archive_dir / "decision.json", {"status": "pending", "branch": wt.branch})

        strict = _is_strict(diff)
        eventlog.append(
            session_id, actor="evolution", kind="evolution.proposal",
            ref=str(archive_dir.name), strict=strict,
        )

        smoke_info: dict = {"ran": False}
        if strict:
            ok, smoke_text = await _run_smoke(wt)
            (archive_dir / "smoke.log").write_text(smoke_text, encoding="utf-8")
            smoke_info = {"ran": True, "ok": ok}
            if not ok:
                write_json(archive_dir / "decision.json", {"status": "auto_rejected", "reason": "smoke failed"})
                eventlog.append(session_id, actor="evolution", kind="evolution.auto_reject", ref=str(archive_dir.name))
                return {"content": [{"type": "text", "text": "REJECTED: smoke tests failed in worktree. Decision archived."}], "isError": True}

        decision = await hitl.ask(
            session_id,
            kind="evolution_merge",
            summary=f"Merge proposal: {summary}",
            payload={
                "archive": str(archive_dir),
                "branch": wt.branch,
                "diff_preview": diff[:8000],
                "strict": strict,
                "rationale": rationale,
            },
        )

        if decision.get("decision") == "approve":
            # Guard: never merge into the user root while a research session runs.
            if not await _wait_for_sessions_idle(session_id):
                write_json(archive_dir / "decision.json", {"status": "deferred", "reason": "sessions_running"})
                eventlog.append(
                    session_id, actor="evolution", kind="evolution.note",
                    note=("Merge deferred: research session(s) still running after the wait "
                          "window. Stop them and re-run this evolution to merge. Worktree preserved."),
                )
                return {"content": [{"type": "text", "text": (
                    "DEFERRED: research session(s) still running; merge NOT applied. "
                    "Stop them, then re-run this evolution. Your worktree is preserved."
                )}], "isError": True}
            head = sandbox.merge_to_main(wt)
            # Compute revert.patch (reverse diff) for rollback.
            revert = subprocess.check_output(
                ["git", "diff", head, wt.base], cwd=str(settings.ROOT), text=True
            )
            (archive_dir / "revert.patch").write_text(revert, encoding="utf-8")
            write_json(archive_dir / "decision.json", {"status": "merged", "head": head})
            # R17: promote the merged evolution to a first-class version node —
            # tag ver/<id> + archive manifest — so it becomes switchable. Never
            # fatal: the merge already landed; a manifest hiccup must not fail it.
            try:
                node = archive.record_merged_version(
                    settings.ROOT, archive_dir=archive_dir, head_sha=head,
                    base_sha=wt.base, summary=summary, rationale=rationale,
                    owner=archive.owner_of(settings.ROOT), smoke=smoke_info,
                )
                eventlog.append(session_id, actor="evolution", kind="version.recorded",
                                ref=str(archive_dir.name), version=node["id"], tag=node["tag"])
            except Exception as e:
                eventlog.append(session_id, actor="evolution", kind="version.record_error", error=str(e))
            sandbox.remove_after_merge(wt)
            eventlog.append(session_id, actor="evolution", kind="evolution.merged", ref=str(archive_dir.name))
            return {"content": [{"type": "text", "text": f"MERGED as version {archive_dir.name}. Restart sessions to pick up changes."}]}
        else:
            decision_kind = decision.get("decision", "reject")
            note = decision.get("note", "") or ""
            write_json(archive_dir / "decision.json", {"status": "rejected", "note": note})

            # A stop/interrupt tears the session down — discard the worktree.
            # (reject_all_pending marks these with note=="stopped"; the stop
            # flag yields decision=="interrupted".)
            if decision_kind != "reject" or note == "stopped":
                sandbox.discard(wt)
                eventlog.append(session_id, actor="evolution", kind="evolution.rejected", ref=str(archive_dir.name))
                return {"content": [{"type": "text", "text": f"REJECTED. {note}"}]}

            # A genuine human reject keeps the worktree so the agent can revise
            # in place and re-propose without losing its committed work.
            eventlog.append(
                session_id, actor="evolution", kind="evolution.rejected",
                ref=str(archive_dir.name), retained=True,
            )
            return {"content": [{"type": "text", "text": (
                f"REJECTED. {note}\n\n"
                "Your worktree is preserved and your previous changes are still "
                "committed on its branch. Address the feedback with further edits "
                "in the worktree, then call propose_merge again — do NOT start over."
            )}]}

    return create_sdk_mcp_server("propose_merge", "0.1.0", tools=[propose])
