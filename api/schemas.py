from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StartResearchRequest(BaseModel):
    task: str
    session_id: str | None = None
    # Autonomous (no-human) mode: HITL gates auto-answer so the run proceeds
    # without a human. For HITL-vs-autonomous benchmarking; not exposed in the UI.
    autonomous: bool = False


class StartResearchResponse(BaseModel):
    session_id: str
    task: str


class AuthMe(BaseModel):
    user_id: str
    display_name: str
    root: str


class AgentKeyStatus(BaseModel):
    has_custom_key: bool
    default_available: bool


class AgentKeyUpdate(BaseModel):
    api_key: str


class SessionSummary(BaseModel):
    session_id: str
    task: str | None = None
    mtime: float
    last_ts: str | None = None
    last_kind: str | None = None
    running: bool = False


class HumanDirective(BaseModel):
    text: str


class StartEvolutionRequest(BaseModel):
    command: str
    session_id: str | None = None


class StartEvolutionResponse(BaseModel):
    session_id: str
    command: str


# ---- Hypothesis engine (Google AI co-scientist protocol) ----
# CONTRACT STUBS ONLY — no /hypothesis/* endpoints are wired yet. These pin the
# request/response shapes the UI and a future hypothesis.runtime will share, the
# same way StartEvolutionRequest exists ahead of POST /evolution/commands.
# Full design + steps: docs/plans/H1-hypothesis-coscientist.md.


class HypothesisConfig(BaseModel):
    """Knobs for a hypothesis session (all optional; sane server defaults)."""

    n_initial: int | None = None  # candidates in the first generation round
    max_rounds: int | None = None  # generation/tournament rounds before stopping


class StartHypothesisRequest(BaseModel):
    goal: str
    session_id: str | None = None
    config: HypothesisConfig | None = None


class StartHypothesisResponse(BaseModel):
    session_id: str
    goal: str


class HypothesisReview(BaseModel):
    reviewer: str
    verdict: str  # e.g. "supported" | "weak" | "refuted"
    novelty: float | None = None
    testability: float | None = None
    correctness: float | None = None
    note: str | None = None


class HypothesisView(BaseModel):
    id: str
    statement: str
    rationale: str = ""
    # proposed | reviewed | top | evolved | parked | selected
    state: str = "proposed"
    elo: float = 1200.0  # tournament rating
    round: int = 0
    parent_ids: list[str] = []  # lineage for evolved hypotheses
    reviews: list[HypothesisReview] = []


class SelectHypothesisRequest(BaseModel):
    hypothesis_id: str
    note: str | None = None


class HitlAnswer(BaseModel):
    decision: str  # "approve" | "reject"
    note: str | None = None


class RoleSummary(BaseModel):
    name: str
    description: str
    tools: list[str]
    can_spawn: bool
    model: str


class MemorySearchResult(BaseModel):
    layer: str
    hits: list[dict[str, Any]]


class LibraryFile(BaseModel):
    name: str
    size: int
    mtime: float


class LibraryUploadResponse(BaseModel):
    name: str
    size: int
    overwrote: bool


class LibraryHealth(BaseModel):
    file_count: int
    total_bytes: int
    disk_free_bytes: int
    staging_files: int
    broken_symlinks: list[dict[str, str]]


class SessionFile(BaseModel):
    path: str  # relative to the session dir, e.g. "results/summary.md"
    size: int
    mtime: float


class GitCommit(BaseModel):
    sha: str
    parents: list[str]
    author: str
    ts: float
    refs: list[str]
    subject: str


class GitHistory(BaseModel):
    commits: list[GitCommit]
    head: str | None = None
