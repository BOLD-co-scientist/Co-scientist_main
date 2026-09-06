import type { Api, StreamHandle } from "./api";
import type {
  AdvisorRecord,
  BriefRecord,
  Ev,
  GitHistory,
  HitlPending,
  EvoLogEntry,
  HypSession,
  ImportRecord,
  LibraryFile,
  ProjectEdge,
  ProjectNodeSummary,
  ProjectNodeView,
  RoleSummary,
  SessionSummary,
  Skill,
  SkillDir,
} from "./types";

// ---- onboarding (O1) mock helpers: the fixed brief shape + a rendering that
// mirrors api/onboarding.py closely enough for design review ----
export function emptyBrief(): Omit<BriefRecord, "id" | "status" | "created" | "updated" | "hypothesis_session_id" | "hypothesis" | "session_id"> {
  return {
    title: "",
    domain: "",
    research_question: "",
    objectives: [],
    significance: "",
    prior_work: "",
    open_gap: "",
    evaluation_protocol: "",
    success_criteria: "",
    data: [],
    task_definition: "",
    existing_results: "",
    data_access: "",
    data_notes: "",
    constraints: "",
    deliverables: [],
    keywords: [],
    visibility: "org",
    parent_node: null,
    node_id: null,
    harness: null,
    tree_context: null,
  };
}

const REGISTRATION_FIELDS = ["title", "research_question", "significance", "prior_work", "open_gap", "evaluation_protocol"] as const;
export function mockCompleteness(b: BriefRecord) {
  const has = (k: keyof BriefRecord) => {
    const v = b[k];
    return Array.isArray(v) ? v.length > 0 : typeof v === "string" && v.trim().length > 0;
  };
  return [
    { q: 0, label: "Definition of the problem", filled: has("title") && has("research_question"), missing: (["title", "research_question"] as const).filter((k) => !has(k)) as string[] },
    { q: 1, label: "Why the problem is scientifically important", filled: has("significance"), missing: has("significance") ? [] : ["significance"] },
    { q: 2, label: "What existing work has achieved, and what remains genuinely open", filled: has("prior_work") && has("open_gap"), missing: (["prior_work", "open_gap"] as const).filter((k) => !has(k)) as string[] },
    { q: 3, label: "How progress is evaluated objectively — no hackable proxy, shortcut or subjective judgement", filled: has("evaluation_protocol"), missing: has("evaluation_protocol") ? [] : ["evaluation_protocol"] },
    { q: 4, label: "The exact dataset, metadata, task definition, evaluation protocol, existing results and permissions", filled: has("task_definition") && has("existing_results") && has("data_access"), missing: (["task_definition", "existing_results", "data_access"] as const).filter((k) => !has(k)) as string[] },
  ];
}

function renderMockBrief(b: BriefRecord): string {
  const num = (xs: string[], empty: string) => (xs.length ? xs.map((x, i) => `${i + 1}. ${x}`).join("\n") : `_${empty}_`);
  const para = (s: string, empty: string) => (s.trim() ? s.trim() : `_${empty}_`);
  const data = b.data.length
    ? b.data.map((d) => `- \`state/library/${d.path}\`${d.description ? ` — ${d.description}` : ""}`).join("\n")
    : "_No data files were attached to this brief._";
  const hyp = b.hypothesis
    ? `**Statement:** ${b.hypothesis.statement}${b.hypothesis.rationale ? `\n**Rationale:** ${b.hypothesis.rationale}` : ""}`
    : "_No working hypothesis was selected during onboarding._";
  return [
    `# Problem brief: ${b.title || "(untitled)"}`,
    b.domain ? `**Domain:** ${b.domain}` : "",
    b.keywords.length ? `**Keywords:** ${b.keywords.join(", ")}` : "",
    "",
    "## Why this matters",
    para(b.significance, "Not stated."),
    "",
    "## Prior work and what remains open",
    "**What existing work has achieved:** " + para(b.prior_work, "Not stated."),
    "**What remains genuinely open:** " + para(b.open_gap, "Not stated."),
    "",
    "## Research question",
    para(b.research_question, "No research question given."),
    "",
    "## Objectives",
    num(b.objectives, "No explicit objectives — derive them from the research question."),
    "",
    "## Evaluation protocol",
    para(b.evaluation_protocol, "Not stated."),
    "",
    "## Data",
    data,
    "**Task definition:** " + para(b.task_definition, "Not stated."),
    "**Existing results / baselines:** " + para(b.existing_results, "None recorded."),
    "**Data access and permissions:** " + para(b.data_access, "None stated."),
    b.data_notes ? `**Data notes:** ${b.data_notes}` : "",
    "",
    "## Constraints",
    para(b.constraints, "None stated."),
    "",
    "## Success criteria",
    para(b.success_criteria, "None stated."),
    "",
    "## Deliverables",
    num(b.deliverables, "None stated — default to a written report under results/."),
    "",
    "## Project tree context",
    b.node_id ? `**Registered as project node** \`${b.node_id}\`.` : "_Not registered on the project tree._",
    "",
    "## Harness",
    "**Active version:** bootstrap harness (mock)",
    "",
    "## Working hypothesis",
    hyp,
  ]
    .filter((l) => l !== "")
    .join("\n");
}

// ============================================================
// Seed data for the Evolution view (a designed-but-stubbed surface until
// POST /evolution/commands lands). Exported so the view seeds identically in
// both mock and live modes — the hypothesis tree is a client-side artifact.
// ============================================================

// Evolution capability lineage — skills the system has taught itself.
export const EVODIR: Record<SkillDir, { color: string; label: string }> = {
  docking: { color: "var(--cyan)", label: "DOCKING" },
  selectivity: { color: "var(--grn)", label: "SELECTIVITY" },
  ingestion: { color: "var(--accent)", label: "INGESTION" },
  memory: { color: "var(--lav)", label: "MEMORY" },
};

export const SKILLS: Skill[] = [
  { id: "core", parent: null, dir: null, status: "core", title: "Harness core", adds: "base tools", desc: "The base agent harness — the tools and prompts every session starts from before any self-modification." },
  { id: "dock0", parent: "core", dir: "docking", status: "merged", sha: "c22a5f1", title: "GPU docking backend", adds: "longjob: dock", desc: "AutoDock Vina on a managed A100 pool, exposed to the data_analyst as a longjob tool." },
  { id: "dock1", parent: "dock0", dir: "docking", status: "inflight", sha: "4d1e9c2", title: "Batch pose submission", adds: "evo/faster-docking", desc: "Submit all candidate poses in one GPU job instead of N sequential calls — ~6× fewer cold starts." },
  { id: "dock2", parent: "dock1", dir: "docking", status: "proposed", title: "Ensemble docking", adds: "—", desc: "Dock against multiple receptor conformations and average — better for flexible kinases." },
  { id: "sel0", parent: "core", dir: "selectivity", status: "merged", sha: "9f2c1a4", title: "S(10) selectivity metric", adds: "py: compute_selectivity", desc: "Fraction of kinases within 10× Kd of the most potent target. Added to the analysis toolchain." },
  { id: "sel1", parent: "sel0", dir: "selectivity", status: "inflight", title: "Off-target panel v2", adds: "data: panel_v2", desc: "Grow the reference panel from 468 to 520 kinases, including MAP4K4 and GAK." },
  { id: "sel2", parent: "sel0", dir: "selectivity", status: "proposed", title: "Gini + entropy scores", adds: "—", desc: "Complementary concentration metrics for panels with sparse Kd coverage." },
  { id: "ing0", parent: "core", dir: "ingestion", status: "inflight", title: "ChEMBL streaming loader", adds: "py: chembl_stream", desc: "Chunked, memory-safe ingestion of large assay tables — fixes the 1.4M-row OOM crash." },
  { id: "ing1", parent: "ing0", dir: "ingestion", status: "proposed", title: "Assay table chunking", adds: "—", desc: "Partition assay pulls by target family so subagents can parallelize." },
  { id: "mem0", parent: "core", dir: "memory", status: "merged", sha: "e90dd34", title: "Eventlog cursor resume", adds: "core: eventlog", desc: "Resume an SSE stream from the last seen event id after a disconnect." },
  { id: "mem1", parent: "mem0", dir: "memory", status: "proposed", title: "Semantic memory reindex", adds: "—", desc: "Periodic re-embedding of project memory so retrieval stays sharp as sessions grow." },
];

export const EVO_LOG: EvoLogEntry[] = [
  { kind: "evolution.start", time: "13:04", body: 'Started worktree evo/selectivity-scorer on request "add a proper selectivity metric".', tone: "evo" },
  { kind: "evolution.proposal", time: "13:07", body: "Proposes adding an S(10) selectivity metric helper to the data_analyst toolchain.", tone: "evo" },
  { kind: "evolution.tool", time: "13:08", body: "edit \u2192 tools/py_exec/server.py (+38 \u22124)", tone: "mid" },
  { kind: "evolution.note", time: "13:11", body: "Ran unit tests: 42 passed, 0 failed.", tone: "grn" },
  { kind: "evolution_live_check", time: "13:12", body: "Strict smoke v0 passed on the worktree.", tone: "grn" },
  { kind: "evolution.merged", time: "13:14", body: "Merged evo/selectivity-scorer \u2192 main. Restart sessions to pick up changes.", tone: "grn" },
];

const D = "2026-07-09 ";
const ev = (id: string, t: string, actor: string, kind: string, x: Record<string, unknown> = {}): Ev => ({ id, ts: D + t, actor, kind, ...x });

const SESSIONS: SessionSummary[] = [
  { session_id: "s_7f3a", task: "Characterize kinase inhibitor selectivity across the human kinome", running: true, blocked: true, last_kind: "hitl.pending" },
  { session_id: "s_9d4e", task: "Design primers for CRISPR knockout of TP53 exon 4", running: false, blocked: false, last_kind: "session.idle" },
  { session_id: "s_2c1b", task: "Meta-analysis of GLP-1 cardiovascular outcome trials", running: false, blocked: false, status: "complete", last_kind: "research.complete" },
  { session_id: "s_11a0", task: "Screen ChEMBL for BRD4 binders under 300 Da", running: false, blocked: false, status: "crashed", last_kind: "session.crashed" },
];

const EVENTS: Record<string, Ev[]> = {
  s_7f3a: [
    ev("9a1c04f2b7e0", "14:22:03", "system", "session.start", { model: "claude-opus-4-7" }),
    ev("9a1c04f2b7e1", "14:22:03", "human", "research.requested", { task: "Characterize kinase inhibitor selectivity across the human kinome" }),
    ev("9a1c04f2b7e2", "14:22:05", "supervisor", "turn.start"),
    ev("9a1c04f2b7e3", "14:22:12", "supervisor", "report", { text: "Plan: (1) pull the curated kinome family tree, (2) score selectivity for the candidate set against a 468-kinase off-target panel, (3) cross-reference known clinical off-targets. Dispatching a data analyst for the quantitative pass." }),
    ev("9a1c04f2b7e4", "14:22:20", "supervisor", "tool.use", { tool: "web_search", arg: 'query: "human kinome families curated panel"\ntop_k: 8' }),
    ev("9a1c04f2b7e5", "14:22:41", "supervisor", "bus.send", { target: "data_analyst", text: "Compute a selectivity score \u2014 S(10) and Gini \u2014 for each compound in kinome_panel.csv against the 468-kinase panel." }),
    ev("9a1c04f2b7e6", "14:23:02", "data_analyst", "tool.use", { tool: "py_exec", arg: 'import pandas as pd\ndf = pd.read_csv("library/kinome_panel.csv")\nscores = compute_selectivity(df, panel=468)' }),
    ev("9a1c04f2b7e7", "14:23:48", "data_analyst", "report", { text: "Scored 32 compounds. 4 are highly selective (S(10) < 0.05). One flag: compound CX-14 shows an unexpected off-target at MAP4K4 (Kd \u2248 38 nM) that isn't in the annotated panel." }),
    ev("9a1c04f2b7e8", "14:24:01", "system", "checkpoint.triggered", { summary: "quantitative pass \u00b7 41 events" }),
    ev("9a1c04f2b7e9", "14:24:12", "data_analyst", "hitl.pending", { req: "req_8843" }),
  ],
  s_9d4e: [
    ev("4b2d1", "11:02:10", "system", "session.start"),
    ev("4b2d2", "11:02:10", "human", "research.requested", { task: "Design primers for CRISPR knockout of TP53 exon 4" }),
    ev("4b2d3", "11:03:40", "supervisor", "report", { text: "Drafted 3 guide RNA candidates targeting exon 4 with off-target scores below 0.2. Ready for your review \u2014 reply to refine or approve." }),
    ev("4b2d4", "11:03:45", "system", "session.idle"),
  ],
  s_2c1b: [
    ev("7c3a1", "09:15:00", "system", "session.start"),
    ev("7c3a2", "09:15:00", "human", "research.requested", { task: "Meta-analysis of GLP-1 cardiovascular outcome trials" }),
    ev("7c3a3", "09:41:22", "supervisor", "report", { text: "Pooled 7 RCTs (n=56,004). MACE hazard ratio 0.86 [0.80\u20130.93], consistent across agents. Full write-up in results/glp1_meta.md." }),
    ev("7c3a4", "09:41:30", "supervisor", "research.complete", { summary: "GLP-1 RAs reduce MACE by ~14% across 7 trials; no significant heterogeneity." }),
    ev("7c3a5", "09:41:31", "system", "session.end"),
  ],
  s_11a0: [
    ev("2e5f1", "16:20:00", "system", "session.start"),
    ev("2e5f1b", "16:20:00", "human", "research.requested", { task: "Screen ChEMBL for BRD4 binders under 300 Da" }),
    ev("2e5f2", "16:21:14", "data_analyst", "tool.use", { tool: "py_exec", arg: 'chembl_client.query(target="BRD4", mw_max=300)' }),
    ev("2e5f3", "16:21:40", "system", "session.crashed", { error: "py_exec: MemoryError loading 1.4M-row assay table" }),
  ],
};

// Events that stream in after the pending docking approval is granted.
const POST_APPROVAL: Ev[] = [
  ev("9a1c04f2b7ea", "14:31:20", "data_analyst", "longjob.submitted", { title: "GPU docking submitted \u00b7 job dk_4471" }),
  ev("9a1c04f2b7eb", "14:38:05", "supervisor", "report", { text: "Docking confirms the MAP4K4 liability for CX-14 (pose \u0394G \u22129.4 kcal/mol). The other three selective compounds remain clean. Writing up the selectivity report." }),
  ev("9a1c04f2b7ec", "14:38:40", "supervisor", "research.complete", { summary: "3 of 32 compounds meet the selectivity bar; CX-14 flagged for a MAP4K4 off-target. Full report in results/selectivity_summary.md." }),
  ev("9a1c04f2b7ed", "14:38:41", "system", "session.idle"),
];

const PENDING: Record<string, HitlPending[]> = {
  s_7f3a: [
    {
      request_id: "req_8843",
      session_id: "s_7f3a",
      requester: "data_analyst",
      title: "Submit a GPU docking job",
      action: "Run kinome-wide docking for CX-14 against the 468-kinase panel to confirm the suspected MAP4K4 off-target.",
      detail: "AutoDock Vina \u00b7 1\u00d7 A100 GPU\nest. runtime ~40 min \u00b7 est. cost ~$12\nwrites \u2192 results/docking/",
    },
  ],
};

const FILES: Record<string, { path: string; size: number }[]> = {
  s_7f3a: [
    { path: "results/selectivity_summary.md", size: 18442 },
    { path: "results/kinome_heatmap.png", size: 486123 },
    { path: "scratch/raw_hits.csv", size: 91204 },
  ],
};

const ROLES: RoleSummary[] = [
  { name: "supervisor", description: "Lead research agent. Talks to the human, plans, dispatches subagents, synthesizes findings.", tools: ["Task", "bus", "memory", "hitl", "fs_read", "fs_write_workspace"], can_spawn: true, model: "claude-opus-4-7" },
  { name: "data_analyst", description: "Quantitative analysis on tabular data \u2014 CSV/spreadsheet computation, statistics, plots.", tools: ["bus", "memory", "fs_read", "fs_write_workspace", "py_exec", "longjob"], can_spawn: false, model: "claude-sonnet-4-6" },
  { name: "generalist_researcher", description: "General-purpose worker. Reads, summarizes, drafts, executes small Python snippets.", tools: ["bus", "memory", "fs_read", "fs_write_workspace", "py_exec"], can_spawn: false, model: "claude-sonnet-4-6" },
];

const GIT: GitHistory = {
  head: "9f2c1a4",
  commits: [
    { sha: "9f2c1a4", parents: ["4d1e9c2"], subject: "merge evo/selectivity-scorer: add S(10) metric", author: "evolution", ts: "13:14", refs: [{ name: "HEAD", kind: "head" }, { name: "main", kind: "main" }] },
    { sha: "4d1e9c2", parents: ["7b12f08"], subject: "wip: batch docking backend", author: "evolution", ts: "14:29", refs: [{ name: "evo/faster-docking", kind: "evo" }] },
    { sha: "7b12f08", parents: ["c22a5f1"], subject: "supervisor: tighten dispatch prompt", author: "alice", ts: "11:40", refs: [] },
    { sha: "c22a5f1", parents: ["e90dd34"], subject: "add longjob GPU backend", author: "alice", ts: "Jul 8", refs: [] },
    { sha: "e90dd34", parents: [], subject: "scaffold: eventlog cursor resume", author: "alice", ts: "Jul 8", refs: [] },
  ],
};

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const rid = () => Math.random().toString(16).slice(2, 14);

// Canned parallel hypotheses for VITE_MOCK demo mode.
const MOCK_HYPS = [
  { statement: "A dormant persister subpopulation survives antibiotics via metabolic shutdown, then repopulates.", rationale: "Persisters are phenotypically (not genetically) tolerant; test by time-kill curves on stationary-phase cells." },
  { statement: "Stochastic efflux-pump bursts in a fraction of cells transiently lower intracellular drug below lethal levels.", rationale: "Single-cell reporters would show heterogeneous pump expression correlating with survival." },
  { statement: "Tolerance is collective and density-dependent, mediated by secreted molecules that buffer the population.", rationale: "Conditioned-media transfer and density titration would reveal a quorum-like effect." },
  { statement: "Preexisting (p)ppGpp / stringent-response heterogeneity primes a subset for tolerance before exposure.", rationale: "relA/spoT mutants and (p)ppGpp reporters would predict which cells survive." },
  { statement: "Toxin–antitoxin modules enforce reversible dormancy that a targeted molecule could disrupt.", rationale: "TA knockouts should collapse the tolerant fraction." },
  { statement: "Membrane-potential dissipation lets an adjuvant re-sensitize tolerant cells to the primary drug.", rationale: "Combine a protonophore with the antibiotic and measure the tolerant fraction." },
];
const MOCK_HYP_STORE: Array<{
  id: string; goal: string; created: string; selected_id: string | null; select_note?: string | null;
  rounds: Array<{ round: number; parent_id: string | null; feedback: string | null; served_by: string; hypotheses: Array<{ id: string; statement: string; rationale: string; round: number; parent_id?: string | null }> }>;
}> = [];

export function createMockApi(): Api {
  const sessions = clone(SESSIONS);
  const events: Record<string, Ev[]> = clone(EVENTS);
  const pending: Record<string, HitlPending[]> = clone(PENDING);
  const library: LibraryFile[] = [
    { name: "kinome_panel.csv", size: 240128 },
    { name: "chembl_brd4.sdf", size: 1884160 },
    { name: "assay_protocol_v2.pdf", size: 512400 },
    { name: "prior_results.xlsx", size: 84992 },
  ];
  const queues: Record<string, Ev[]> = { s_7f3a: clone(POST_APPROVAL) };
  const streams = new Map<string, { onEvent: (e: Ev) => void; timer?: number }>();

  let clock = 14 * 3600 + 40 * 60;
  const nextTs = () => {
    clock += 7 + Math.floor(Math.random() * 7);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${D}${p(Math.floor(clock / 3600))}:${p(Math.floor((clock % 3600) / 60))}:${p(clock % 60)}`;
  };
  const find = (sid: string) => sessions.find((s) => s.session_id === sid)!;
  const push = (sid: string, e: Ev) => {
    (events[sid] ||= []).push(e);
    streams.get(sid)?.onEvent(e);
  };
  const delay = <T,>(v: T, ms = 260): Promise<T> => new Promise((r) => setTimeout(() => r(v), ms));

  // ---- onboarding (O1) state ----
  const briefs: BriefRecord[] = [];
  const briefLinks = new Map<string, string>(); // hypothesis session id → brief id
  const sessionBriefs = new Map<string, BriefRecord>();

  // ---- project tree (O2) mock: registry filled by registering briefs ----
  const nodes: ProjectNodeView[] = [];
  const edges: ProjectEdge[] = [];
  const recs = new Map<string, AdvisorRecord>();
  const running = new Set<string>();
  const importsStore: ImportRecord[] = [];
  const me = "u_alice";
  const summaryOf = (n: ProjectNodeView): ProjectNodeSummary => {
    const { statement: _s, links: _l, edges: _e, neighbours: _n, is_owner: _o, recommendation: _r, source: _src, revisions: _rev, ...rest } = n;
    return rest;
  };
  const nodeView = (n: ProjectNodeView): ProjectNodeView => ({
    ...clone(n),
    edges: edges.filter((e) => (e.src === n.id || e.dst === n.id) && e.status !== "rejected"),
    neighbours: edges
      .filter((e) => (e.src === n.id || e.dst === n.id) && e.status !== "rejected")
      .map((e) => ({ node: summaryOf(nodes.find((x) => x.id === (e.src === n.id ? e.dst : e.src))!), edge: e })),
    recommendation: recs.get(n.id) ? { rec_id: recs.get(n.id)!.rec_id, status: recs.get(n.id)!.status, served_by: recs.get(n.id)!.served_by ?? undefined, harness_import: !!recs.get(n.id)!.harness_import, tool_imports: recs.get(n.id)!.tool_imports?.length ?? 0, related: recs.get(n.id)!.related_problems?.length ?? 0 } : null,
  });
  const cannedRec = (n: ProjectNodeView): AdvisorRecord => {
    const other = nodes.find((x) => x.id !== n.id);
    return {
      rec_id: "rec-" + rid().slice(0, 6),
      node_id: n.id,
      status: "ready",
      created: "2026-09-06 12:00:00",
      served_by: "claude-fable-5 (mock)",
      usage: { input_tokens: 4200, output_tokens: 610 },
      validation_notes: [],
      summary: other
        ? `This problem sits next to “${other.title}” (${other.owner.display_name}). Reuse its evaluation pipeline rather than rebuilding it.`
        : "No other problems are on the tree yet; nothing to reuse. Start from the base harness.",
      related_problems: other ? [{ node_id: other.id, relation: "adjacent", confidence: 0.72, rationale: "Same measurement modality and overlapping evaluation protocol." }] : [],
      harness_import: other
        ? { recommended: true, owner: other.owner.user_id, owner_name: other.owner.display_name, version_id: "20260901-101500__peak-fitting", summary: "Add peak-fitting tool + data_analyst wiring", rationale: "The donor version carries a peak-fitting tool that matches question 4's task definition.", confidence: 0.66, mode_hint: "adopt", risks: ["the donor version predates the R17 compat gate (no tests/test_contract_compat.py); only the v0 smoke can be run"], alternatives: [], custom_tools: ["peak_fit"], from_node: other.id, from_node_title: other.title }
        : null,
      tool_imports: other ? [{ name: "peak_fit", owner: other.owner.user_id, owner_name: other.owner.display_name, version_id: "20260901-101500__peak-fitting", description: "Fits Lorentzian peaks and reports linewidths with bootstrap CIs.", rationale: "Directly implements the fit named in the evaluation protocol.", confidence: 0.8, role_wiring: ["data_analyst"] }] : [],
      statement_feedback: { missing: mockCompleteness(briefs.find((b) => b.node_id === n.id) ?? ({ ...emptyBrief(), id: "", status: "draft", created: "", updated: "", hypothesis_session_id: null, hypothesis: null, session_id: null } as BriefRecord)).filter((q) => !q.filled).map((q) => ({ q: q.q, issue: `Not answered: ${q.missing.join(", ")}` })), notes: ["State the baseline per qubit, not per cooldown."], suggested_keywords: ["CPMG", "filter function"] },
      hypothesis_seed: { suggested: true, why: "The open gap is mechanistic; competing hypotheses are meaningful." },
      start_from_scratch_rationale: other ? "" : "The project tree is empty apart from this problem.",
    };
  };

  const mockStartHypothesis = (goal: string, n = 4, briefId?: string): Promise<HypSession> => {
    const hyps = MOCK_HYPS.slice(0, n).map((h, i) => ({ ...h, id: `h0_${i}`, round: 0 }));
    const rec = {
      id: `hyp-${MOCK_HYP_STORE.length + 1}`,
      goal,
      created: "2026-07-20 00:00:00",
      selected_id: null as string | null,
      rounds: [{ round: 0, parent_id: null, feedback: null, served_by: "claude-opus-4-8 (mock)", hypotheses: hyps }],
    };
    MOCK_HYP_STORE.push(rec);
    if (briefId) briefLinks.set(rec.id, briefId);
    return delay(clone(rec), 700);
  };

  return {
    authMe: () => delay({ user_id: "u_alice", display_name: "alice", root: "/data/alice" }),
    listSessions: () => delay(clone(sessions)),
    getEvents: (sid) => delay(clone(events[sid] ?? [])),

    openStream(sid, _since, onEvent): StreamHandle {
      const entry = { onEvent } as { onEvent: (e: Ev) => void; timer?: number };
      streams.set(sid, entry);
      const s = find(sid);
      if (s?.running && !s.blocked && queues[sid]?.length) {
        entry.timer = window.setInterval(() => {
          const q = queues[sid];
          if (!q || !q.length) {
            window.clearInterval(entry.timer);
            return;
          }
          const e = q.shift()!;
          push(sid, e);
          if (e.kind === "session.idle" || e.kind === "session.end") {
            s.running = false;
            s.last_kind = e.kind;
            window.clearInterval(entry.timer);
          }
        }, 2200);
      }
      return {
        close: () => {
          if (entry.timer) window.clearInterval(entry.timer);
          streams.delete(sid);
        },
      };
    },

    createSession: (task) => {
      const id = "s_" + rid().slice(0, 4);
      sessions.unshift({ session_id: id, task, running: false, blocked: false, last_kind: "session.idle" });
      events[id] = [ev(rid(), nextTs().slice(11), "system", "session.start")];
      return delay({ session_id: id, task });
    },

    forkSession: (sid) => {
      const id = "s_" + rid().slice(0, 4);
      const src = find(sid);
      sessions.unshift({ session_id: id, task: src?.task ?? "(fork)", running: false, blocked: false, last_kind: "session.forked" });
      events[id] = [...(events[sid] ?? [])];
      return delay({ session_id: id, parent: sid });
    },

    sendMessage: async (sid, text) => {
      const s = find(sid);
      if (!s) return { ok: false, status: 404, error: "Session not found." };
      if (s.running && !s.blocked) return { ok: false, status: 409, error: "Session busy." };
      push(sid, ev(rid(), nextTs().slice(11), "human", "message.received", { text }));
      if (!s.blocked) {
        s.running = true;
        s.last_kind = "turn.start";
        setTimeout(() => {
          push(sid, ev(rid(), nextTs().slice(11), "supervisor", "report", { text: "Understood \u2014 I\u2019ll fold that into the next pass and report back." }));
          push(sid, ev(rid(), nextTs().slice(11), "system", "session.idle"));
          s.running = false;
          s.last_kind = "session.idle";
        }, 1800);
      }
      return delay({ ok: true, status: 200, resumed: true });
    },

    interject: (sid, text) => {
      push(sid, ev(rid(), nextTs().slice(11), "human", "interjection", { text }));
      return delay({ ok: true, queued: true });
    },

    stop: (sid) => {
      const s = find(sid);
      if (s) {
        s.running = false;
        s.blocked = false;
        s.last_kind = "session.idle";
        push(sid, ev(rid(), nextTs().slice(11), "system", "session.interrupted"));
      }
      return delay({ ok: true });
    },

    listFiles: (sid) => delay(clone(FILES[sid] ?? [])),
    downloadFile: async () => {
      /* no-op in mock — real client streams an authenticated blob */
    },
    listLibrary: () => delay(clone(library)),
    uploadLibrary: (file, onProgress) => {
      onProgress?.(1);
      library.unshift({ name: file.name, size: file.size });
      return delay({ ok: true, name: file.name, size: file.size });
    },
    deleteLibrary: (name) => {
      const i = library.findIndex((f) => f.name === name);
      if (i >= 0) library.splice(i, 1);
      return delay({ ok: true });
    },
    libraryHealth: () => delay({ used_bytes: 2_878_000_000, free_bytes: 442_000_000_000, max_bytes: 445_000_000_000 }),

    getPending: (sid) => delay(clone(pending[sid] ?? [])),
    getReflection: (sid) => delay({ session_id: sid, reflection: "", proposals: [] }),
    reflect: () => delay({ ok: true }),
    answerHitl: (sid, _req, decision) => {
      const imp = importsStore.find((i) => i.session_id === sid && i.status === "pending");
      if (imp) {
        imp.status = decision === "approve" ? "applied" : "rejected";
        if (decision === "approve") {
          imp.result = { version_id: "20260906-120100__import-" + (imp.kind === "harness" ? "peak-fitting" : "tools"), sha: imp.sha, tools: imp.tools };
          const n = nodes.find((x) => x.id === imp.node_id);
          if (n) { n.active_version = { id: imp.result.version_id, summary: imp.summary, imported_from: { owner: imp.owner, owner_name: imp.owner_name, version_id: imp.version_id } }; n.custom_tools = ["peak_fit"]; n.links.custom_tools = ["peak_fit"]; }
        }
        pending[sid] = [];
        const s0 = find(sid);
        if (s0) { s0.blocked = false; s0.last_kind = decision === "approve" ? "import.applied" : "import.rejected"; }
        push(sid, ev(rid(), nextTs().slice(11), "human", "hitl.answer", { decision }));
        push(sid, ev(rid(), nextTs().slice(11), "system", decision === "approve" ? "import.applied" : "import.rejected", { import_id: imp.id, version: imp.result?.version_id }));
        return delay({ ok: true });
      }
      const s = find(sid);
      pending[sid] = [];
      if (s) {
        s.blocked = false;
        push(sid, ev(rid(), nextTs().slice(11), "human", "hitl.answer", { text: decision }));
        if (decision === "reject") {
          s.running = false;
          s.last_kind = "session.idle";
          queues[sid] = [];
          push(sid, ev(rid(), nextTs().slice(11), "supervisor", "report", { text: "Understood \u2014 skipping the GPU docking run. I\u2019ll rely on the panel data alone and note CX-14\u2019s MAP4K4 flag as unconfirmed." }));
          push(sid, ev(rid(), nextTs().slice(11), "system", "session.idle"));
        } else {
          s.running = true;
          s.last_kind = "turn.start";
        }
      }
      return delay({ ok: true });
    },

    listRoles: () => delay(clone(ROLES)),
    searchMemory: (layer, q) =>
      delay({
        layer,
        query: q,
        hits: [
          { layer: "project", score: 0.91, text: "CX-14 previously flagged in a 2025 run for a weak GAK interaction \u2014 worth checking alongside MAP4K4." },
          { layer: "global", score: 0.78, text: "S(10) is preferred over the Gini coefficient when the panel has gaps in Kd coverage." },
        ],
      }),
    gitHistory: () => delay(clone(GIT)),
    getCommit: (sha) =>
      delay({
        sha,
        subject: (GIT.commits.find((c) => c.sha === sha)?.subject) ?? "commit",
        author: "coscientist-evolution",
        ts: 1784498000,
        parents: [],
        files: [
          { path: "tools/example/server.py", additions: 42, deletions: 6 },
          { path: "roles/subagents/example.yaml", additions: 11, deletions: 0 },
        ],
      }),
    spawnEvolution: (command) => delay({ session_id: "evo-mock-1", command }),
    versions: () => delay({ versions: [], active: null }),
    activateVersion: (_id) => delay({ versions: [], active: null }),

    // ---- hypothesis engine (mock: canned parallel sets, deterministic) ----
    startHypothesis: (goal, n = 4) => mockStartHypothesis(goal, n),
    refineHypothesis: (hid, parentId, feedback, n = 4) => {
      const rec = MOCK_HYP_STORE.find((r) => r.id === hid)!;
      const round = rec.rounds.length;
      const hyps = MOCK_HYPS.slice(0, n).map((h, i) => ({
        id: `h${round}_${i}`,
        statement: `${feedback ? "[" + feedback + "] " : ""}${h.statement}`,
        rationale: h.rationale,
        round,
        parent_id: parentId,
      }));
      rec.rounds.push({ round, parent_id: parentId, feedback: feedback ?? null, served_by: "claude-opus-4-8 (mock)", hypotheses: hyps });
      return delay(clone(rec), 700);
    },
    selectHypothesis: (hid, hypId, note) => {
      const rec = MOCK_HYP_STORE.find((r) => r.id === hid)!;
      rec.selected_id = hypId;
      rec.select_note = note ?? null;
      // Server-side sync: a brief-linked search writes the choice into the brief.
      const bid = briefLinks.get(hid);
      const brief = bid ? briefs.find((b) => b.id === bid) : undefined;
      if (brief && brief.hypothesis_session_id === hid) {
        const card = rec.rounds.flatMap((r) => r.hypotheses).find((h) => h.id === hypId);
        brief.hypothesis = card ? { id: card.id, statement: card.statement, rationale: card.rationale, note: note ?? null } : null;
      }
      return delay(clone(rec));
    },
    getHypothesis: (hid) => delay(clone(MOCK_HYP_STORE.find((r) => r.id === hid)!)),
    listHypothesisSessions: () =>
      delay(
        MOCK_HYP_STORE.map((r) => ({
          id: r.id,
          goal: r.goal,
          created: r.created,
          rounds: r.rounds.length,
          latest_count: r.rounds[r.rounds.length - 1].hypotheses.length,
          selected_id: r.selected_id,
        })),
      ),

    // ---- onboarding phase (O1): in-memory briefs mirroring api/onboarding.py ----
    createBrief: (fields) => {
      const rec: BriefRecord = {
        ...emptyBrief(),
        ...fields,
        id: `brief-${briefs.length + 1}`,
        status: "draft",
        created: "2026-07-20 00:00:00",
        updated: "2026-07-20 00:00:00",
        hypothesis_session_id: null,
        hypothesis: null,
        session_id: null,
      };
      briefs.unshift(rec);
      return delay(clone(rec));
    },
    listBriefs: () =>
      delay(
        briefs.map((b) => ({
          id: b.id,
          title: b.title,
          research_question: b.research_question,
          status: b.status,
          created: b.created,
          updated: b.updated,
          session_id: b.session_id,
          hypothesis_session_id: b.hypothesis_session_id,
          hypothesis: b.hypothesis?.statement ?? null,
          data_count: b.data.length,
        })),
      ),
    getBrief: (bid) => delay(clone(briefs.find((b) => b.id === bid)!)),
    updateBrief: (bid, patch) => {
      const rec = briefs.find((b) => b.id === bid)!;
      Object.assign(rec, patch);
      return delay(clone(rec));
    },
    deleteBrief: (bid) => {
      const i = briefs.findIndex((b) => b.id === bid);
      if (i >= 0) briefs.splice(i, 1);
      return delay({ ok: true });
    },
    previewBrief: (bid) => {
      const rec = briefs.find((b) => b.id === bid)!;
      const missing = (["title", "research_question"] as const).filter((f) => !rec[f].trim());
      const text = renderMockBrief(rec);
      const size = new TextEncoder().encode(text).length;
      return delay({
        text,
        missing,
        data_problems: [],
        size_bytes: size,
        max_bytes: 100_000,
        too_large: size > 100_000,
        missing_for_registration: REGISTRATION_FIELDS.filter((f) => !rec[f].trim()),
        pending_imports: importsStore.filter((i) => i.status === "pending").map((i) => i.id),
        completeness: mockCompleteness(rec),
      });
    },
    briefHypotheses: async (bid, n = 4) => {
      const rec = briefs.find((b) => b.id === bid)!;
      const hyp = await mockStartHypothesis(rec.research_question || rec.title, n, rec.id);
      rec.hypothesis_session_id = hyp.id;
      rec.hypothesis = null; // a fresh search resets the selection
      return hyp;
    },
    launchBrief: (bid) => {
      const rec = briefs.find((b) => b.id === bid)!;
      const id = "s_" + rid().slice(0, 4);
      sessions.unshift({ session_id: id, task: rec.title, running: false, blocked: false, last_kind: "session.idle", has_brief: true, brief_title: rec.title, hypothesis: rec.hypothesis?.statement ?? null });
      events[id] = [
        ev(rid(), nextTs().slice(11), "human", "research.requested", { task: rec.title, brief_id: rec.id }),
        ev(rid(), nextTs().slice(11), "human", "session.brief", { brief_id: rec.id, title: rec.title, text: renderMockBrief(rec) }),
        ev(rid(), nextTs().slice(11), "system", "session.start"),
        ev(rid(), nextTs().slice(11), "system", "session.idle"),
      ];
      rec.status = "launched";
      rec.session_id = id;
      sessionBriefs.set(id, rec);
      return delay({ session_id: id, task: rec.title, brief_id: rec.id }, 500);
    },
    getSessionBrief: (sid) => {
      const rec = sessionBriefs.get(sid);
      if (!rec) return Promise.reject(new Error("404"));
      return delay({ ...clone(rec), text: renderMockBrief(rec) });
    },
    downloadLibrary: async () => {},

    // ---- project tree + advisor + imports (O2) ----
    registerBrief: (bid, visibility) => {
      const rec = briefs.find((b) => b.id === bid)!;
      if (visibility) rec.visibility = visibility;
      let n = nodes.find((x) => x.id === rec.node_id);
      const st = { title: rec.title, domain: rec.domain, research_question: rec.research_question, objectives: rec.objectives, significance: rec.significance, prior_work: rec.prior_work, open_gap: rec.open_gap, evaluation_protocol: rec.evaluation_protocol, success_criteria: rec.success_criteria, task_definition: rec.task_definition, existing_results: rec.existing_results, data_access: rec.data_access, data_notes: rec.data_notes, keywords: rec.keywords, data: rec.data.map((d) => ({ path: d.path, description: d.description })) };
      const changed = !n || JSON.stringify(n.statement) !== JSON.stringify(st);
      if (!n) {
        n = { id: "p-20260906-" + rid().slice(0, 6), title: rec.title, research_question: rec.research_question, domain: rec.domain, keywords: rec.keywords, owner: { user_id: me, display_name: "alice" }, visibility: rec.visibility, status: "registered", parent_node: rec.parent_node, created: "2026-09-06 12:00:00", updated: "2026-09-06 12:00:00", session_count: 0, outcome: "", active_version: null, version_count: 0, custom_tools: [], dataset_count: rec.data.length, statement: st, revisions: 1, links: { sessions: [], harness_versions: [], tools: ["fs_read", "py_exec"], custom_tools: [], roles: ["supervisor", "data_analyst", "generalist_researcher"], datasets: [], outcomes: [] }, edges: [], neighbours: [], is_owner: true };
        nodes.push(n);
        rec.node_id = n.id;
        // deterministic adjacency: any other node with a shared keyword
        for (const o of nodes) {
          if (o.id === n.id) continue;
          const shared = o.keywords.filter((k) => rec.keywords.map((x) => x.toLowerCase()).includes(k.toLowerCase()));
          if (shared.length) edges.push({ id: rid(), src: n.id, dst: o.id, type: "adjacent", weight: Math.min(1, 0.3 + 0.2 * shared.length), source: "keyword", status: "proposed", rationale: "Shared terms: " + shared.join(", ") });
        }
        if (rec.parent_node && nodes.find((x) => x.id === rec.parent_node)) edges.push({ id: rid(), src: n.id, dst: rec.parent_node, type: "subproblem", weight: 1, source: "human", status: "confirmed", rationale: "Started as a subproblem of the parent." });
      } else {
        Object.assign(n, { title: rec.title, research_question: rec.research_question, domain: rec.domain, keywords: rec.keywords, visibility: rec.visibility, statement: st, revisions: changed ? n.revisions + 1 : n.revisions });
      }
      const node = n;
      if (changed || !recs.has(node.id)) {
        running.add(node.id);
        setTimeout(() => { recs.set(node.id, cannedRec(node)); running.delete(node.id); }, 1800);
      }
      return delay({ node: nodeView(node), changed, advisor_started: changed, brief: clone(rec) });
    },
    projectTree: () => delay({ nodes: nodes.map(summaryOf), edges: edges.filter((e) => e.status !== "rejected"), me }),
    getProject: (pid) => {
      const n = nodes.find((x) => x.id === pid);
      return n ? delay(nodeView(n)) : Promise.reject(new Error("404"));
    },
    projectNear: (pid) => {
      const n = nodes.find((x) => x.id === pid)!;
      const v = nodeView(n);
      return delay({ self: summaryOf(n), neighbours: v.neighbours, edges: v.edges });
    },
    patchProject: (pid, patch) => {
      const n = nodes.find((x) => x.id === pid)!;
      if (patch.status) n.status = patch.status as ProjectNodeView["status"];
      if (patch.visibility) n.visibility = patch.visibility;
      if (patch.keywords) { n.keywords = patch.keywords; n.statement.keywords = patch.keywords; }
      return delay(nodeView(n));
    },
    declareEdge: (pid, dst, type, rationale) => {
      edges.push({ id: rid(), src: pid, dst, type: type as ProjectEdge["type"], weight: 1, source: "human", status: "confirmed", rationale });
      return delay(nodeView(nodes.find((x) => x.id === pid)!));
    },
    decideEdge: (pid, eid, decision) => {
      const e = edges.find((x) => x.id === eid);
      if (e) e.status = decision === "confirm" ? "confirmed" : "rejected";
      return delay(nodeView(nodes.find((x) => x.id === pid)!));
    },
    spawnBrief: (pid) => {
      const n = nodes.find((x) => x.id === pid)!;
      const rec: BriefRecord = { ...emptyBrief(), domain: n.domain, keywords: [...n.keywords], prior_work: `Derived from "${n.title}" (${n.id}). That problem asks: ${n.research_question}`, parent_node: n.id, visibility: n.visibility, id: `brief-${briefs.length + 1}`, status: "draft", created: "2026-09-06 12:00:00", updated: "2026-09-06 12:00:00", hypothesis_session_id: null, hypothesis: null, session_id: null };
      briefs.unshift(rec);
      return delay(clone(rec));
    },
    advise: (pid) => {
      const n = nodes.find((x) => x.id === pid)!;
      running.add(pid);
      setTimeout(() => { recs.set(pid, cannedRec(n)); running.delete(pid); }, 1800);
      return delay({ ok: true, running: true, started: true });
    },
    getRecommendations: (pid) => delay({ running: running.has(pid), latest: recs.get(pid) ? clone(recs.get(pid)!) : null, pending_imports: importsStore.filter((i) => i.status === "pending"), imports: importsStore.filter((i) => i.node_id === pid) }, 120),
    createImport: (pid, body) => {
      const iid = "imp-20260906-120000-" + rid().slice(0, 6);
      const sid = `evo-import-${iid}`;
      const rec: ImportRecord = { id: iid, kind: body.kind, mode: body.kind === "harness" ? body.mode ?? "adopt" : undefined, user_id: me, owner: body.owner, owner_name: nodes.find((n) => n.owner.user_id === body.owner)?.owner.display_name ?? "colleague", version_id: body.version_id, sha: "b7f919eda3c7", summary: body.kind === "harness" ? "Add peak-fitting tool + data_analyst wiring" : (body.tools ?? []).join(", "), tools: body.tools, roles: body.roles, node_id: pid, session_id: sid, request_id: rid(), status: "pending", risks: ["the donor version predates the R17 compat gate (no tests/test_contract_compat.py); only the v0 smoke can be run"], smoke: { ran: true, ok: true, tests: ["tests/test_smoke_v0.py"] }, diffstat: " roles/subagents/data_analyst.yaml | 1 +\n tools/peak_fit/__init__.py        | 0\n tools/peak_fit/server.py          | 142 ++++\n 3 files changed, 143 insertions(+)", diff_preview: "diff --git a/roles/subagents/data_analyst.yaml b/roles/subagents/data_analyst.yaml\n@@ -9,3 +9,4 @@ tools:\n   - longjob\n   - ocr\n   - latex_compile\n+  - peak_fit", rollback_to: { sha: "e90dd34", version_id: null }, created: "2026-09-06 12:01:00" };
      importsStore.push(rec);
      pending[sid] = [{ request_id: rec.request_id!, session_id: sid, requester: "project-tree advisor", title: body.kind === "harness" ? `Import ${rec.owner_name}'s harness version: ${rec.summary}` : `Import tools ${rec.summary} from ${rec.owner_name}`, action: body.kind === "harness" ? `Import ${rec.owner_name}'s harness version and switch to it.` : `Add ${rec.summary} to your harness.`, detail: `**Smoke + compat gate:** passed in a temporary worktree ✓\n\n\`\`\`\n${rec.diffstat}\n\`\`\``, detailMarkdown: true, meta: `${rec.version_id} · ${rec.mode ?? "tool"} · smoke ✓` }];
      sessions.unshift({ session_id: sid, task: `import ${rec.summary}`, running: false, blocked: true, last_kind: "hitl.pending" });
      events[sid] = [ev(rid(), nextTs().slice(11), "human", "import.requested", { import_id: iid }), ev(rid(), nextTs().slice(11), "system", "hitl.pending", { summary: pending[sid][0].title })];
      return delay(clone(rec), 900);
    },
    getImport: (iid) => delay(clone(importsStore.find((i) => i.id === iid)!)),
    listImports: () => delay(clone(importsStore)),
    evolveImport: (iid) => {
      const rec = importsStore.find((i) => i.id === iid)!;
      rec.status = "evolving";
      rec.fallback_session = "evo-mock-1";
      return delay({ ok: true, session_id: "evo-mock-1", import: clone(rec) });
    },
    shareVersion: () => delay({ versions: [], active: null }),
  };
}
