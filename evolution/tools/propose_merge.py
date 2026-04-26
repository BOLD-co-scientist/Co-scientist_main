"""Propose a merge: produces a diff, archives it, raises an HITL request,
blocks until decided, then merges or discards."""
from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import eventlog, hitl, sandbox, settings
from scaffold._atomic import write_json


_STRICT_TRIGGERS = ("scaffold/", "evolution/", "pyproject.toml", "Dockerfile", "docker-compose.yml")


def _is_strict(diff: str) -> bool:
    return any(trig in diff for trig in _STRICT_TRIGGERS)


async def _run_smoke(wt: sandbox.Worktree) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_shell(
        "pytest -q tests/test_smoke_v0.py",
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

        eventlog.append(
            session_id, actor="evolution", kind="evolution.proposal",
            ref=str(archive_dir.name), strict=_is_strict(diff),
        )

        if _is_strict(diff):
            ok, smoke_text = await _run_smoke(wt)
            (archive_dir / "smoke.log").write_text(smoke_text, encoding="utf-8")
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
                "strict": _is_strict(diff),
                "rationale": rationale,
            },
        )

        if decision.get("decision") == "approve":
            head = sandbox.merge_to_main(wt)
            # Compute revert.patch (reverse diff) for rollback.
            revert = subprocess.check_output(
                ["git", "diff", head, wt.base], cwd=str(settings.ROOT), text=True
            )
            (archive_dir / "revert.patch").write_text(revert, encoding="utf-8")
            write_json(archive_dir / "decision.json", {"status": "merged", "head": head})
            sandbox.remove_after_merge(wt)
            eventlog.append(session_id, actor="evolution", kind="evolution.merged", ref=str(archive_dir.name))
            return {"content": [{"type": "text", "text": f"MERGED. Archived at {archive_dir.name}. Restart sessions to pick up changes."}]}
        else:
            note = decision.get("note", "")
            write_json(archive_dir / "decision.json", {"status": "rejected", "note": note})
            sandbox.discard(wt)
            eventlog.append(session_id, actor="evolution", kind="evolution.rejected", ref=str(archive_dir.name))
            return {"content": [{"type": "text", "text": f"REJECTED. {note}"}]}

    return create_sdk_mcp_server("propose_merge", "0.1.0", tools=[propose])
