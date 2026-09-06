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

// Brief v2 (O2): the five questions we ask every researcher. Q0 definition
// (title, question, objectives, domain) · Q1 significance · Q2 prior_work +
// open_gap · Q3 evaluation_protocol (+ success_criteria) · Q4 data +
// task_definition + existing_results + data_access (+ data_notes).
// `background` is the O1 name for prior_work (server reads it as an alias).
export interface BriefFields {
  title: string;
  domain: string;
  research_question: string;
  objectives: string[];
  significance: string;
  prior_work: string;
  open_gap: string;
  evaluation_protocol: string;
  success_criteria: string;
  data: BriefDataItem[];
  task_definition: string;
  existing_results: string;
  data_access: string;
  data_notes: string;
  constraints: string;
  deliverables: string[];
  keywords: string[];
  visibility: "org" | "private";
  parent_node: string | null;
}

export type BriefTextField = Exclude<keyof BriefFields, "objectives" | "data" | "deliverables" | "keywords" | "visibility" | "parent_node">;

export interface QuestionCompleteness {
  q: number;
  label: string;
  filled: boolean;
  missing: string[];
}

/** The harness a session runs on, stamped at launch (O2). */
export interface BriefHarness {
  version_id: string | null;
  summary: string | null;
  sha: string | null;
  imported_from: { owner?: string; owner_name?: string; version_id?: string; summary?: string } | null;
  tools: string[];
  custom_tools: string[];
  roles: string[];
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
  node_id: string | null;
  harness: BriefHarness | null;
  tree_context: string | null;
  schema_version?: number;
}

export interface BriefSummary {
  id: string;
  title: string;
  research_question: string;
  domain?: string;
  keywords?: string[];
  status: "draft" | "launched";
  created?: string | null;
  updated?: string | null;
  session_id: string | null;
  hypothesis_session_id: string | null;
  hypothesis: string | null;
  data_count: number;
  visibility?: "org" | "private";
  node_id?: string | null;
  parent_node?: string | null;
  registrable?: boolean;
  completeness?: QuestionCompleteness[];
}

export interface BriefPreview {
  text: string; // the exact first-turn message the supervisor receives
  missing: string[]; // required fields still empty
  data_problems: string[]; // attachments that would fail at launch
  size_bytes: number; // the message travels as one argv element (128 KiB cap on Linux)
  max_bytes: number;
  too_large: boolean;
  missing_for_registration: string[]; // Q1–Q3 fields still empty (share/register)
  pending_imports: string[]; // import ids awaiting approval (launch warns)
  completeness: QuestionCompleteness[];
}

// ---- Project tree (O2) ----
export type NodeStatus = "registered" | "active" | "solved" | "abandoned" | "superseded";
export type EdgeType = "adjacent" | "subproblem" | "shares_dataset" | "shares_method" | "supersedes";

export interface ProjectOwner {
  user_id: string;
  display_name: string;
}

export interface ProjectEdge {
  id: string;
  src: string;
  dst: string;
  type: EdgeType;
  weight: number;
  source: "keyword" | "advisor" | "human" | "import";
  status: "proposed" | "confirmed" | "rejected" | "dropped";
  rationale: string;
  rec_id?: string | null;
  ts?: string;
}

export interface ProjectNodeSummary {
  id: string;
  title: string;
  research_question: string;
  domain: string;
  keywords: string[];
  owner: ProjectOwner;
  visibility: "org" | "private";
  status: NodeStatus;
  parent_node: string | null;
  created?: string;
  updated?: string;
  session_count: number;
  outcome: string;
  active_version: { id: string; summary?: string | null; imported_from?: { owner?: string; owner_name?: string; version_id?: string } | null } | null;
  version_count: number;
  custom_tools: string[];
  dataset_count: number;
}

export interface ProjectStatement {
  title: string;
  domain: string;
  research_question: string;
  objectives: string[];
  significance: string;
  prior_work: string;
  open_gap: string;
  evaluation_protocol: string;
  success_criteria: string;
  task_definition: string;
  existing_results: string;
  data_access: string;
  data_notes: string;
  keywords: string[];
  data: { path: string; description: string }[];
}

export interface ProjectOutcome {
  session_id: string;
  summary: string;
  results: string[];
  version: string | null;
  ts: string;
}

export interface ProjectNodeView extends ProjectNodeSummary {
  statement: ProjectStatement;
  statement_sha256?: string;
  revisions: number;
  links: {
    sessions: string[];
    harness_versions: { id: string; summary?: string; sha?: string; active?: boolean; imported_from?: unknown }[];
    tools: string[];
    custom_tools: string[];
    roles: string[];
    datasets: { path: string; size?: number; kind?: string }[];
    outcomes: ProjectOutcome[];
  };
  edges: ProjectEdge[];
  neighbours: { node: ProjectNodeSummary; edge: ProjectEdge }[];
  is_owner: boolean;
  source?: { user_id: string; brief_id: string };
  recommendation?: { rec_id: string; status: string; served_by?: string; created?: string; harness_import: boolean; tool_imports: number; related: number } | null;
}

export interface ProjectTree {
  nodes: ProjectNodeSummary[];
  edges: ProjectEdge[];
  me: string;
}

export interface ProjectNear {
  self: ProjectNodeSummary;
  neighbours: { node: ProjectNodeSummary; edge: ProjectEdge }[];
  edges: ProjectEdge[];
}

// ---- Advisor (O2) ----
export interface AdvisorRelated {
  node_id: string;
  relation: EdgeType;
  confidence: number;
  rationale: string;
}
export interface AdvisorHarness {
  recommended: boolean;
  owner: string;
  owner_name?: string | null;
  version_id: string;
  sha?: string | null;
  from_node?: string | null;
  from_node_title?: string | null;
  summary?: string | null;
  rationale: string;
  confidence: number;
  mode_hint: "adopt" | "merge";
  risks: string[];
  alternatives: { owner: string; version_id: string; why: string }[];
  custom_tools?: string[];
}
export interface AdvisorTool {
  name: string;
  owner: string;
  owner_name?: string | null;
  version_id: string;
  description: string;
  from_node?: string | null;
  rationale: string;
  confidence: number;
  role_wiring: string[];
}
export interface AdvisorRecord {
  rec_id: string;
  node_id: string;
  status: "ready" | "failed" | "skipped";
  trigger?: string;
  created: string;
  served_by?: string | null;
  usage?: { input_tokens: number; output_tokens: number };
  validation_notes?: string[];
  error?: string;
  reason?: string;
  summary?: string;
  related_problems?: AdvisorRelated[];
  harness_import?: AdvisorHarness | null;
  tool_imports?: AdvisorTool[];
  statement_feedback?: { missing: { q: number; issue: string }[]; notes: string[]; suggested_keywords: string[] };
  hypothesis_seed?: { suggested: boolean; why: string };
  start_from_scratch_rationale?: string;
}
export interface RecommendationsView {
  running: boolean;
  latest: AdvisorRecord | null;
  pending_imports: ImportRecord[];
  imports: ImportRecord[];
}

// ---- Imports (O2) ----
export type ImportStatus = "pending" | "applied" | "rejected" | "failed" | "conflict" | "fallback" | "evolving";
export interface ImportRecord {
  id: string;
  kind: "harness" | "tool";
  mode?: "adopt" | "merge";
  user_id: string;
  owner: string;
  owner_name?: string;
  version_id: string;
  sha: string;
  summary?: string;
  rationale?: string;
  tools?: string[];
  roles?: string[];
  node_id?: string | null;
  from_node?: string | null;
  from_node_title?: string | null;
  session_id: string;
  request_id: string | null;
  status: ImportStatus;
  risks?: string[];
  donor_skills?: string[];
  include_skills?: string[];
  smoke?: { ran: boolean; ok: boolean | null; log?: string; tests?: string[] };
  diffstat?: string;
  diff_preview?: string;
  rollback_to?: { sha: string; version_id: string | null };
  result?: { version_id: string; sha?: string; tools?: string[]; skills_copied?: string[] };
  error?: string;
  evolution_command?: string;
  fallback_session?: string;
  merge_report?: { tools_added: string[]; roles_added: string[]; roles_wired: Record<string, string[]>; patched: boolean };
  created: string;
  updated?: string;
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
  // O2: provenance of an imported version (adopt: a second root; merge/tool: a child).
  imported_from?: { owner?: string; owner_name?: string; version_id?: string; from_node_title?: string | null; tools?: string[] } | null;
  import_id?: string | null;
  rollback_to?: { sha: string; version_id: string | null } | null;
  shared?: boolean;
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
