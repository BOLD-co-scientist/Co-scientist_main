import type { Api, StreamHandle } from "./api";
import type {
  Ev,
  GitHistory,
  Hyp,
  HitlPending,
  EvoLogEntry,
  LibraryFile,
  RoleSummary,
  SessionSummary,
  Skill,
  SkillDir,
} from "./types";

// ============================================================
// Seed data for the Evolution view (a designed-but-stubbed surface until
// POST /evolution/commands lands). Exported so the view seeds identically in
// both mock and live modes — the hypothesis tree is a client-side artifact.
// ============================================================
export const SEED_HYPS: Hyp[] = [
  { id: "n0", parent: null, dir: null, status: "root", title: "Which candidates are truly kinome-selective?", detail: "The seed question for this session. The system fanned out three competing reads of the panel data; you chose which to pursue." },
  { id: "n1", parent: "n0", dir: "triage", status: "supported", title: "The 4 top compounds (S(10) < 0.05) are genuinely clean", detail: "Do the most selective hits hold up across the full 468-kinase panel?", evidence: "Supported \u2014 all four stay below the S(10) bar with no secondary hits." },
  { id: "n2", parent: "n0", dir: "offtarget", status: "path", title: "CX-14 has a real MAP4K4 liability", detail: "The analyst flagged an unexpected MAP4K4 hit (Kd \u2248 38 nM) outside the annotated panel. Worth chasing down." },
  { id: "n3", parent: "n0", dir: "triage", status: "parked", title: "Panel coverage gaps are hiding other off-targets", detail: "The 468-panel misses ~20% of the kinome \u2014 there may be liabilities we simply cannot see yet." },
  { id: "n4", parent: "n2", dir: "offtarget", status: "refuted", title: "It is a low-occupancy binding artifact", detail: "Perhaps the MAP4K4 signal is weak and reversible rather than a true liability.", evidence: "Refuted \u2014 the dose\u2013response is clean and saturable; occupancy is high." },
  { id: "n5", parent: "n2", dir: "offtarget", status: "path", title: "Genuine high-affinity off-target", detail: "The signal reflects real, tight binding to MAP4K4.", evidence: "Supported \u2014 docking gives \u0394G \u22129.4 kcal/mol in a well-formed pose." },
  { id: "n6", parent: "n2", dir: "offtarget", status: "parked", title: "Assay or crystallization artifact", detail: "The hit could be an artifact of the assay format rather than biology." },
  { id: "n7", parent: "n5", dir: "redesign", status: "proposed", title: "Redesign CX-14 to remove MAP4K4 affinity", detail: "A medicinal-chemistry route: keep on-target potency while designing out the off-target." },
  { id: "n8", parent: "n5", dir: "profile", status: "proposed", title: "Profile the full 32-compound series vs MAP4K4", detail: "Is this a CX-14 quirk, or a scaffold-wide liability across the whole series?" },
  { id: "n9", parent: "n5", dir: "synergy", status: "proposed", title: "Exploit MAP4K4 inhibition for therapeutic synergy", detail: "Reframe the off-target as a feature \u2014 MAP4K4 is itself a target in some indications." },
];

export const HYP_CHILDREN: Record<string, Array<{ dir: Hyp["dir"]; title: string }>> = {
  n7: [
    { dir: "redesign", title: "Add a bulky R3 substituent to clash with the MAP4K4 pocket" },
    { dir: "redesign", title: "Swap the hinge-binding scaffold entirely" },
  ],
  n8: [
    { dir: "profile", title: "Run the full 468-panel on all 32 compounds" },
    { dir: "profile", title: "Focused 12-kinase counter-screen around MAP4K4" },
  ],
  n9: [
    { dir: "synergy", title: "Check MAP4K4 inhibition in the disease model" },
    { dir: "synergy", title: "Literature scan: MAP4K4 + primary-target synergy" },
  ],
};

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
    answerHitl: (sid, _req, decision) => {
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
  };
}
