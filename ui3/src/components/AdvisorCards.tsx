import { useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "../state/store";
import { HttpError } from "../lib/api";
import Markdown from "./Markdown";
import type { AdvisorRecord, AdvisorTool, BriefRecord, ImportRecord, RecommendationsView } from "../lib/types";

// O2: the background advisor's recommendations for one project node, and the
// human-gated imports they lead to. The record is server-authoritative
// (state/projects/recommendations/<pid>/latest.json); this component polls it
// while a run is in flight and renders: statement review (gaps by question),
// related problems, the harness recommendation (→ approval card → active
// version / switch back), tool imports (→ approval card → imported list), and a
// footer with served_by / cost / re-run. Everything here is optional: the
// wizard launches without any of it.

export interface AdvisorState extends RecommendationsView {
  loading: boolean;
  refresh: () => Promise<void>;
  rerun: () => Promise<void>;
  /** A run was just requested (register / re-run): keep polling until its record lands. */
  expectRun: () => void;
}

/** Poll GET /projects/{pid}/recommendations every 3 s while `running`. */
export function useAdvisor(nodeId: string | null): AdvisorState {
  const { api } = useApp();
  const [view, setView] = useState<RecommendationsView>({ running: false, latest: null, pending_imports: [], imports: [] });
  const [loading, setLoading] = useState(false);
  const timer = useRef<number | null>(null);
  const nodeRef = useRef(nodeId);
  nodeRef.current = nodeId;
  // Polls still owed after a run was requested: the server marks `running`
  // from the spawn, but a slow start or a fast skip can land between polls,
  // so keep polling for a bounded number of rounds until a record appears.
  const [expect, setExpect] = useState(0);
  const seenRec = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    const pid = nodeRef.current;
    if (!pid) return;
    try {
      const v = await api.getRecommendations(pid);
      if (nodeRef.current !== pid) return;
      setView(v);
      const rid = v.latest?.rec_id ?? null;
      if (v.running) setExpect((n) => Math.max(n, 1));
      else if (rid && rid !== seenRec.current) setExpect(0);
      else setExpect((n) => Math.max(0, n - 1));
      seenRec.current = rid;
    } catch {
      /* keep the last view */
    }
  }, [api]);

  const expectRun = useCallback(() => setExpect(12), []);

  useEffect(() => {
    setView({ running: false, latest: null, pending_imports: [], imports: [] });
    setExpect(0);
    seenRec.current = null;
    if (!nodeId) return;
    let cancelled = false;
    setLoading(true);
    void refresh().finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [nodeId, refresh]);

  // Poll while running (or while an import is pending, so approvals made
  // elsewhere — the Evolution drawer — show up here too).
  useEffect(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = null;
    if (!nodeId) return;
    if (view.running || view.pending_imports.length > 0 || expect > 0) {
      timer.current = window.setInterval(() => void refresh(), 3000);
    }
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [nodeId, view.running, view.pending_imports.length, expect, refresh]);

  const rerun = useCallback(async () => {
    const pid = nodeRef.current;
    if (!pid) return;
    try {
      await api.advise(pid);
      setView((v) => ({ ...v, running: true }));
      setExpect(12);
      window.setTimeout(() => void refresh(), 1200);
    } catch {
      /* surfaced by the caller's banner if needed */
    }
  }, [api, refresh]);

  return { ...view, loading, refresh, rerun, expectRun };
}

const card: React.CSSProperties = { border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg1)", padding: "13px 15px" };
const cardTitle: React.CSSProperties = { fontSize: 11, fontWeight: 700, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", display: "flex", alignItems: "center", gap: 8 };
const chip = (bg: string, fg: string): React.CSSProperties => ({ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 700, padding: "1px 7px", borderRadius: 5, background: bg, color: fg, letterSpacing: ".04em" });
const btn = (kind: "primary" | "ghost" | "danger" | "ok"): React.CSSProperties => ({
  padding: "7px 12px",
  borderRadius: 8,
  fontSize: 12.5,
  fontWeight: 700,
  background: kind === "primary" ? "var(--accent)" : kind === "ok" ? "var(--ok)" : kind === "danger" ? "var(--err-soft)" : "var(--bg2)",
  color: kind === "primary" ? "#06121c" : kind === "ok" ? "#04160c" : kind === "danger" ? "var(--err)" : "var(--mid)",
  border: kind === "ghost" ? "1px solid var(--border)" : kind === "danger" ? "1px solid var(--err)" : "none",
});
const pct = (x: number) => `${Math.round(x * 100)}%`;
const cost = (u?: { input_tokens: number; output_tokens: number }) => {
  if (!u) return null;
  // Rough platform-model pricing so the human sees an order of magnitude.
  const usd = (u.input_tokens * 15 + u.output_tokens * 75) / 1_000_000;
  return `${u.input_tokens + u.output_tokens} tokens · ≈$${usd.toFixed(2)}`;
};

export function AdvisorPill({ nodeId, state }: { nodeId: string | null; state: AdvisorState }) {
  if (!nodeId) return null;
  const r = state.latest;
  const running = state.running;
  const n = r?.status === "ready" ? (r.harness_import ? 1 : 0) + (r.tool_imports?.length ?? 0) + (r.related_problems?.length ?? 0) : 0;
  const label = running ? "advisor running" : !r ? "advisor queued" : r.status === "failed" ? "advisor failed" : r.status === "skipped" ? "advisor: nothing to reuse yet" : n ? `advisor: ${n} recommendation${n === 1 ? "" : "s"}` : "advisor: start from scratch";
  const color = running ? "var(--accent)" : r?.status === "failed" ? "var(--err)" : r?.status === "ready" && n ? "var(--ok)" : "var(--lo)";
  return (
    <span title="The project-tree advisor runs in the background (Fable, Opus fallback) whenever the statement changes. It never blocks." style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 9px", borderRadius: 7, border: `1px solid ${color}`, color, fontFamily: "var(--mono)", fontSize: 10.5, fontWeight: 600 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, animation: running ? "pulse 1.4s infinite" : undefined }} />
      {label}
    </span>
  );
}

export default function AdvisorCards({
  nodeId, brief, roles, state, onJumpToQuestion, onAddKeywords, onOpenNode, onGoHypotheses,
}: {
  nodeId: string;
  brief: BriefRecord;
  roles: string[];
  state: AdvisorState;
  onJumpToQuestion: (q: number) => void;
  onAddKeywords: (kws: string[]) => void;
  onOpenNode: (pid: string) => void;
  onGoHypotheses: () => void;
}) {
  const { api, activateVersion, refreshSessions, loadVersions, setMainView, setEvolutionCommand } = useApp();
  const r = state.latest;
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"adopt" | "merge" | null>(null);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [wiring, setWiring] = useState<Record<string, string[]>>({});

  useEffect(() => {
    // Default tool selection + wiring from the record; re-seeded per record.
    const p: Record<string, boolean> = {};
    const w: Record<string, string[]> = {};
    for (const t of r?.tool_imports ?? []) { p[t.name] = true; w[t.name] = t.role_wiring; }
    setPicked(p);
    setWiring(w);
    setMode(r?.harness_import?.mode_hint ?? null);
  }, [r?.rec_id]);

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
      await state.refresh();
    } catch (e) {
      setError(e instanceof HttpError ? e.message : "Something went wrong.");
    } finally {
      setBusy(null);
    }
  };

  const harnessImport = (mode: "adopt" | "merge") => {
    const h = r?.harness_import;
    if (!h) return;
    void run("harness", () => api.createImport(nodeId, { kind: "harness", owner: h.owner, version_id: h.version_id, mode }));
  };
  const toolImport = () => {
    const tools = (r?.tool_imports ?? []).filter((t) => picked[t.name]);
    if (!tools.length) return;
    // One import per donor version (the API stages one worktree per request).
    const byVersion = new Map<string, AdvisorTool[]>();
    for (const t of tools) byVersion.set(`${t.owner}|${t.version_id}`, [...(byVersion.get(`${t.owner}|${t.version_id}`) ?? []), t]);
    const [first] = byVersion.values();
    const rolesFor = Array.from(new Set(first.flatMap((t) => wiring[t.name] ?? t.role_wiring)));
    void run("tools", () => api.createImport(nodeId, { kind: "tool", owner: first[0].owner, version_id: first[0].version_id, tools: first.map((t) => t.name), roles: rolesFor }));
  };
  const decide = (imp: ImportRecord, decision: "approve" | "reject") =>
    run(`decide:${imp.id}`, async () => {
      if (!imp.request_id) return;
      await api.answerHitl(imp.session_id, imp.request_id, decision);
      await refreshSessions();
      loadVersions();
    });
  const switchBack = (imp: ImportRecord) =>
    run(`back:${imp.id}`, async () => {
      const target = imp.rollback_to?.version_id ?? imp.rollback_to?.sha;
      if (target) await activateVersion(target);
    });
  const evolve = (imp: ImportRecord) =>
    run(`evolve:${imp.id}`, async () => {
      await api.evolveImport(imp.id);
      setEvolutionCommand("");
      setMainView("evolution");
    });

  const importsFor = (kind: "harness" | "tool") => state.imports.filter((i) => i.kind === kind);
  const pendingFor = (kind: "harness" | "tool") => state.pending_imports.find((i) => i.kind === kind && i.node_id === nodeId) ?? null;
  const appliedHarness = importsFor("harness").find((i) => i.status === "applied") ?? null;
  const appliedTools = importsFor("tool").filter((i) => i.status === "applied");
  const hasOwnVersions = (brief.harness?.version_id ?? null) !== null;

  if (state.loading && !r) return <div style={{ ...card, color: "var(--lo)", fontSize: 12.5 }}>Loading recommendations…</div>;

  if (!r || state.running) {
    if (!r && !state.running && !state.loading) {
      return (
        <div style={{ ...card, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.45, display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ flex: 1 }}>No recommendation yet for this problem.</span>
          <button onClick={() => void state.rerun()} style={btn("ghost")}>Run the advisor</button>
        </div>
      );
    }
    return (
      <div style={{ ...card, display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", animation: "pulse 1.4s infinite", flex: "0 0 auto" }} />
        <div style={{ flex: 1, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.45 }}>
          <b style={{ color: "var(--hi)", fontWeight: 600 }}>The advisor is reading the project tree.</b> It compares your statement with every shared problem, their outcomes, harness versions and evolved tools, then recommends what to reuse. Usually under a minute; you can keep going — nothing here blocks the launch.
        </div>
      </div>
    );
  }

  if (r.status === "failed") {
    return (
      <div style={{ ...card, borderColor: "var(--err)" }}>
        <div style={{ ...cardTitle, color: "var(--err)" }}>Advisor failed</div>
        <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--mid)", whiteSpace: "pre-wrap" }}>{r.error ?? "Unknown error."}</div>
        <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--lo)" }}>Tried: {(r as { attempts?: { model: string; outcome: string }[] }).attempts?.map((a) => `${a.model} (${a.outcome})`).join(", ") || r.served_by || "—"}. The keyword-based neighbours on the left still apply.</div>
        <button onClick={() => void state.rerun()} style={{ ...btn("ghost"), marginTop: 10 }}>Retry</button>
      </div>
    );
  }

  const gaps = r.statement_feedback?.missing ?? [];
  const notes = r.statement_feedback?.notes ?? [];
  const kws = (r.statement_feedback?.suggested_keywords ?? []).filter((k) => !brief.keywords.some((x) => x.toLowerCase() === k.toLowerCase()));
  const h = r.harness_import ?? null;
  const pendingH = pendingFor("harness");
  const pendingT = pendingFor("tool");
  const fallbackT = importsFor("tool").find((i) => i.status === "fallback" || i.status === "conflict") ?? importsFor("harness").find((i) => i.status === "conflict") ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {error && (
        <div style={{ padding: "9px 13px", background: "var(--err-soft)", border: "1px solid var(--err)", borderRadius: 9, fontSize: 12.5, color: "var(--err)", display: "flex", gap: 8 }}>
          <span style={{ flex: 1 }}>{error}</span>
          <button onClick={() => setError(null)} style={{ color: "var(--err)" }}>✕</button>
        </div>
      )}

      {/* summary */}
      <div style={card}>
        <div style={cardTitle}>
          Advisor summary
          {r.status === "skipped" && <span style={chip("var(--bg3)", "var(--lo)")}>empty tree</span>}
        </div>
        <div style={{ marginTop: 6, fontSize: 13.5, color: "var(--hi)", lineHeight: 1.5 }}>{r.summary || "No summary."}</div>
        {r.start_from_scratch_rationale && (
          <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--mid)" }}><b style={{ color: "var(--hi)", fontWeight: 600 }}>Why nothing is imported:</b> {r.start_from_scratch_rationale}</div>
        )}
      </div>

      {/* statement review */}
      {(gaps.length > 0 || notes.length > 0 || kws.length > 0) && (
        <div style={card}>
          <div style={cardTitle}>Statement review</div>
          {gaps.length > 0 && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 5 }}>
              {gaps.map((g, i) => (
                <div key={i} style={{ display: "flex", gap: 9, alignItems: "flex-start", fontSize: 12.5 }}>
                  <button onClick={() => g.q >= 0 && onJumpToQuestion(g.q)} title="Jump to this question" style={{ ...chip("var(--warn-soft)", "var(--warn)"), flex: "0 0 auto", cursor: g.q >= 0 ? "pointer" : "default", marginTop: 2 }}>{g.q >= 0 ? `Q${g.q}` : "note"}</button>
                  <span style={{ color: "var(--hi)", lineHeight: 1.45 }}>{g.issue}</span>
                </div>
              ))}
            </div>
          )}
          {notes.length > 0 && (
            <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>
              {notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
          {kws.length > 0 && (
            <div style={{ marginTop: 9, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
              <span style={{ fontSize: 11.5, color: "var(--lo)" }}>Suggested keywords:</span>
              {kws.map((k) => (
                <button key={k} onClick={() => onAddKeywords([k])} title="Add to your keywords" style={{ padding: "2px 9px", borderRadius: 7, border: "1px dashed var(--accent)", color: "var(--accent)", fontSize: 12 }}>+ {k}</button>
              ))}
              <button onClick={() => onAddKeywords(kws)} style={{ fontSize: 11.5, color: "var(--accent)", fontWeight: 600 }}>add all</button>
            </div>
          )}
        </div>
      )}

      {/* related problems */}
      {(r.related_problems?.length ?? 0) > 0 && (
        <div style={card}>
          <div style={cardTitle}>Related problems</div>
          <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 7 }}>
            {r.related_problems!.map((x) => (
              <div key={x.node_id} style={{ padding: "8px 10px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 9 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={chip("var(--accent-soft)", "var(--accent)")}>{x.relation.replace("_", " ")}</span>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{pct(x.confidence)}</span>
                  <span style={{ flex: 1 }} />
                  <button onClick={() => onOpenNode(x.node_id)} style={{ fontSize: 11.5, color: "var(--accent)", fontWeight: 600 }}>open →</button>
                </div>
                <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.45 }}>{x.rationale}</div>
                <div style={{ marginTop: 3, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{x.node_id} · confirm or reject the relation from the near-tree</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* harness recommendation */}
      <div style={{ ...card, borderColor: h ? "var(--evo)" : "var(--border)" }}>
        <div style={cardTitle}>
          Harness
          {h && <span style={chip("var(--evo-soft)", "var(--evo)")}>import recommended · {pct(h.confidence)}</span>}
          {appliedHarness && <span style={chip("var(--ok-soft)", "var(--ok)")}>imported</span>}
        </div>
        {!h && !appliedHarness && (
          <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>
            No colleague's harness fits this problem well enough to import. You start from {hasOwnVersions ? "your active version" : "the base harness"}; the evolution agent can grow it as the session runs.
          </div>
        )}
        {h && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--hi)" }}>{h.summary || h.version_id}</div>
            <div style={{ marginTop: 2, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{h.owner_name ?? h.owner} · {h.version_id}{h.from_node_title ? ` · evolved for “${h.from_node_title}”` : ""}</div>
            <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.5 }}>{h.rationale}</div>
            {h.custom_tools && h.custom_tools.length > 0 && (
              <div style={{ marginTop: 5, fontSize: 12, color: "var(--mid)" }}>Brings: {h.custom_tools.map((t) => <code key={t} style={{ fontFamily: "var(--mono)", fontSize: 11, marginRight: 6, color: "var(--hi)" }}>{t}</code>)}</div>
            )}
            {h.risks.length > 0 && (
              <div style={{ marginTop: 7, padding: "7px 10px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 8, fontSize: 12, color: "var(--hi)" }}>
                <div style={{ fontSize: 10.5, fontWeight: 700, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".06em" }}>Risks (computed against your harness)</div>
                <ul style={{ margin: "4px 0 0", paddingLeft: 16, lineHeight: 1.45 }}>{h.risks.map((x, i) => <li key={i}>{x}</li>)}</ul>
              </div>
            )}
            {h.alternatives.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--lo)" }}>Alternatives: {h.alternatives.map((a) => `${a.version_id} (${a.why})`).join("; ")}</div>
            )}
            {!pendingH && !appliedHarness && (
              <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--mid)" }}>
                  <input type="radio" checked={(mode ?? h.mode_hint) === "adopt"} onChange={() => setMode("adopt")} /> Import &amp; switch <span style={{ color: "var(--lo)" }}>(their version becomes your active harness; switch back any time)</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--mid)" }}>
                  <input type="radio" checked={(mode ?? h.mode_hint) === "merge"} onChange={() => setMode("merge")} /> Merge into mine <span style={{ color: "var(--lo)" }}>(keeps your own evolutions)</span>
                </label>
                <span style={{ flex: 1 }} />
                <button onClick={() => harnessImport(mode ?? h.mode_hint)} disabled={busy === "harness"} style={btn("primary")}>
                  {busy === "harness" ? "Fetching + running the smoke gate…" : (mode ?? h.mode_hint) === "adopt" ? "Import & switch →" : "Merge into my harness →"}
                </button>
              </div>
            )}
          </div>
        )}
        {pendingH && <ApprovalCard imp={pendingH} busy={busy} onDecide={decide} />}
        {appliedHarness && (
          <div style={{ marginTop: 10, padding: "9px 12px", background: "var(--ok-soft)", border: "1px solid var(--ok)", borderRadius: 9, fontSize: 12.5, color: "var(--hi)", display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ flex: 1 }}>
              Active version: <b style={{ fontWeight: 600 }}>{appliedHarness.result?.version_id}</b> — imported from {appliedHarness.owner_name} ({appliedHarness.mode}). Sessions launched from now on run on it.
            </span>
            <button onClick={() => void switchBack(appliedHarness)} disabled={busy === `back:${appliedHarness.id}`} style={btn("ghost")}>{busy === `back:${appliedHarness.id}` ? "Switching…" : "Switch back"}</button>
          </div>
        )}
        {importsFor("harness").filter((i) => i.status === "failed" || i.status === "rejected").slice(-1).map((i) => (
          <div key={i.id} style={{ marginTop: 8, fontSize: 12, color: i.status === "failed" ? "var(--err)" : "var(--lo)" }}>
            Last import {i.status}{i.error ? `: ${i.error}` : ""}.
          </div>
        ))}
      </div>

      {/* tool imports */}
      {((r.tool_imports?.length ?? 0) > 0 || pendingT || appliedTools.length > 0) && (
        <div style={card}>
          <div style={cardTitle}>
            Tool imports
            {appliedTools.length > 0 && <span style={chip("var(--ok-soft)", "var(--ok)")}>{appliedTools.flatMap((i) => i.tools ?? []).length} imported</span>}
          </div>
          {appliedTools.length > 0 && (
            <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--hi)" }}>
              Imported: {appliedTools.flatMap((i) => i.tools ?? []).map((t) => <code key={t} style={{ fontFamily: "var(--mono)", fontSize: 11.5, marginRight: 6 }}>{t}</code>)} <span style={{ color: "var(--lo)" }}>(as version {appliedTools[appliedTools.length - 1].result?.version_id})</span>
            </div>
          )}
          {!pendingT && (r.tool_imports ?? []).filter((t) => !appliedTools.some((i) => (i.tools ?? []).includes(t.name))).length > 0 && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 7 }}>
              {(r.tool_imports ?? []).filter((t) => !appliedTools.some((i) => (i.tools ?? []).includes(t.name))).map((t) => (
                <label key={t.name} style={{ display: "flex", gap: 10, padding: "8px 10px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 9, cursor: "pointer" }}>
                  <input type="checkbox" checked={!!picked[t.name]} onChange={(e) => setPicked((p) => ({ ...p, [t.name]: e.target.checked }))} style={{ marginTop: 3 }} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <code style={{ fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--hi)", fontWeight: 600 }}>{t.name}</code>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{t.owner_name ?? t.owner} · {pct(t.confidence)}</span>
                    </span>
                    {t.description && <span style={{ display: "block", marginTop: 2, fontSize: 12, color: "var(--mid)" }}>{t.description}</span>}
                    <span style={{ display: "block", marginTop: 3, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.45 }}>{t.rationale}</span>
                    <span style={{ display: "flex", flexWrap: "wrap", gap: 5, marginTop: 6, alignItems: "center" }}>
                      <span style={{ fontSize: 11, color: "var(--lo)" }}>wire into:</span>
                      {roles.map((role) => {
                        const on = (wiring[t.name] ?? t.role_wiring).includes(role);
                        return (
                          <button key={role} onClick={(e) => { e.preventDefault(); setWiring((w) => { const cur = w[t.name] ?? t.role_wiring; return { ...w, [t.name]: on ? cur.filter((x) => x !== role) : [...cur, role] }; }); }} style={{ padding: "1px 8px", borderRadius: 6, border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`, background: on ? "var(--accent-soft)" : "transparent", color: on ? "var(--accent)" : "var(--lo)", fontSize: 11, fontFamily: "var(--mono)" }}>{role}</button>
                        );
                      })}
                    </span>
                  </span>
                </label>
              ))}
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button onClick={toolImport} disabled={busy === "tools" || !Object.values(picked).some(Boolean)} style={btn("primary")}>
                  {busy === "tools" ? "Staging + running the smoke gate…" : "Import selected tools →"}
                </button>
              </div>
            </div>
          )}
          {pendingT && <ApprovalCard imp={pendingT} busy={busy} onDecide={decide} />}
        </div>
      )}

      {/* fallback → evolution agent */}
      {fallbackT && fallbackT.status !== "evolving" && (
        <div style={{ ...card, borderColor: "var(--evo)" }}>
          <div style={{ ...cardTitle, color: "var(--evo)" }}>Needs the evolution agent</div>
          <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>
            {fallbackT.status === "conflict" ? "The donor's changes conflict with your harness." : "The plain checkout failed the smoke gate — the tool depends on scaffold changes from the donor."} The API prepared a command; the evolution agent will port what is needed in an isolated worktree and ask you to approve the merge.
          </div>
          <textarea readOnly value={fallbackT.evolution_command ?? ""} rows={4} style={{ width: "100%", marginTop: 8, padding: "8px 10px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12, fontFamily: "var(--mono)", resize: "vertical" }} />
          <div style={{ marginTop: 8, display: "flex", justifyContent: "flex-end" }}>
            <button onClick={() => void evolve(fallbackT)} disabled={busy === `evolve:${fallbackT.id}`} style={{ ...btn("primary"), background: "var(--evo)", color: "#100a1c" }}>{busy === `evolve:${fallbackT.id}` ? "Starting…" : "Run evolution →"}</button>
          </div>
        </div>
      )}

      {/* hypothesis seed */}
      {r.hypothesis_seed && (
        <div style={{ ...card, display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ flex: 1, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.45 }}>
            <b style={{ color: "var(--hi)", fontWeight: 600 }}>Hypothesis search {r.hypothesis_seed.suggested ? "recommended" : "optional"}.</b> {r.hypothesis_seed.why}
          </span>
          {r.hypothesis_seed.suggested && <button onClick={onGoHypotheses} style={btn("ghost")}>Go to hypotheses →</button>}
        </div>
      )}

      {/* footer */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)", flexWrap: "wrap" }}>
        <span>served by {r.served_by ?? "—"}</span>
        {cost(r.usage) && <span>· {cost(r.usage)}</span>}
        <span>· {r.created}</span>
        {(r.validation_notes?.length ?? 0) > 0 && <span title={r.validation_notes!.join("\n")}>· {r.validation_notes!.length} dropped by the validator</span>}
        <span style={{ flex: 1 }} />
        <button onClick={() => void state.rerun()} style={{ color: "var(--accent)", fontWeight: 600 }}>Re-run advisor</button>
      </div>
    </div>
  );
}

function ApprovalCard({ imp, busy, onDecide }: { imp: ImportRecord; busy: string | null; onDecide: (imp: ImportRecord, d: "approve" | "reject") => Promise<void> }) {
  const [showDiff, setShowDiff] = useState(false);
  const smoke = imp.smoke;
  return (
    <div style={{ marginTop: 10, border: "1px solid var(--warn)", borderRadius: 10, overflow: "hidden", animation: "ring 2s 1" }}>
      <div style={{ padding: "9px 12px", background: "var(--warn-soft)", borderBottom: "1px solid var(--warn)", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--warn)", animation: "pulse 1.6s infinite" }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".07em" }}>Approval required</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: smoke?.ok ? "var(--ok)" : smoke?.ran ? "var(--err)" : "var(--lo)" }}>{smoke?.ran ? (smoke.ok ? "smoke + compat ✓" : "smoke ✗") : "no tests in donor tree"}</span>
      </div>
      <div style={{ padding: "11px 12px", fontSize: 12.5, color: "var(--hi)", lineHeight: 1.5 }}>
        <div>{imp.kind === "harness" ? `Import ${imp.owner_name}'s version “${imp.summary}” (${imp.mode}).` : `Add ${(imp.tools ?? []).join(", ")} from ${imp.owner_name}'s version, wired into ${(imp.roles ?? []).join(", ")}.`}</div>
        {imp.rollback_to && <div style={{ marginTop: 4, fontSize: 11.5, color: "var(--mid)" }}>Rollback: activate {imp.rollback_to.version_id ?? `the bootstrap root (${imp.rollback_to.sha.slice(0, 8)})`}.</div>}
        {(imp.risks?.length ?? 0) > 0 && (
          <ul style={{ margin: "6px 0 0", paddingLeft: 16, fontSize: 12, color: "var(--warn)" }}>{imp.risks!.map((x, i) => <li key={i} style={{ color: "var(--hi)" }}>{x}</li>)}</ul>
        )}
        {(imp.donor_skills?.length ?? 0) > 0 && <div style={{ marginTop: 5, fontSize: 11.5, color: "var(--lo)" }}>Donor skills (not copied automatically): {imp.donor_skills!.join(", ")}</div>}
        {imp.diffstat && (
          <pre style={{ margin: "8px 0 0", padding: "7px 9px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 7, fontFamily: "var(--mono)", fontSize: 11, color: "var(--mid)", whiteSpace: "pre-wrap", maxHeight: 140, overflowY: "auto" }}>{imp.diffstat.trim()}</pre>
        )}
        {imp.diff_preview && (
          <button onClick={() => setShowDiff((s) => !s)} style={{ marginTop: 6, fontSize: 11.5, color: "var(--accent)", fontWeight: 600 }}>{showDiff ? "hide diff" : "show diff preview"}</button>
        )}
        {showDiff && imp.diff_preview && (
          <Markdown text={"```diff\n" + imp.diff_preview + "\n```"} style={{ marginTop: 6, maxHeight: 320, overflowY: "auto", fontSize: 11.5 }} />
        )}
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button onClick={() => void onDecide(imp, "approve")} disabled={busy === `decide:${imp.id}`} style={{ ...btn("ok"), flex: 1 }}>{busy === `decide:${imp.id}` ? "Applying…" : "Approve"}</button>
          <button onClick={() => void onDecide(imp, "reject")} disabled={busy === `decide:${imp.id}`} style={{ ...btn("danger"), flex: 1 }}>Reject</button>
        </div>
        <div style={{ marginTop: 6, fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>session {imp.session_id} · also in the Evolution tab's approvals</div>
      </div>
    </div>
  );
}
