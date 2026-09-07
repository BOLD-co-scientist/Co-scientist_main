// Domain types. Server payloads mirror brief §§1–4; event records are
// intentionally open (`[k: string]: unknown`) since each `kind` carries its own
// fields. Timestamps are server-local strings "YYYY-MM-DD HH:MM:SS" (§7.7).

export interface AuthMe {
  user_id: string;
  display_name: string;
  root: string;
}

export interface AgentKeyStatus {
  has_custom_key: boolean;
  default_available: boolean;
}

export type SessionStatus =
  | "running"
  | "blocked"
  | "idle"
  | "complete"
  | "crashed";

export interface SessionSummary {
  session_id: string;
  task: string;
  running: boolean;
  /** R17: created by a newer schema than the active version can read → opens read-only. */
  readonly?: boolean;
  /** Present when the agent is waiting on a HITL decision. */
  blocked?: boolean;
  status?: SessionStatus;
  event_count?: number;
  last_kind?: string;
  created?: string;
  updated?: string;
  /** O1: the session was launched from a problem brief (server-authoritative). */
  has_brief?: boolean;
  brief_title?: string | null;
  /** Working hypothesis statement chosen during onboarding, if any. */
  hypothesis?: string | null;
}

// ---- Onboarding phase (O1): fixed-format problem brief ----
// Mirrors api/schemas.py ProblemBrief / BriefRecord. The shape is fixed: every
// field is always present (empty when unfilled); title + research_question are
// required to launch. Rendering + the opening message are server-side.
export interface BriefDataItem {
  path: string; // library-relative POSIX path
  description: string;
  size?: number | null;
  is_dir?: boolean;
}

export interface BriefFields {
  title: string;
  domain: string;
  background: string;
  research_question: string;
  objectives: string[];
  data: BriefDataItem[];
  data_notes: string;
  constraints: string;
  success_criteria: string;
  deliverables: string[];
}

export interface BriefHypothesis {
  id?: string | null;
  statement: string;
  rationale: string;
  note?: string | null;
}

export interface BriefRecord extends BriefFields {
  id: string;
  status: "draft" | "launched";
  created: string;
  updated: string;
  hypothesis_session_id: string | null;
  hypothesis: BriefHypothesis | null;
  session_id: string | null;
}

export interface BriefSummary {
  id: string;
  title: string;
  research_question: string;
  status: "draft" | "launched";
  created?: string | null;
  updated?: string | null;
  session_id: string | null;
  hypothesis_session_id: string | null;
  hypothesis: string | null;
  data_count: number;
}

export interface BriefPreview {
  text: string; // the exact first-turn message the supervisor receives
  missing: string[]; // required fields still empty
  data_problems: string[]; // attachments that would fail at launch
  size_bytes: number; // the message travels as one argv element (128 KiB cap on Linux)
  max_bytes: number;
  too_large: boolean;
}

/** GET /sessions/{sid}/brief — the frozen brief plus its rendered text. */
export interface SessionBrief extends BriefRecord {
  text: string;
}

export interface Ev {
  id: string;
  ts: string;
  actor: string;
  kind: string;
  [k: string]: unknown;
}

export interface SessionFile {
  path: string;
  size: number;
  modified?: string;
}

export interface LibraryFile {
  name: string;
  size: number;
  modified?: string;
}

export interface LibraryUploadResponse {
  ok: boolean;
  name: string;
  size: number;
}

export interface LibraryHealth {
  used_bytes: number;
  free_bytes: number;
  max_bytes?: number;
}

export interface HitlPending {
  request_id: string;
  session_id?: string;
  requester?: string;
  title: string;
  action?: string;
  detail?: string;
  // When set, `detail` is markdown (e.g. a checkpoint's agent summary) and
  // should be rendered as such rather than as a raw mono/JSON blob.
  detailMarkdown?: boolean;
  // Optional one-line context shown under the action (e.g. "20 events").
  meta?: string;
  created?: string;
  /** R19: the independent judge's gate-2 review of this request, when it has run. */
  judge?: JudgeVerdict;
}

export type HitlDecision = "approve" | "reject";

export interface RoleSummary {
  name: string;
  description: string;
  model: string;
  tools: string[];
  can_spawn: boolean;
}

export interface MemoryHit {
  layer: string;
  score: number;
  text: string;
}

export interface MemorySearchResult {
  layer: string;
  query?: string;
  hits: MemoryHit[];
}

export interface GitRef {
  name: string;
  kind: "head" | "main" | "evo" | "tag" | "branch";
}

export interface GitCommit {
  sha: string;
  parents: string[];
  author: string;
  ts: string;
  refs: GitRef[];
  subject: string;
}

export interface GitHistory {
  head: string;
  commits: GitCommit[];
}

// R17: a switchable version node (a merged evolution, tagged ver/<id>).
export interface Version {
  id: string;
  tag: string;
  sha: string;
  base_sha?: string;
  summary?: string;
  rationale?: string;
  owner?: string;
  status?: string; // merged | rejected | superseded | ...
  origin_session?: string | null; // R17: the evolution session that produced this version (its birth story)
  smoke?: { ran: boolean; ok?: boolean };
  created_at?: number;
  archive_dir?: string;
  active?: boolean;
}
export interface VersionList {
  versions: Version[];
  active: string | null; // id of the active version, or null (detached / root)
  head?: string;
}

// What's *in* a commit — for the clickable evolution-graph node detail.
export interface CommitFile {
  path: string;
  additions: number;
  deletions: number;
  binary?: boolean;
}
export interface CommitDetail {
  sha: string;
  subject: string;
  author: string;
  ts: number;
  parents: string[];
  files: CommitFile[];
}

/** Result of POST /messages — carries status so the UI can handle 409 (§7.6). */
export interface SendResult {
  ok: boolean;
  status: number;
  resumed?: boolean;
  error?: string;
}

// ---- Hypothesis engine (server-authoritative; parallel-set flow) ----
// One card in a parallel set. The backend generates these from a goal (round 0)
// or from a chosen card + feedback (round N). See api/hypothesis.py.
export interface HypCard {
  id: string;
  statement: string;
  rationale: string;
  round: number;
  parent_id?: string | null;
}

export interface HypRound {
  round: number;
  parent_id: string | null;
  feedback: string | null;
  served_by: string; // which model produced this set (fable, or opus fallback)
  hypotheses: HypCard[];
}

export interface HypSession {
  id: string;
  goal: string;
  created: string;
  selected_id: string | null;
  select_note?: string | null;
  rounds: HypRound[];
}

export interface HypSummary {
  id: string;
  goal: string;
  created: string;
  rounds: number;
  latest_count: number;
  selected_id: string | null;
}

export interface EvoLogEntry {
  kind: string;
  time: string;
  body: string;
  tone: "evo" | "grn" | "mid";
}

// ---- R16: evolution reflection & suggestions (per research session) ----
export interface EvoProposal {
  title: string; // may carry a trailing "(recommended)" the model added, sparingly
  direction: string; // agents | workflow | tools | methodology | memory
  rationale: string; // grounded in the session; notes recurrence in prose
  command: string; // ready-to-run instruction for the evolution agent
}

export interface Reflection {
  session_id: string; // the research session this reflection is about
  reflection: string;
  proposals: EvoProposal[];
}

// ---- Evolution capability graph (self-modified skills) ----
export type SkillStatus = "core" | "merged" | "inflight" | "proposed";
export type SkillDir = "docking" | "selectivity" | "ingestion" | "memory";

export interface Skill {
  id: string;
  parent: string | null;
  dir: SkillDir | null;
  status: SkillStatus;
  title: string;
  adds: string;
  desc: string;
  sha?: string;
}

// ---- R19: guided evolution search (goal ledger, planner proposals, judge, modes) ----
// Mirrors api/evo_store.py / api/goals.py / api/judge.py records.
export type EvoMode = "manual" | "automatic";

export interface JudgeVerdict {
  stage: string; // "proposal" (gate 1) | "merge" (gate 2)
  verdict: "approve" | "decline" | "unavailable";
  score: number;
  why: string;
  risks: string[];
  recommendation: string;
  served_by: string;
  error?: string | null;
  at?: string;
  mode?: string;
  request_id?: string;
}

export type SubgoalStatus = "pending" | "current" | "done" | "skipped";
export interface EvoSubgoal {
  id: string;
  order: number;
  text: string;
  acceptance: string[];
  capabilities_needed: string[];
  status: SubgoalStatus;
}
export interface EvoWishlistItem {
  name: string;
  why: string;
  candidates: string[];
  status: string; // missing | present:<tool> | proposed:<pid> | version:<id>
}
export interface EvoDrift {
  id: string;
  question: string;
  inferred_current: string | null;
  declared_current: string | null;
  why: string;
  asked_at: string;
  answered: { changed: boolean; note: string; at: string } | null;
}
export interface GoalLedger {
  brief_id: string;
  title: string;
  research_question: string;
  approach_hints: string;
  subgoals: EvoSubgoal[];
  capability_wishlist: EvoWishlistItem[];
  current: string | null;
  inferred_current: string | null;
  phase: { inferred_current?: string | null; confidence?: number; evidence?: string; plan_changed?: boolean; why?: string; at?: string } | null;
  drift: EvoDrift | null;
  drift_history?: unknown[];
  researcher_ordered: boolean;
  derived: { served_by?: string | null; error?: string | null; at?: string } | null;
  updated?: string;
}
export interface GoalsPatch {
  approach_hints?: string;
  subgoals?: { id?: string; text: string; acceptance?: string[]; capabilities_needed?: string[]; status?: SubgoalStatus }[];
  order?: string[];
  current?: string;
}

export type ProposalStatus = "proposed" | "declined" | "queued" | "implementing" | "merged" | "rejected" | "ended";
export interface EvoProposalNode {
  id: string;
  brief_id: string;
  run_id: string;
  parent_version: string | null;
  title: string;
  scope: "harness" | "platform";
  direction: string; // agents | workflow | tools | methodology | memory | ui | import
  goal_ids: string[];
  rationale: string;
  command: string;
  expected_gain: string;
  cost: string;
  why_now: string;
  provenance?: string[];
  status: ProposalStatus;
  judge: JudgeVerdict | null;
  human: { decision: string; note: string; at: string; edited?: boolean } | null;
  session_id: string | null;
  version_id: string | null;
  created: string;
  updated?: string;
  error?: string;
  launched_by?: string;
}
export interface ProposalList {
  proposals: EvoProposalNode[];
  latest_run: { id: string; created: string; trigger: string; served_by?: string | null; error?: string | null; phase?: unknown; drift_opened?: boolean } | null;
  running: boolean;
  mode: EvoMode;
  evolution_running: boolean;
  platform_evolution?: boolean;
}
export interface EvoModeInfo {
  mode: EvoMode;
  updated?: string | null;
  judge: { available: boolean; model: string };
  /** False when platform-scope (ui3/api) evolutions are disabled on this deployment. */
  platform_evolution?: boolean;
}
export interface CalibrationGate {
  pairs: number;
  agreement: number | null;
  judge_accept_rate: number | null;
  human_accept_rate: number | null;
  disagreements: { proposal_id: string; judge: string; human: string }[];
}
export interface JudgeInfo {
  available: boolean;
  model: string;
  calibration: { n_proposals: number; gate1: CalibrationGate; gate2: CalibrationGate };
}
export interface DecideResult {
  ok: boolean;
  status: string;
  launched: { proposal_id: string; session_id: string }[];
  queued: string[];
}
