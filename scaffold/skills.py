"""Per-user skill library (R7). A skill is an instruction-only `SKILL.md`
(Claude Code format) under the user's `.claude/skills/<name>/`, discovered by the
SDK as a "project" skill relative to the runtime's cwd. The `propose_skill` tool
lets the research agent crystallize a reusable workflow, gated by human approval.
See docs/plans/R7-skill-library.md."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from scaffold import eventlog, hitl, settings
from scaffold._atomic import write_json


def safe_name(raw: str) -> str:
    """Normalize a proposed skill name to a safe kebab-case directory name."""
    s = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")
    return s[:48]


def skill_dir(name: str) -> Path:
    return settings.SKILLS_DIR / name


def skill_path(name: str) -> Path:
    return skill_dir(name) / "SKILL.md"


def build_skill_md(name: str, description: str, body: str) -> str:
    """Render a SKILL.md with the YAML frontmatter the SDK expects."""
    desc = " ".join((description or "").split())  # single line
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{body.strip()}\n"


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal `name:`/`description:` reader — avoids a YAML dep in the substrate."""
    out: dict[str, str] = {}
    if not text.startswith("---"):
        return out
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else ""
    for line in block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            if k in ("name", "description"):
                out[k] = v.strip()
    return out


def list_skills() -> list[dict]:
    """Existing skills in this root, as {name, description}."""
    base = settings.SKILLS_DIR
    if not base.exists():
        return []
    out: list[dict] = []
    for d in sorted(base.iterdir()):
        sm = d / "SKILL.md"
        if not d.is_dir() or not sm.exists():
            continue
        fm = _parse_frontmatter(sm.read_text(encoding="utf-8"))
        out.append({"name": fm.get("name", d.name), "description": fm.get("description", "")})
    return out


def save_skill(name: str, description: str, body: str) -> Path:
    """Write `.claude/skills/<name>/SKILL.md` in the current root. Returns the path."""
    path = skill_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_skill_md(name, description, body), encoding="utf-8")
    return path


async def run_proposal(
    session_id: str, agent_id: str, name: str, description: str, body: str
) -> tuple[bool, str]:
    """Archive a skill candidate, ask the human, and save it on approval.
    Returns ``(saved, message)``. Plain function so it's testable without the
    SDK tool wrapper. Mirrors evolution/propose_merge."""
    name = safe_name(name)
    description = (description or "").strip()
    body = (body or "").strip()
    if not name:
        return False, "ERROR: invalid skill name"
    if not body:
        return False, "ERROR: skill body is empty"

    existing = {s["name"] for s in list_skills()}
    skill_md = build_skill_md(name, description, body)

    # Archive the candidate for audit (like the evolutions archive).
    ts = time.strftime("%Y%m%d-%H%M%S")
    archive_dir = settings.SKILL_PROPOSALS_ARCHIVE / f"{ts}__{name}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    write_json(archive_dir / "decision.json", {"status": "pending", "name": name})

    eventlog.append(
        session_id, actor=agent_id, kind="skill.proposed",
        name=name, ref=archive_dir.name, overwrite=name in existing,
    )

    decision = await hitl.ask(
        session_id,
        kind="skill_proposal",
        summary=f"Save skill: {name}",
        payload={
            "name": name,
            "description": description,
            "skill_md": skill_md,
            "overwrite": name in existing,
            "archive": str(archive_dir),
        },
    )

    # Only an explicit approval saves the skill. A chat reply (decision
    # "answer"), reject, or stop leaves the library unchanged.
    if decision.get("decision") == "approve":
        path = save_skill(name, description, body)
        write_json(archive_dir / "decision.json", {"status": "saved", "name": name})
        eventlog.append(session_id, actor=agent_id, kind="skill.approved", name=name, ref=archive_dir.name)
        # R17 note: a skill is a durable, cross-version asset (like memory/results),
        # NOT a version node — it is saved here and left out of git on purpose, so it
        # persists across version switches instead of time-travelling with the code.
        # (Revisit if we ever treat skills as version-bound harness — see R17 doc.)
        return True, f"SAVED skill '{name}' at {path}. It is available to your future sessions."

    note = (decision.get("note") or "").strip()
    write_json(archive_dir / "decision.json", {"status": "rejected", "name": name, "note": note})
    eventlog.append(session_id, actor=agent_id, kind="skill.rejected", name=name, ref=archive_dir.name)
    return False, f"Skill '{name}' was not saved." + (f" Feedback: {note}" if note else "")


def make_propose_skill_server(session_id: str, agent_id: str):
    """MCP server exposing `propose_skill` to the supervisor."""

    @tool(
        "propose_skill",
        "Save a reusable workflow as a skill for future sessions (asks the human "
        "to approve first). Use only for coherent, recurring workflows; don't "
        "duplicate an existing skill.",
        {"name": str, "description": str, "body": str},
    )
    async def propose_skill(args: dict[str, Any]) -> dict:
        saved, msg = await run_proposal(
            session_id, agent_id,
            args.get("name", ""), args.get("description", ""), args.get("body", ""),
        )
        result = {"content": [{"type": "text", "text": msg}]}
        if not saved and msg.startswith("ERROR:"):
            result["isError"] = True
        return result

    return create_sdk_mcp_server("propose_skill", "0.1.0", tools=[propose_skill])
