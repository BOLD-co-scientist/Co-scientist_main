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
    # O1 onboarding: set when the session was launched from a problem brief
    # (state/sessions/<sid>/brief.json). `hypothesis` is the working hypothesis
    # statement the human selected during onboarding, if any.
    has_brief: bool = False
    brief_title: str | None = None
    hypothesis: str | None = None
    # R17: True when the session was stamped by a NEWER schema than the active
    # version can read — it opens read-only and must be forked to continue.
    readonly: bool = False


class HumanDirective(BaseModel):
    text: str


class StartEvolutionRequest(BaseModel):
    command: str
    session_id: str | None = None
    # Commit sha the human picked in the evolution graph to branch from; HEAD if omitted.
    base: str | None = None


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


class RefineHypothesisRequest(BaseModel):
    # Generate a fresh parallel set derived from the chosen hypothesis, optionally
    # steered by free-form feedback ("what to do differently").
    parent_id: str
    feedback: str | None = None
    n: int | None = None


# ---- Onboarding phase (O1): fixed-format problem brief ----
# The human defines the problem, attaches data, and runs the hypothesis search
# BEFORE the research session exists. Launch freezes the brief into the session.
# Rendering + validation: api/onboarding.py. Plan: docs/plans/O1-onboarding.md.


class BriefDataItem(BaseModel):
    path: str  # library-relative POSIX path (e.g. "panel/ic50.csv")
    description: str = ""
    # Stamped by the server when the path is resolved (preview / launch);
    # ignored on input.
    size: int | None = None
    is_dir: bool = False
    file_count: int | None = None
    files: list[str] | None = None  # bounded inner listing for folders


class ProblemBrief(BaseModel):
    """The fixed format. Every field is optional while drafting; `title` and
    `research_question` are required to launch."""

    title: str = ""
    domain: str = ""
    background: str = ""
    research_question: str = ""
    objectives: list[str] = []
    data: list[BriefDataItem] = []
    data_notes: str = ""
    constraints: str = ""
    success_criteria: str = ""
    deliverables: list[str] = []


class BriefPatch(BaseModel):
    """Partial update: only the fields present are changed."""

    title: str | None = None
    domain: str | None = None
    background: str | None = None
    research_question: str | None = None
    objectives: list[str] | None = None
    data: list[BriefDataItem] | None = None
    data_notes: str | None = None
    constraints: str | None = None
    success_criteria: str | None = None
    deliverables: list[str] | None = None


class BriefHypothesis(BaseModel):
    id: str | None = None
    statement: str
    rationale: str = ""
    note: str | None = None


class BriefRecord(ProblemBrief):
    id: str
    status: str  # "draft" | "launched"
    created: str
    updated: str
    hypothesis_session_id: str | None = None
    hypothesis: BriefHypothesis | None = None
    session_id: str | None = None


class BriefSummary(BaseModel):
    id: str
    title: str
    research_question: str = ""
    status: str
    created: str | None = None
    updated: str | None = None
    session_id: str | None = None
    hypothesis_session_id: str | None = None
    hypothesis: str | None = None
    data_count: int = 0


class BriefHypothesesRequest(BaseModel):
    n: int | None = None


class LaunchBriefRequest(BaseModel):
    autonomous: bool = False


class LaunchBriefResponse(BaseModel):
    session_id: str
    task: str
    brief_id: str


class BriefPreview(BaseModel):
    text: str  # the exact first-turn message the supervisor will receive
    missing: list[str]  # required fields still empty
    data_problems: list[str] = []  # attached paths that would fail at launch
    size_bytes: int = 0  # UTF-8 size of `text` (it travels as one argv element)
    max_bytes: int = 0
    too_large: bool = False


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
