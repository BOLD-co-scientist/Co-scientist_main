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
  /** Present when the agent is waiting on a HITL decision. */
  blocked?: boolean;
  status?: SessionStatus;
  event_count?: number;
  last_kind?: string;
  created?: string;
  updated?: string;
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
