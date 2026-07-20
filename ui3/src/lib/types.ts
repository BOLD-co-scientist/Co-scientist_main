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

// ---- Hypothesis explorer (client-side model for the Evolution view) ----
export type HypStatus =
  | "root"
  | "path"
  | "supported"
  | "refuted"
  | "parked"
  | "proposed";

export type HypDir =
  | "triage"
  | "offtarget"
  | "redesign"
  | "profile"
  | "synergy";

export interface Hyp {
  id: string;
  parent: string | null;
  dir: HypDir | null;
  status: HypStatus;
  title: string;
  detail: string;
  evidence?: string;
}

export interface EvoLogEntry {
  kind: string;
  time: string;
  body: string;
  tone: "evo" | "grn" | "mid";
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
