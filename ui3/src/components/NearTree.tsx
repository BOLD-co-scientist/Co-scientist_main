import { useCallback, useEffect, useState } from "react";
import { useApp } from "../state/store";
import type { EdgeType, ProjectEdge, ProjectNear, ProjectNodeSummary, ProjectNodeView } from "../lib/types";

// O2: the near-tree — this problem in the centre, its neighbours around it,
// one ring per relation strength. Click a neighbour for its statement,
// outcomes, harness and tools; confirm/reject the relation; declare a new one.
// Pure SVG (no library): a dozen nodes at most.

const W = 420, H = 300, CX = W / 2, CY = H / 2;
const EDGE_COLOR: Record<EdgeType, string> = { adjacent: "var(--accent)", subproblem: "var(--lav)", shares_dataset: "var(--grn)", shares_method: "var(--evo)", supersedes: "var(--warn)" };
const STATUS_COLOR: Record<string, string> = { registered: "var(--mid)", active: "var(--ok)", solved: "var(--accent)", abandoned: "var(--lo)", superseded: "var(--lo)" };

export default function NearTree({ nodeId, refreshKey, onOpenFull }: { nodeId: string; refreshKey: number; onOpenFull: (pid: string) => void }) {
  const { api, user } = useApp();
  const [near, setNear] = useState<ProjectNear | null>(null);
  const [tree, setTree] = useState<ProjectNodeSummary[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectNodeView | null>(null);
  const [declaring, setDeclaring] = useState(false);
  const [declType, setDeclType] = useState<EdgeType>("adjacent");
  const [declDst, setDeclDst] = useState("");
  const [declWhy, setDeclWhy] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [n, t] = await Promise.all([api.projectNear(nodeId), api.projectTree()]);
      setNear(n);
      setTree(t.nodes);
    } catch {
      setNear(null);
    }
  }, [api, nodeId]);
  useEffect(() => { void load(); }, [load, refreshKey]);
  useEffect(() => {
    if (!sel) { setDetail(null); return; }
    let cancelled = false;
    api.getProject(sel).then((d) => { if (!cancelled) setDetail(d); }).catch(() => { if (!cancelled) setDetail(null); });
    return () => { cancelled = true; };
  }, [sel, api]);

  const decide = async (e: ProjectEdge, d: "confirm" | "reject") => {
    try { await api.decideEdge(nodeId, e.id, d); await load(); } catch (x) { setErr(String((x as Error).message ?? x)); }
  };
  const declare = async () => {
    if (!declDst) return;
    try { await api.declareEdge(nodeId, declDst, declType, declWhy); setDeclaring(false); setDeclWhy(""); setDeclDst(""); await load(); } catch (x) { setErr(String((x as Error).message ?? x)); }
  };

  const nbrs = near?.neighbours ?? [];
  const others = tree.filter((n) => n.id !== nodeId && !nbrs.some((x) => x.node.id === n.id));
  const pos = (i: number, n: number, weight: number, status: string) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, n);
    const r = status === "confirmed" ? 84 : 84 + (1 - Math.min(1, weight)) * 46;
    return { x: CX + r * Math.cos(a), y: CY + r * Math.sin(a) };
  };

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg1)", overflow: "hidden" }}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", flex: 1 }}>Near tree</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{nbrs.length} neighbour{nbrs.length === 1 ? "" : "s"} · {tree.length} on the tree</span>
        <button onClick={() => onOpenFull(nodeId)} style={{ fontSize: 11.5, color: "var(--accent)", fontWeight: 600 }}>Open full tree →</button>
      </div>
      {!near ? (
        <div style={{ padding: 20, fontSize: 12.5, color: "var(--lo)" }}>Loading…</div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block", background: "var(--bg0)" }}>
          {nbrs.map((x, i) => {
            const p = pos(i, nbrs.length, x.edge.weight, x.edge.status);
            return <line key={x.edge.id} x1={CX} y1={CY} x2={p.x} y2={p.y} stroke={EDGE_COLOR[x.edge.type]} strokeWidth={x.edge.status === "confirmed" ? 2.5 : 1.5} strokeDasharray={x.edge.status === "proposed" ? "5 4" : undefined} opacity={0.75} />;
          })}
          <circle cx={CX} cy={CY} r={13} fill="var(--accent)" />
          <text x={CX} y={CY + 30} textAnchor="middle" fontSize={11} fill="var(--hi)" style={{ fontFamily: "var(--ui)" }}>{trunc(near.self.title, 34)}</text>
          {nbrs.map((x, i) => {
            const p = pos(i, nbrs.length, x.edge.weight, x.edge.status);
            const on = sel === x.node.id;
            return (
              <g key={x.node.id} onClick={() => setSel(on ? null : x.node.id)} style={{ cursor: "pointer" }}>
                <circle cx={p.x} cy={p.y} r={on ? 12 : 9} fill={STATUS_COLOR[x.node.status] ?? "var(--mid)"} stroke={on ? "var(--hi)" : "var(--bg0)"} strokeWidth={2} />
                <text x={p.x} y={p.y + (p.y > CY ? 24 : -16)} textAnchor="middle" fontSize={10.5} fill={on ? "var(--hi)" : "var(--mid)"} style={{ fontFamily: "var(--ui)" }}>{trunc(x.node.title, 26)}</text>
                <text x={p.x} y={p.y + (p.y > CY ? 36 : -4)} textAnchor="middle" fontSize={9} fill="var(--lo)" style={{ fontFamily: "var(--mono)" }}>{x.node.owner.display_name} · {x.edge.type.replace("_", " ")}</text>
              </g>
            );
          })}
          {nbrs.length === 0 && <text x={CX} y={CY + 56} textAnchor="middle" fontSize={11} fill="var(--lo)" style={{ fontFamily: "var(--ui)" }}>No adjacent problems yet — you are the first in this area.</text>}
        </svg>
      )}
      <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 10, fontSize: 10.5, color: "var(--mid)", alignItems: "center" }}>
        {(["adjacent", "shares_dataset", "shares_method", "subproblem"] as EdgeType[]).map((t) => (
          <span key={t} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span style={{ width: 14, height: 2, background: EDGE_COLOR[t] }} />{t.replace("_", " ")}</span>
        ))}
        <span style={{ color: "var(--lo)" }}>dashed = proposed</span>
        <span style={{ flex: 1 }} />
        <button onClick={() => setDeclaring((d) => !d)} style={{ fontSize: 11.5, color: "var(--accent)", fontWeight: 600 }}>{declaring ? "cancel" : "Declare relation"}</button>
      </div>
      {declaring && (
        <div style={{ padding: "8px 14px 12px", borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 6 }}>
          <select value={declDst} onChange={(e) => setDeclDst(e.target.value)} style={sel_}>
            <option value="">— pick a problem —</option>
            {[...nbrs.map((x) => x.node), ...others].map((n) => <option key={n.id} value={n.id}>{n.title} ({n.owner.display_name})</option>)}
          </select>
          <div style={{ display: "flex", gap: 6 }}>
            <select value={declType} onChange={(e) => setDeclType(e.target.value as EdgeType)} style={{ ...sel_, flex: "0 0 150px" }}>
              {(["adjacent", "subproblem", "shares_dataset", "shares_method", "supersedes"] as EdgeType[]).map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
            </select>
            <input value={declWhy} onChange={(e) => setDeclWhy(e.target.value)} placeholder="why? (one line)" style={{ ...sel_, flex: 1 }} />
            <button onClick={() => void declare()} disabled={!declDst} style={{ padding: "6px 12px", borderRadius: 8, background: "var(--accent)", color: "#06121c", fontWeight: 700, fontSize: 12 }}>Save</button>
          </div>
        </div>
      )}
      {err && <div style={{ padding: "6px 14px", fontSize: 11.5, color: "var(--err)" }}>{err}</div>}
      {sel && (
        <div style={{ borderTop: "1px solid var(--border)", padding: "11px 14px 13px", background: "var(--bg2)" }}>
          {!detail ? (
            <div style={{ fontSize: 12, color: "var(--lo)" }}>Loading…</div>
          ) : (
            <NeighbourDetail d={detail} edge={nbrs.find((x) => x.node.id === sel)?.edge ?? null} me={user?.user_id ?? ""} onDecide={decide} onOpen={() => onOpenFull(detail.id)} />
          )}
        </div>
      )}
    </div>
  );
}

const sel_: React.CSSProperties = { padding: "6px 9px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12, outline: "none" };
const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

function NeighbourDetail({ d, edge, me, onDecide, onOpen }: { d: ProjectNodeView; edge: ProjectEdge | null; me: string; onDecide: (e: ProjectEdge, x: "confirm" | "reject") => Promise<void>; onOpen: () => void }) {
  const st = d.statement;
  const canDecide = edge && edge.status === "proposed";
  return (
    <div style={{ fontSize: 12.5, color: "var(--hi)", lineHeight: 1.5 }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 13.5 }}>{d.title}</div>
          <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)", marginTop: 2 }}>{d.owner.display_name}{d.owner.user_id === me ? " (you)" : ""} · {d.status} · {d.session_count} session{d.session_count === 1 ? "" : "s"} · {d.id}</div>
        </div>
        <button onClick={onOpen} style={{ fontSize: 11.5, color: "var(--accent)", fontWeight: 600, flex: "0 0 auto" }}>open →</button>
      </div>
      <div style={{ marginTop: 7 }}><span style={{ color: "var(--lo)" }}>Asks:</span> {st.research_question || "—"}</div>
      {st.open_gap && <div style={{ marginTop: 4 }}><span style={{ color: "var(--lo)" }}>Open:</span> {trunc(st.open_gap, 260)}</div>}
      {d.outcome && <div style={{ marginTop: 4 }}><span style={{ color: "var(--lo)" }}>Outcome:</span> {d.outcome}</div>}
      {d.active_version && <div style={{ marginTop: 4 }}><span style={{ color: "var(--lo)" }}>Harness:</span> {d.active_version.summary ?? d.active_version.id}{d.active_version.imported_from ? ` (imported from ${d.active_version.imported_from.owner_name ?? "a colleague"})` : ""}</div>}
      {d.custom_tools.length > 0 && <div style={{ marginTop: 4 }}><span style={{ color: "var(--lo)" }}>Evolved tools:</span> {d.custom_tools.map((t) => <code key={t} style={{ fontFamily: "var(--mono)", fontSize: 11, marginRight: 6 }}>{t}</code>)}</div>}
      {edge && (
        <div style={{ marginTop: 8, padding: "7px 10px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 8, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--mid)" }}>{edge.type.replace("_", " ")} · {edge.source} · {edge.status} · w={edge.weight}</span>
          <span style={{ flex: 1, fontSize: 11.5, color: "var(--mid)" }}>{edge.rationale}</span>
          {canDecide && (
            <>
              <button onClick={() => void onDecide(edge, "confirm")} style={{ padding: "3px 9px", borderRadius: 6, background: "var(--ok)", color: "#04160c", fontSize: 11, fontWeight: 700 }}>Confirm</button>
              <button onClick={() => void onDecide(edge, "reject")} style={{ padding: "3px 9px", borderRadius: 6, background: "var(--err-soft)", border: "1px solid var(--err)", color: "var(--err)", fontSize: 11, fontWeight: 700 }}>Reject</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
