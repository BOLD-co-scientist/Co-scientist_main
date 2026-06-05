from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(os.environ.get("COSCIENTIST_ROOT", Path(__file__).resolve().parents[1]))

STATE = ROOT / "state"
SESSIONS = STATE / "sessions"
GLOBAL_MEMORY = STATE / "memory" / "global.jsonl"
EVOLUTIONS_ARCHIVE = STATE / "archive" / "evolutions"
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

CHECKPOINT_EVENT_INTERVAL = int(os.environ.get("COSCIENTIST_CHECKPOINT_EVENTS", "20"))

PYEXEC_CPU_SECONDS = int(os.environ.get("COSCIENTIST_PYEXEC_CPU_SECONDS", "60"))
PYEXEC_MEM_MB = int(os.environ.get("COSCIENTIST_PYEXEC_MEM_MB", "1024"))


def ensure_runtime_dirs() -> None:
    """Create global runtime directories that exist outside any session."""
    LIBRARY.mkdir(parents=True, exist_ok=True)
    LIBRARY_STAGING.mkdir(parents=True, exist_ok=True)


def session_dir(session_id: str) -> Path:
    return SESSIONS / session_id


def ensure_session_dirs(session_id: str) -> Path:
    sd = session_dir(session_id)
    for sub in (
        "inbox",
        "status",
        "hitl/pending",
        "hitl/answered",
        "memory/agent",
        "memory/project",
        "results",
        "scratch",
    ):
        (sd / sub).mkdir(parents=True, exist_ok=True)
    return sd
