from __future__ import annotations

import os
import time
from pathlib import Path

from scaffold._atomic import read_json, write_json


ROOT = Path(os.environ.get("COSCIENTIST_ROOT", Path(__file__).resolve().parents[1]))

STATE = ROOT / "state"
SESSIONS = STATE / "sessions"

# SDK/CLI conversation transcripts. The Claude Code CLI defaults to
# ~/.claude, which lives inside the container and is wiped on `docker compose
# down`. Point it at the persistent ./state mount so `resume=<uuid>` survives
# restarts. setdefault so a host test run can override with a temp dir.
CLAUDE_CONFIG_DIR = Path(
    os.environ.setdefault("CLAUDE_CONFIG_DIR", str(STATE / ".claude"))
)
GLOBAL_MEMORY = STATE / "memory" / "global.jsonl"
EVOLUTIONS_ARCHIVE = STATE / "archive" / "evolutions"
# Per-user skill library. Discovered by the SDK as "project" skills relative to
# the agent's cwd (= ROOT in the per-tenant runtime subprocess), so each user's
# instance accrues its own skills. See docs/plans/R7-skill-library.md.
SKILLS_DIR = ROOT / ".claude" / "skills"
SKILL_PROPOSALS_ARCHIVE = STATE / "archive" / "skills"
LIBRARY = STATE / "library"
LIBRARY_STAGING = LIBRARY / ".staging"
LIBRARY_EVENTS = STATE / "library_events.jsonl"
# Soft cap for HTTP uploads only; host-drop has no cap. Override via env.
LIBRARY_MAX_BYTES = int(
    os.environ.get("LIBRARY_MAX_BYTES", str(50 * 1024 * 1024 * 1024))
)

WORKTREES = ROOT / "worktrees"
ROLES = ROOT / "roles"
PROMPTS = ROOT / "prompts"
TOOLS = ROOT / "tools"
RESEARCHER_DATA = ROOT / "researcher_data"

MODEL_SUPERVISOR = os.environ.get("COSCIENTIST_MODEL_SUPERVISOR", "claude-opus-4-7")
MODEL_SUBAGENT = os.environ.get("COSCIENTIST_MODEL_SUBAGENT", "claude-sonnet-4-6")
MODEL_EVOLUTION = os.environ.get("COSCIENTIST_MODEL_EVOLUTION", "claude-opus-4-7")

MAX_TURNS = int(os.environ.get("COSCIENTIST_MAX_TURNS", "80"))

CHECKPOINT_EVENT_INTERVAL = 10

# Evolution merge guard: never apply a merge into the user root while a research
# session is running — wait until all of them go idle, so code never changes under
# a running turn. Modeled on Claude Code's Monitor: poll-until-idle on an interval
# with a long backstop, and DON'T force on timeout (the human stops sessions to
# unblock). Cross-process signal = per-session marker files under
# state/control/research_active/ (see api.server). 0 disables the wait.
EVOLUTION_MERGE_WAIT_S = int(os.environ.get("COSCIENTIST_EVOLUTION_MERGE_WAIT_S", "3600"))
EVOLUTION_MERGE_POLL_S = int(os.environ.get("COSCIENTIST_EVOLUTION_MERGE_POLL_S", "5"))

# R12 long-job runner. The spool is a shared directory both the container and the
# host-side spooler daemon see (default: under the state mount, so no extra
# mount). In the container, point COSCIENTIST_SPOOL_DIR at the GLOBAL state mount
# (/app/state/jobspool) so one spooler serves all tenants. See
# docs/plans/R12-longjob-runner.md and deploy/flair_spooler.py.
LONGJOB_SPOOL_DIR = Path(os.environ.get("COSCIENTIST_SPOOL_DIR", str(STATE / "jobspool")))
# Default docker image for FLAIR jobs (a job may override if allow-listed by the
# spooler). Empty → the agent must pass an image in the JobSpec.
LONGJOB_JOB_IMAGE = os.environ.get("COSCIENTIST_JOB_IMAGE", "")

# Whether a research turn reflects at its end and may propose saving a reusable
# workflow as a skill (HITL-gated). Off → no reflection round-trip. See R7.
SKILL_REFLECTION = os.environ.get("COSCIENTIST_SKILL_REFLECTION", "1") not in ("0", "false", "False", "")

PYEXEC_CPU_SECONDS = int(os.environ.get("COSCIENTIST_PYEXEC_CPU_SECONDS", "60"))
PYEXEC_MEM_MB = int(os.environ.get("COSCIENTIST_PYEXEC_MEM_MB", "1024"))

# R14 deep-research tool (OpenAI Deep Research API via the Responses API).
# The tool is inert unless OPENAI_API_KEY is set — that presence is the enable
# gate, so no HITL prompt fires on the mandated once-per-session scope call.
# Set COSCIENTIST_DEEPRESEARCH_HITL=1 to gate every run behind a human approve.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")  # optional (Azure/proxy)
DEEPRESEARCH_MODEL = os.environ.get(
    "COSCIENTIST_DEEPRESEARCH_MODEL", "o4-mini-deep-research"
)
# The genuine deep-research models require OpenAI *organization verification*.
# Until the org is verified they fail mid-run ("organization must be verified").
# When that happens we retry once with this web-search-grounded fallback model
# (available without verification) so a run still produces a cited report — then
# the report is labeled as fallback output. Set empty to disable and hard-fail
# instead. Once the org is verified, the primary model succeeds and this is moot.
DEEPRESEARCH_FALLBACK_MODEL = os.environ.get(
    "COSCIENTIST_DEEPRESEARCH_FALLBACK_MODEL", "o4-mini"
)
# How long the blocking `run` call polls before degrading to a background handle.
DEEPRESEARCH_MAX_WAIT_S = int(os.environ.get("COSCIENTIST_DEEPRESEARCH_MAX_WAIT_S", "1200"))
DEEPRESEARCH_POLL_S = int(os.environ.get("COSCIENTIST_DEEPRESEARCH_POLL_S", "10"))
DEEPRESEARCH_HITL = os.environ.get("COSCIENTIST_DEEPRESEARCH_HITL", "0") not in (
    "0", "false", "False", "",
)

# R13: default format for the *written deliverable files* a research session
# produces under results/. "latex" → the agent emits a compilable .tex document
# and compiles it to PDF via the `latex_compile` tool; "markdown" → the prior
# human-readable .md behavior. Anything else falls back to "latex". This governs
# deliverable files only, not the bus chat narration (which stays markdown).
# See docs/plans/R13-latex-output.md.
OUTPUT_FORMAT = os.environ.get("COSCIENTIST_OUTPUT_FORMAT", "latex").strip().lower()
if OUTPUT_FORMAT not in ("latex", "markdown"):
    OUTPUT_FORMAT = "latex"


def ensure_runtime_dirs() -> None:
    """Create global runtime directories that exist outside any session."""
    LIBRARY.mkdir(parents=True, exist_ok=True)
    LIBRARY_STAGING.mkdir(parents=True, exist_ok=True)
    CLAUDE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def session_dir(session_id: str) -> Path:
    return SESSIONS / session_id


def sdk_session_file(session_id: str) -> Path:
    """Where we persist the SDK/CLI conversation UUID for ``session_id`` so a
    later turn can resume the same conversation."""
    return session_dir(session_id) / "sdk_session.json"


def read_sdk_session(session_id: str) -> str | None:
    """Return the persisted SDK conversation UUID for this session, or None."""
    rec = read_json(sdk_session_file(session_id), default=None)
    if isinstance(rec, dict):
        return rec.get("sdk_session_id") or None
    return None


def write_sdk_session(session_id: str, sdk_session_id: str, turns: int) -> None:
    """Persist the SDK conversation UUID + turn count for ``session_id``."""
    write_json(
        sdk_session_file(session_id),
        {
            "sdk_session_id": sdk_session_id,
            "turns": turns,
            "updated": time.time(),
        },
    )


def ensure_session_dirs(session_id: str) -> Path:
    sd = session_dir(session_id)
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
