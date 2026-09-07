import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../state/store";
import type { EdgeType, ProjectEdge, ProjectNodeSummary, ProjectNodeView, ProjectTree } from "../lib/types";

// O2: the org-wide project tree. Every shared problem (and your private ones),
// clustered by domain, owner-coloured, status-shaped; edges typed. Click a node
// for its full statement (the five questions), sessions/outcomes, harness,
// tools and relations; confirm/reject relations; start a subproblem from it.
// Canvas idioms follow EvolutionGraph (drag to pan, wheel to zoom).

const EDGE_COLOR: Record<EdgeType, string> = { adjacent: "var(--accent)", subproblem: "var(--lav)", shares_dataset: "var(--grn)", shares_method: "var(--evo)", supersedes: "var(--warn)" };
const OWNER_PALETTE = ["var(--accent)", "var(--grn)", "var(--evo)", "var(--cyan)", "var(--lav)", "var(--warn)"];
const STATUS_SHAPE: Record<string, { r: number; dash?: string; opacity: number }> = {
  registered: { r: 9, opacity: 0.9 },
  active: { r: 11, opacity: 1 },
  solved: { r: 10, opacity: 1 },
  abandoned: { r: 7, dash: "3 3", opacity: 0.45 },
  superseded: { r: 7, dash: "3 3", opacity: 0.5 },
};
const COL_W = 250, ROW_H = 96, PADX = 60, PADY = 70;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

function layout(nodes: ProjectNodeSummary[]) {
  const domains = Array.from(new Set(nodes.map((n) => (n.domain || "other").trim().toLowerCase()))).sort();
  const pos = new Map<string, { x: number; y: number; col: number }>();
  domains.forEach((d, c) => {
    const members = nodes.filter((n) => (n.domain || "other").trim().toLowerCase() === d).sort((a, b) => (a.created ?? "").localeCompare(b.created ?? ""));
    members.forEach((n, i) => pos.set(n.id, { x: PADX + c * COL_W + COL_W / 2, y: PADY + i * ROW_H, col: c }));
  });
  const rows = Math.max(1, ...domains.map((d) => nodes.filter((n) => (n.domain || "other").trim().toLowerCase() === d).length));
  return { domains, pos, width: PADX * 2 + Math.max(1, domains.length) * COL_W, height: PADY + rows * ROW_H + 40 };
}

export default function ProjectTreeView() {
  const { api, user, projectFocus, openProject, startBriefFromNode, setOnboardingBriefId, setMainView, select } = useApp();
  const [tree, setTree] = useState<ProjectTree | null>(null);
  const [sel, setSel] = useState<string | null>(projectFocus);
  const [detail, setDetail] = useState<ProjectNodeView | null>(null);
  const [t, setT] = useState({ x: 0, y: 0, k: 1 });
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: boolean } | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try { setTree(await api.projectTree()); } catch { /* keep */ }
  }, [api]);
  useEffect(() => { void load(); const id = setInterval(() => void load(), 10000); return () => clearInterval(id); }, [load]);
  useEffect(() => { if (projectFocus) setSel(projectFocus); }, [projectFocus]);
  useEffect(() => {
    if (!sel) { setDetail(null); return; }
    let cancelled = false;
    api.getProject(sel).then((d) => { if (!cancelled) setDetail(d); }).catch(() => { if (!cancelled) { setDetail(null); setSel(null); } });
    return () => { cancelled = true; };
  }, [sel, api]);

  const nodes = useMemo(() => {
    const all = tree?.nodes ?? [];
    const q = query.trim().toLowerCase();
    return q ? all.filter((n) => [n.title, n.research_question, n.domain, n.owner.display_name, ...n.keywords].join(" ").toLowerCase().includes(q)) : all;
  }, [tree, query]);
  const { domains, pos, width, height } = useMemo(() => layout(nodes), [nodes]);
  const owners = useMemo(() => Array.from(new Set((tree?.nodes ?? []).map((n) => n.owner.user_id))).sort(), [tree]);
  const ownerColor = (uid: string) => OWNER_PALETTE[Math.max(0, owners.indexOf(uid)) % OWNER_PALETTE.length];
  const edges = (tree?.edges ?? []).filter((e) => pos.has(e.src) && pos.has(e.dst));

  const onWheel = (e: React.WheelEvent) => {
    if (!wrap.current) return;
    e.preventDefault();
    const r = wrap.current.getBoundingClientRect();
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    const k2 = clamp(t.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.4, 2.4);
    setT({ k: k2, x: cx - (cx - t.x) * (k2 / t.k), y: cy - (cy - t.y) * (k2 / t.k) });
  };
  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY, ox: t.x, oy: t.y, moved: false }; };
  const onMove = (e: React.MouseEvent) => {
    const d = drag.current; if (!d) return;
    if (!d.moved && Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) d.moved = true;
    if (d.moved) setT((s) => ({ ...s, x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) }));
  };
  const onUp = () => { drag.current = null; };

  const decide = async (pid: string, e: ProjectEdge, d: "confirm" | "reject") => {
    try { setDetail(await api.decideEdge(pid, e.id, d)); await load(); } catch (x) { setErr(String((x as Error).message ?? x)); }
  };
  const patch = async (pid: string, p: { status?: string; visibility?: "org" | "private" }) => {
    try { setDetail(await api.patchProject(pid, p)); await load(); } catch (x) { setErr(String((x as Error).message ?? x)); }
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
            Projects
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--accent-soft)", color: "var(--accent)" }}>org tree</span>
            {tree && <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{tree.nodes.length} problem{tree.nodes.length === 1 ? "" : "s"} · {tree.edges.length} relation{tree.edges.length === 1 ? "" : "s"} · {owners.length} researcher{owners.length === 1 ? "" : "s"}</span>}
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
            Every shared problem, how it relates to the others, and what each one used. Click a node for its statement, outcomes and harness; start a subproblem from it; confirm or reject relations.
          </div>
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="filter by title, keyword, owner…" style={{ width: 240, padding: "8px 11px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12.5, outline: "none" }} />
        <button onClick={() => { setOnboardingBriefId(null); setMainView("onboarding"); }} style={{ padding: "8px 14px", borderRadius: 9, fontSize: 12.5, fontWeight: 700, background: "var(--accent)", color: "#06121c" }}>＋ New problem</button>
      </div>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div ref={wrap} onWheel={onWheel} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp} style={{ flex: 1, position: "relative", overflow: "hidden", background: "var(--bg0)", cursor: drag.current ? "grabbing" : "grab", userSelect: "none" }}>
          {!tree ? (
            <div style={{ padding: 40, color: "var(--lo)", fontSize: 13 }}>Loading the tree…</div>
          ) : nodes.length === 0 ? (
            <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--lo)", textAlign: "center", padding: 40, gap: 8 }}>
              <div style={{ fontSize: 26, opacity: 0.5 }}>⬡</div>
              <div style={{ fontSize: 14, color: "var(--mid)", fontWeight: 600 }}>{query ? "No problem matches the filter." : "No problems on the tree yet."}</div>
              {!query && <div style={{ fontSize: 12.5, maxWidth: 420, lineHeight: 1.5 }}>A problem appears here when a researcher answers questions 1–3 in onboarding and registers it (or launches a complete brief). Yours can stay private.</div>}
            </div>
          ) : (
            <div style={{ position: "absolute", left: 0, top: 0, transform: `translate(${t.x}px,${t.y}px) scale(${t.k})`, transformOrigin: "0 0" }}>
              <svg width={width} height={height} style={{ display: "block" }}>
                {domains.map((d, c) => (
                  <g key={d}>
                    <rect x={PADX + c * COL_W + 8} y={PADY - 44} width={COL_W - 16} height={height - PADY + 20} rx={14} fill="var(--bg1)" stroke="var(--border)" />
                    <text x={PADX + c * COL_W + COL_W / 2} y={PADY - 22} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--lo)" style={{ fontFamily: "var(--ui)", letterSpacing: ".08em", textTransform: "uppercase" }}>{d}</text>
                  </g>
                ))}
                {edges.map((e) => {
                  const a = pos.get(e.src)!, b = pos.get(e.dst)!;
                  const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
                  const bend = a.col === b.col ? 60 : 0;
                  return <path key={e.id} d={`M${a.x},${a.y} Q${mx + bend},${my} ${b.x},${b.y}`} fill="none" stroke={EDGE_COLOR[e.type]} strokeWidth={e.status === "confirmed" ? 2.5 : 1.5} strokeDasharray={e.status === "proposed" ? "5 4" : undefined} opacity={0.6} />;
                })}
                {nodes.map((n) => {
                  const p = pos.get(n.id)!;
                  const sh = STATUS_SHAPE[n.status] ?? STATUS_SHAPE.registered;
                  const on = sel === n.id;
                  const mine = n.owner.user_id === user?.user_id;
                  return (
                    <g key={n.id} onMouseDown={(e) => e.stopPropagation()} onClick={() => setSel(on ? null : n.id)} style={{ cursor: "pointer" }} opacity={sh.opacity}>
                      {on && <circle cx={p.x} cy={p.y} r={sh.r + 7} fill="none" stroke="var(--hi)" strokeWidth={1.5} opacity={0.5} />}
                      <circle cx={p.x} cy={p.y} r={sh.r} fill={n.status === "solved" ? "var(--bg0)" : ownerColor(n.owner.user_id)} stroke={ownerColor(n.owner.user_id)} strokeWidth={2.5} strokeDasharray={sh.dash} />
                      {n.visibility === "private" && <text x={p.x + sh.r + 4} y={p.y - sh.r} fontSize={10} fill="var(--lo)">🔒</text>}
                      {n.active_version?.imported_from && <text x={p.x - sh.r - 12} y={p.y - sh.r} fontSize={10} fill="var(--evo)">⇩</text>}
                      <foreignObject x={p.x - COL_W / 2 + 14} y={p.y + sh.r + 4} width={COL_W - 28} height={ROW_H - sh.r - 10} style={{ pointerEvents: "none" }}>
                        <div style={{ textAlign: "center" }}>
                          <div style={{ fontSize: 11.5, color: on ? "var(--hi)" : "var(--mid)", lineHeight: 1.25, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", fontWeight: on ? 600 : 500 }}>{n.title || "(untitled)"}</div>
                          <div style={{ fontFamily: "var(--mono)", fontSize: 9.5, color: "var(--lo)", marginTop: 1 }}>{mine ? "you" : n.owner.display_name} · {n.status}{n.session_count ? ` · ${n.session_count} run${n.session_count === 1 ? "" : "s"}` : ""}</div>
                        </div>
                      </foreignObject>
                    </g>
                  );
                })}
              </svg>
            </div>
          )}
          <div style={{ position: "absolute", left: 12, bottom: 12, display: "flex", gap: 12, padding: "6px 11px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 9, fontSize: 10.5, color: "var(--mid)", flexWrap: "wrap" }}>
            {owners.map((o) => <span key={o} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><span style={{ width: 9, height: 9, borderRadius: "50%", background: ownerColor(o) }} />{tree?.nodes.find((n) => n.owner.user_id === o)?.owner.display_name ?? o}</span>)}
            <span style={{ color: "var(--lo)" }}>· hollow = solved · dashed = abandoned · ⇩ imported harness · 🔒 private · dashed edge = proposed</span>
          </div>
          <div style={{ position: "absolute", right: 12, bottom: 12, display: "flex", flexDirection: "column", gap: 4 }}>
            {([["+", () => setT((s) => ({ ...s, k: clamp(s.k * 1.2, 0.4, 2.4) }))], ["−", () => setT((s) => ({ ...s, k: clamp(s.k / 1.2, 0.4, 2.4) }))], ["⤢", () => setT({ x: 0, y: 0, k: 1 })]] as [string, () => void][]).map(([l, f], i) => (
              <button key={i} onClick={f} style={{ width: 30, height: 30, borderRadius: 8, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--mid)", fontSize: 15, fontWeight: 700 }}>{l}</button>
            ))}
          </div>
        </div>

        {/* detail panel */}
        <div style={{ width: 400, flex: "0 0 400px", borderLeft: "1px solid var(--border)", background: "var(--bg1)", overflowY: "auto", minHeight: 0 }}>
          {!sel ? (
            <div style={{ padding: 24, fontSize: 12.5, color: "var(--lo)", lineHeight: 1.55 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--mid)", marginBottom: 6 }}>Pick a problem</div>
              Columns are domains. Colour = researcher. A node's relations are drawn to its neighbours; dashed relations were proposed by keyword overlap or the advisor and await confirmation from either owner.
              {(tree?.nodes ?? []).length > 0 && (
                <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 4 }}>
                  {(tree?.nodes ?? []).slice(0, 30).map((n) => (
                    <button key={n.id} onClick={() => setSel(n.id)} style={{ textAlign: "left", padding: "7px 9px", borderRadius: 8, background: "var(--bg2)", display: "flex", gap: 8, alignItems: "center" }}>
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: ownerColor(n.owner.user_id), flex: "0 0 auto" }} />
                      <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.title || "(untitled)"}</span>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{n.status}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : !detail ? (
            <div style={{ padding: 24, fontSize: 12.5, color: "var(--lo)" }}>Loading…</div>
          ) : (
            <NodeDetail d={detail} me={user?.user_id ?? ""} err={err} onDecide={decide} onPatch={patch} onSpawn={() => void startBriefFromNode(detail.id)} onOpenSession={(sid) => select(sid)} onOpenBrief={detail.source ? () => { setOnboardingBriefId(detail.source!.brief_id); setMainView("onboarding"); } : undefined} onFocus={(pid) => { openProject(pid); setSel(pid); }} onClose={() => setSel(null)} />
          )}
        </div>
      </div>
    </div>
  );
}

function Q({ n, label, children }: { n: number; label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 10.5, fontWeight: 700, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".06em" }}>
        <span style={{ fontFamily: "var(--mono)", padding: "0 5px", borderRadius: 4, background: "var(--bg3)", color: "var(--mid)", textTransform: "none", letterSpacing: 0 }}>Q{n}</span>{label}
      </div>
      <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>{children}</div>
    </div>
  );
}
const P = ({ s }: { s: string }) => <>{s.trim() ? s : <span style={{ color: "var(--lo)" }}>—</span>}</>;

function NodeDetail({ d, me, err, onDecide, onPatch, onSpawn, onOpenSession, onOpenBrief, onFocus, onClose }: {
  d: ProjectNodeView; me: string; err: string | null;
  onDecide: (pid: string, e: ProjectEdge, x: "confirm" | "reject") => Promise<void>;
  onPatch: (pid: string, p: { status?: string; visibility?: "org" | "private" }) => Promise<void>;
  onSpawn: () => void; onOpenSession: (sid: string) => void; onOpenBrief?: () => void; onFocus: (pid: string) => void; onClose: () => void;
}) {
  const st = d.statement;
  const mine = d.owner.user_id === me;
  const [showAll, setShowAll] = useState(false);
  return (
    <div style={{ padding: "16px 18px 24px" }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14.5, fontWeight: 600, color: "var(--hi)", lineHeight: 1.35 }}>{d.title || "(untitled)"}</div>
          <div style={{ marginTop: 4, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{mine ? "you" : d.owner.display_name} · {d.domain || "no domain"} · {d.visibility} · {d.id}</div>
        </div>
        <button onClick={onClose} style={{ color: "var(--lo)", fontSize: 16, lineHeight: 1 }}>×</button>
      </div>
      <div style={{ marginTop: 9, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
        {mine ? (
          <select value={d.status} onChange={(e) => void onPatch(d.id, { status: e.target.value })} style={{ padding: "4px 8px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 7, color: "var(--hi)", fontSize: 11.5 }}>
            {["registered", "active", "solved", "abandoned", "superseded"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        ) : (
          <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, padding: "2px 7px", borderRadius: 5, background: "var(--bg3)", color: "var(--mid)" }}>{d.status}</span>
        )}
        {mine && (
          <button onClick={() => void onPatch(d.id, { visibility: d.visibility === "org" ? "private" : "org" })} style={{ fontSize: 11, color: "var(--mid)", padding: "3px 8px", border: "1px solid var(--border)", borderRadius: 7 }}>{d.visibility === "org" ? "make private" : "share with org"}</button>
        )}
        {d.keywords.map((k) => <span key={k} style={{ fontSize: 10.5, padding: "2px 7px", borderRadius: 6, background: "var(--accent-soft)", color: "var(--accent)" }}>{k}</span>)}
      </div>
      <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
        <button onClick={onSpawn} style={{ flex: 1, padding: "8px 10px", borderRadius: 8, background: "var(--accent)", color: "#06121c", fontWeight: 700, fontSize: 12 }}>Start a subproblem here →</button>
        {mine && onOpenBrief && d.session_count === 0 && <button onClick={onOpenBrief} style={{ padding: "8px 10px", borderRadius: 8, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--mid)", fontWeight: 600, fontSize: 12 }}>Open my brief</button>}
      </div>
      {err && <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--err)" }}>{err}</div>}

      <Q n={0} label="Definition"><b style={{ fontWeight: 600 }}>{st.research_question || "—"}</b>{st.objectives.length > 0 && <div style={{ marginTop: 3, color: "var(--mid)" }}>{st.objectives.map((o, i) => <div key={i}>{i + 1}. {o}</div>)}</div>}</Q>
      <Q n={1} label="Why it matters"><P s={st.significance} /></Q>
      <Q n={2} label="Prior work · open gap"><P s={st.prior_work} />{st.open_gap && <div style={{ marginTop: 4 }}><span style={{ color: "var(--lo)" }}>Open: </span>{st.open_gap}</div>}</Q>
      <Q n={3} label="Evaluation protocol"><P s={st.evaluation_protocol} />{st.success_criteria && <div style={{ marginTop: 4 }}><span style={{ color: "var(--lo)" }}>Done when: </span>{st.success_criteria}</div>}</Q>
      {showAll ? (
        <Q n={4} label="Data · task · results · access">
          {st.data.length > 0 && <div>{st.data.map((x) => <div key={x.path}><code style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{x.path}</code>{x.description ? ` — ${x.description}` : ""}</div>)}</div>}
          {st.task_definition && <div style={{ marginTop: 3 }}><span style={{ color: "var(--lo)" }}>Task: </span>{st.task_definition}</div>}
          {st.existing_results && <div style={{ marginTop: 3 }}><span style={{ color: "var(--lo)" }}>Existing results: </span>{st.existing_results}</div>}
          {st.data_access && <div style={{ marginTop: 3 }}><span style={{ color: "var(--lo)" }}>Access: </span>{st.data_access}</div>}
          {!st.data.length && !st.task_definition && !st.existing_results && !st.data_access && <span style={{ color: "var(--lo)" }}>—</span>}
        </Q>
      ) : (
        <button onClick={() => setShowAll(true)} style={{ marginTop: 8, fontSize: 11.5, color: "var(--accent)", fontWeight: 600 }}>show data / task / results (Q4) →</button>
      )}

      <div style={{ marginTop: 14, fontSize: 10.5, fontWeight: 700, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".06em" }}>What it used</div>
      <div style={{ marginTop: 5, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.55 }}>
        <div><span style={{ color: "var(--lo)" }}>Sessions:</span> {d.links.sessions.length ? d.links.sessions.map((s) => <button key={s} onClick={() => mine && onOpenSession(s)} title={mine ? "open" : "another researcher's session (only its outcome is shared)"} style={{ fontFamily: "var(--mono)", fontSize: 11, color: mine ? "var(--accent)" : "var(--mid)", marginRight: 6, cursor: mine ? "pointer" : "default" }}>{s}</button>) : <span style={{ color: "var(--lo)" }}>none yet</span>}</div>
        {d.links.outcomes.map((o) => (
          <div key={o.session_id} style={{ marginTop: 5, padding: "7px 9px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8 }}>
            <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{o.session_id} · {o.ts}{o.version ? ` · on ${o.version}` : ""}</div>
            <div style={{ marginTop: 2 }}>{o.summary || "(no summary yet)"}</div>
            {o.results.length > 0 && <div style={{ marginTop: 2, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--mid)" }}>{o.results.join(" · ")}</div>}
          </div>
        ))}
        <div style={{ marginTop: 5 }}><span style={{ color: "var(--lo)" }}>Harness:</span> {d.active_version ? `${d.active_version.summary ?? d.active_version.id}${d.active_version.imported_from ? ` (imported from ${d.active_version.imported_from.owner_name ?? "a colleague"})` : ""}` : "base harness"}{d.version_count ? ` · ${d.version_count} version${d.version_count === 1 ? "" : "s"}` : ""}</div>
        {d.links.harness_versions.length > 0 && <div style={{ marginTop: 2, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--mid)" }}>{d.links.harness_versions.map((v) => `${v.id}${v.active ? " (active)" : ""}`).join(" · ")}</div>}
        <div style={{ marginTop: 5 }}><span style={{ color: "var(--lo)" }}>Evolved tools:</span> {d.custom_tools.length ? d.custom_tools.map((t) => <code key={t} style={{ fontFamily: "var(--mono)", fontSize: 11, marginRight: 6 }}>{t}</code>) : <span style={{ color: "var(--lo)" }}>none (base tools only)</span>}</div>
        <div style={{ marginTop: 3 }}><span style={{ color: "var(--lo)" }}>Datasets:</span> {d.links.datasets.length ? d.links.datasets.map((x) => x.path).join(", ") : <span style={{ color: "var(--lo)" }}>none attached</span>}</div>
      </div>

      <div style={{ marginTop: 14, fontSize: 10.5, fontWeight: 700, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".06em" }}>Relations ({d.edges.length})</div>
      <div style={{ marginTop: 5, display: "flex", flexDirection: "column", gap: 5 }}>
        {d.edges.length === 0 && <div style={{ fontSize: 12, color: "var(--lo)" }}>None yet.</div>}
        {d.edges.map((e) => {
          const otherId = e.src === d.id ? e.dst : e.src;
          const other = d.neighbours.find((x) => x.node.id === otherId)?.node;
          const canDecide = e.status === "proposed" && (mine || other?.owner.user_id === me);
          return (
            <div key={e.id} style={{ padding: "7px 9px", background: "var(--bg2)", border: "1px solid var(--border)", borderLeft: `3px solid ${EDGE_COLOR[e.type]}`, borderRadius: 8, fontSize: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <button onClick={() => other && onFocus(otherId)} style={{ color: "var(--hi)", fontWeight: 600, textAlign: "left" }}>{other?.title ?? otherId}</button>
                <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{e.type.replace("_", " ")} · {e.source} · {e.status} · w={e.weight}</span>
              </div>
              {e.rationale && <div style={{ marginTop: 2, color: "var(--mid)" }}>{e.rationale}</div>}
              {canDecide && (
                <div style={{ marginTop: 5, display: "flex", gap: 6 }}>
                  <button onClick={() => void onDecide(d.id, e, "confirm")} style={{ padding: "3px 9px", borderRadius: 6, background: "var(--ok)", color: "#04160c", fontSize: 11, fontWeight: 700 }}>Confirm</button>
                  <button onClick={() => void onDecide(d.id, e, "reject")} style={{ padding: "3px 9px", borderRadius: 6, background: "var(--err-soft)", border: "1px solid var(--err)", color: "var(--err)", fontSize: 11, fontWeight: 700 }}>Reject</button>
                </div>
              )}
            </div>
          );
        })}
      </div>
      {mine && d.recommendation && (
        <div style={{ marginTop: 12, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>advisor: {d.recommendation.status}{d.recommendation.served_by ? ` · ${d.recommendation.served_by}` : ""} · {d.recommendation.harness_import ? "harness import recommended" : "no harness import"} · {d.recommendation.tool_imports} tool{d.recommendation.tool_imports === 1 ? "" : "s"}</div>
      )}
    </div>
  );
}
