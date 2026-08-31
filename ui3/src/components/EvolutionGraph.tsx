import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useApp } from "../state/store";
import Markdown from "./Markdown";
import type { GitCommit, Version } from "../lib/types";

// The evolution version DAG as ONE zoomable / pannable canvas (R17 UX model).
// Default view is DECLUTTERED: only switchable version nodes (bootstrap root +
// `ver/` tags + in-flight `evo/*` + active HEAD). Toggle "expand" (button or
// Ctrl+E) to fold in EVERY commit from `/git/history` — the real git graph,
// merges included (DAG-capable; today evolution uses --ff-only so it stays a
// near-linear tree). Only the node DOT is interactive (its label is inert):
// hover it → a preview modal opens beside the node; click → pins it. Drag
// anywhere (including on a dot) pans; a press that doesn't move is a click.

const short = (sha: string) => sha.slice(0, 8);
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

interface Node { c: GitCommit; row: number; col: number; }
interface Edge { fromCol: number; fromRow: number; toCol: number; toRow: number; }

function layout(commits: GitCommit[]): { nodes: Node[]; edges: Edge[]; cols: number } {
  const rowOf: Record<string, number> = {};
  commits.forEach((c, i) => (rowOf[c.sha] = i));
  const lanes: (string | null)[] = [];
  const freeLane = () => {
    const i = lanes.indexOf(null);
    if (i >= 0) return i;
    lanes.push(null);
    return lanes.length - 1;
  };
  const nodes: Node[] = [];
  let maxCol = 0;
  commits.forEach((c, row) => {
    let col = lanes.indexOf(c.sha);
    if (col === -1) { col = freeLane(); lanes[col] = c.sha; }
    for (let i = 0; i < lanes.length; i++) if (i !== col && lanes[i] === c.sha) lanes[i] = null;
    nodes.push({ c, row, col });
    maxCol = Math.max(maxCol, col);
    const parents = c.parents.filter((p) => p in rowOf);
    if (parents.length === 0) { lanes[col] = null; }
    else {
      lanes[col] = parents[0];
      for (let k = 1; k < parents.length; k++) { const pl = freeLane(); lanes[pl] = parents[k]; }
    }
  });
  const nodeByRow = new Map(nodes.map((n) => [n.row, n]));
  const edges: Edge[] = [];
  for (const n of nodes) {
    for (const p of n.c.parents) {
      const pr = rowOf[p];
      if (pr === undefined) continue;
      const pn = nodeByRow.get(pr);
      if (!pn) continue;
      edges.push({ fromCol: n.col, fromRow: n.row, toCol: pn.col, toRow: pn.row });
    }
  }
  return { nodes, edges, cols: maxCol + 1 };
}

function collapse(commits: GitCommit[], keep: Set<string>): GitCommit[] {
  const bySha = new Map(commits.map((c) => [c.sha, c]));
  const nearestKept = (start: string): string[] => {
    const out: string[] = [], seen = new Set<string>(), stack = [start];
    while (stack.length) {
      const sha = stack.pop()!;
      if (seen.has(sha)) continue;
      seen.add(sha);
      if (keep.has(sha)) { out.push(sha); continue; }
      const c = bySha.get(sha);
      if (c) for (const p of c.parents) stack.push(p);
    }
    return out;
  };
  return commits.filter((c) => keep.has(c.sha)).map((c) => ({
    ...c,
    parents: Array.from(new Set(c.parents.flatMap((p) => nearestKept(p)))),
  }));
}

const LANE = 26, ROW = 58, PADX = 26, PADY = 26, DOT = 6, HIT = 13, LABELW = 300;

function laneColor(i: number): string {
  const p = ["var(--grn)", "var(--evo)", "var(--cyan)", "var(--accent)", "var(--lav)", "var(--warn)"];
  return p[i % p.length];
}

type Pop = { sha: string; pinned: boolean };

export default function EvolutionCanvas({ onEvolveFrom, onViewConversation }: {
  onEvolveFrom: (sha: string, label: string) => void;
  onViewConversation: (sid: string, label: string) => void;
}) {
  const { git, loadGit, versions, loadVersions, activateVersion } = useApp();
  const [expanded, setExpanded] = useState(false);
  const [pop, setPop] = useState<Pop | null>(null);
  const [t, setT] = useState({ x: 16, y: 16, k: 1 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: boolean } | null>(null);
  const pressSha = useRef<string | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    loadGit(); loadVersions();
    const id = setInterval(() => { loadGit(); loadVersions(); }, 8000);
    return () => clearInterval(id);
  }, [loadGit, loadVersions]);

  const allCommits = git?.commits ?? [];
  const verBySha = useMemo(() => {
    const m = new Map<string, Version>();
    for (const v of versions?.versions ?? []) m.set(v.sha, v);
    return m;
  }, [versions]);
  const activeSha = versions?.head ?? null;

  const isEvo = useCallback((c: GitCommit) => c.refs.some((r) => r.kind === "evo"), []);
  const isVersion = useCallback((c: GitCommit) => verBySha.has(c.sha), [verBySha]);
  const isActive = useCallback((c: GitCommit) => activeSha != null && c.sha === activeSha, [activeSha]);

  const commits = useMemo(() => {
    if (expanded) return allCommits;
    const keep = new Set<string>();
    for (const c of allCommits) {
      if (isVersion(c) || isEvo(c) || isActive(c) || c.parents.length === 0 || c.parents.length > 1 ||
          c.refs.some((r) => r.kind === "head" || r.kind === "main")) keep.add(c.sha);
    }
    return collapse(allCommits, keep);
  }, [allCommits, expanded, isVersion, isEvo, isActive]);

  const { nodes, edges, cols } = useMemo(() => layout(commits), [commits]);
  const hiddenCount = allCommits.length - commits.length;

  const nodeColor = (c: GitCommit) =>
    isActive(c) ? "var(--accent)" : isVersion(c) ? "var(--grn)" : isEvo(c) ? "var(--evo)"
      : c.parents.length === 0 ? "var(--grn)" // the v0 root is a switchable baseline version
      : c.parents.length > 1 ? "var(--accent)" : "var(--mid)";

  const cancelClose = () => { if (closeTimer.current) { clearTimeout(closeTimer.current); closeTimer.current = null; } };
  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => setPop((p) => (p && p.pinned ? p : null)), 200);
  };
  useEffect(() => () => cancelClose(), []);

  const onWheel = (e: React.WheelEvent) => {
    if (!wrap.current) return;
    e.preventDefault();
    setPop((p) => (p && p.pinned ? p : null));
    const r = wrap.current.getBoundingClientRect();
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    const k2 = clamp(t.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), 0.35, 2.6);
    setT({ k: k2, x: cx - (cx - t.x) * (k2 / t.k), y: cy - (cy - t.y) * (k2 / t.k) });
  };
  const onDown = (e: React.MouseEvent) => { drag.current = { x: e.clientX, y: e.clientY, ox: t.x, oy: t.y, moved: false }; };
  const onMove = (e: React.MouseEvent) => {
    const d = drag.current; if (!d) return;
    if (!d.moved && Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) {
      d.moved = true;
      setPop((p) => (p && p.pinned ? p : null));
    }
    if (d.moved) setT((s) => ({ ...s, x: d.ox + (e.clientX - d.x), y: d.oy + (e.clientY - d.y) }));
  };
  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    const sha = pressSha.current;
    pressSha.current = null;
    if (!d || d.moved) return;
    if (sha) setPop((p) => (p && p.pinned && p.sha === sha ? null : { sha, pinned: true }));
    else setPop(null);
  };
  const zoomBy = (f: number) => setT((s) => ({ ...s, k: clamp(s.k * f, 0.35, 2.6) }));
  const reset = () => setT({ x: 16, y: 16, k: 1 });

  useEffect(() => {
    const h = (ev: KeyboardEvent) => {
      if ((ev.ctrlKey || ev.metaKey) && (ev.key === "e" || ev.key === "E")) {
        ev.preventDefault();
        setExpanded((v) => !v);
        setPop(null);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const nodeEnter = (sha: string) => {
    if (drag.current) return;
    setPop((p) => (p && p.pinned ? p : { sha, pinned: false }));
    cancelClose();
  };

  const popNode = pop ? nodes.find((n) => n.c.sha === pop.sha) ?? null : null;
  // Anchor the modal to the NODE's current screen position (tracks zoom/pan),
  // not the mouse. dot centre in screen space = wrap origin + transformed coords.
  let nodeScreen: { x: number; y: number } | null = null;
  if (popNode && wrap.current) {
    const r = wrap.current.getBoundingClientRect();
    nodeScreen = {
      x: r.left + (PADX + popNode.col * LANE) * t.k + t.x,
      y: r.top + (PADY + popNode.row * ROW) * t.k + t.y,
    };
  }

  return (
    <div
      ref={wrap}
      onWheel={onWheel}
      onMouseDown={onDown}
      onMouseMove={onMove}
      onMouseUp={onUp}
      onMouseLeave={() => { drag.current = null; pressSha.current = null; }}
      style={{ flex: 1, position: "relative", overflow: "hidden", background: "var(--bg0)", cursor: drag.current ? "grabbing" : "grab", minHeight: 0, userSelect: "none" }}
    >
      {allCommits.length === 0 ? (
        <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--lo)", textAlign: "center", padding: 40 }}>
          <div style={{ fontSize: 26, marginBottom: 12, opacity: 0.5 }}>{"⬢"}</div>
          <div style={{ fontSize: 14, color: "var(--mid)", fontWeight: 600 }}>No history yet</div>
        </div>
      ) : (
        <div style={{ position: "absolute", left: 0, top: 0, transform: `translate(${t.x}px,${t.y}px) scale(${t.k})`, transformOrigin: "0 0" }}>
          <svg width={PADX * 2 + cols * LANE + LABELW} height={PADY * 2 + Math.max(1, nodes.length) * ROW} style={{ display: "block" }}>
            {edges.map((e, i) => {
              const x1 = PADX + e.fromCol * LANE, y1 = PADY + e.fromRow * ROW;
              const x2 = PADX + e.toCol * LANE, y2 = PADY + e.toRow * ROW;
              const my = (y1 + y2) / 2;
              return <path key={i} d={`M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`} fill="none" stroke={laneColor(e.toCol)} strokeWidth={2} opacity={0.5} />;
            })}
            {nodes.map((n) => {
              const x = PADX + n.col * LANE, y = PADY + n.row * ROW;
              const on = pop?.sha === n.c.sha;
              const color = nodeColor(n.c);
              // The v0 root is a switchable baseline, so it reads as a version node
              // (large, filled) — not a plain commit.
              const isVer = isActive(n.c) || isVersion(n.c) || isEvo(n.c) || n.c.parents.length === 0;
              // A plain commit reads as a smaller, hollow dot; a version/active/evo
              // node is a larger filled dot.
              const r = isVer ? DOT : DOT - 2;
              const ver = verBySha.get(n.c.sha);
              const label = ver?.summary || n.c.subject;
              return (
                <g key={n.c.sha}>
                  {on && <circle cx={x} cy={y} r={r + 7} fill="none" stroke={color} strokeWidth={1.5} opacity={0.4} />}
                  {isActive(n.c) && <circle cx={x} cy={y} r={DOT + 5} fill="none" stroke="var(--accent)" strokeWidth={3} />}
                  <circle cx={x} cy={y} r={r} fill={isVer ? color : "var(--bg2)"} stroke={color} strokeWidth={2} />
                  {/* only this transparent hit-target on the DOT is interactive */}
                  <circle cx={x} cy={y} r={HIT} fill="transparent" style={{ cursor: "pointer", pointerEvents: "all" }}
                    onMouseDown={() => { pressSha.current = n.c.sha; }}
                    onMouseEnter={() => nodeEnter(n.c.sha)}
                    onMouseLeave={scheduleClose} />
                  {/* the label is inert — it never triggers hover/click/drag */}
                  <foreignObject x={PADX + cols * LANE + 6} y={y - ROW / 2 + 6} width={LABELW - 12} height={ROW - 8} style={{ pointerEvents: "none" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 8px", borderRadius: 8, background: on ? "var(--bg2)" : "transparent", border: `1px solid ${on ? "var(--border-hi)" : "transparent"}`, overflow: "hidden" }}>
                      {isActive(n.c) && <span style={badge("var(--accent)", "#0a0f1c")}>ACTIVE</span>}
                      {isEvo(n.c) && <span style={badge("var(--evo)", "#14091f")}>EVO</span>}
                      <span style={{ fontSize: 12.5, color: on ? "var(--hi)" : isVer ? "var(--mid)" : "var(--lo)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
                    </div>
                  </foreignObject>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      <div style={{ position: "absolute", left: 12, top: 12, display: "flex", gap: 8, alignItems: "center" }}>
        <button onClick={() => { setExpanded((v) => !v); setPop(null); }}
          style={{ padding: "6px 11px", borderRadius: 8, fontSize: 11.5, fontWeight: 600, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--mid)" }}>
          {expanded ? "Collapse to versions" : "Expand all commits"}
        </button>
        {!expanded && hiddenCount > 0 && <span style={{ fontSize: 10.5, color: "var(--lo)" }}>{hiddenCount} commit{hiddenCount > 1 ? "s" : ""} folded</span>}
      </div>

      <div style={{ position: "absolute", right: 12, bottom: 12, display: "flex", flexDirection: "column", gap: 4 }}>
        {([["+", () => zoomBy(1.2), ""], ["−", () => zoomBy(1 / 1.2), ""], ["⤢", reset, "reset view"]] as [string, () => void, string][]).map(([lbl, fn, title], i) => (
          <button key={i} onClick={fn} title={title}
            style={{ width: 30, height: 30, borderRadius: 8, background: "var(--bg2)", border: "1px solid var(--border)", color: "var(--mid)", fontSize: 15, fontWeight: 700, lineHeight: 1 }}>{lbl}</button>
        ))}
      </div>

      <div style={{ position: "absolute", left: 12, bottom: 12, display: "flex", gap: 12, padding: "6px 11px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 9, fontSize: 10.5, color: "var(--mid)" }}>
        {legendDot("var(--accent)", "active")}
        {legendDot("var(--grn)", "version")}
        {legendDot("var(--evo)", "in-flight")}
      </div>

      {popNode && pop && nodeScreen && (
        <NodePopover
          nodeX={nodeScreen.x}
          nodeY={nodeScreen.y}
          c={popNode.c}
          version={verBySha.get(popNode.c.sha) ?? null}
          active={isActive(popNode.c)}
          isEvo={isEvo(popNode.c)}
          onEnter={cancelClose}
          onLeave={scheduleClose}
          onClose={() => setPop(null)}
          onActivate={activateVersion}
          onEvolveFrom={onEvolveFrom}
          onViewConversation={onViewConversation}
        />
      )}
    </div>
  );
}

function badge(bg: string, fg: string): CSSProperties {
  return { flex: "0 0 auto", fontSize: 9, fontWeight: 700, letterSpacing: ".05em", padding: "1px 6px", borderRadius: 5, background: bg, color: fg };
}
function legendDot(color: string, label: string) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 9, height: 9, borderRadius: "50%", background: color }} />{label}
    </span>
  );
}

const POP_W = 320, POP_GAP = 16;

// Modal placed relative to the NODE: right of it by default, vertically centred
// on the node; if its bottom would pass the screen bottom it slides up to sit
// just above it; if there isn't room on the right it flips to the node's left
// (vertical logic unchanged). The horizontal gap guarantees it never covers the
// node dot. Height is measured, so centring + bottom-clamp use the real box.
function NodePopover({
  nodeX, nodeY, c, version, active, isEvo, onEnter, onLeave, onClose, onActivate, onEvolveFrom, onViewConversation,
}: {
  nodeX: number; nodeY: number;
  c: GitCommit; version: Version | null; active: boolean; isEvo: boolean;
  onEnter: () => void; onLeave: () => void; onClose: () => void;
  onActivate: (id: string) => Promise<unknown>;
  onEvolveFrom: (sha: string, label: string) => void;
  onViewConversation: (sid: string, label: string) => void;
}) {
  const box = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [activating, setActivating] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useLayoutEffect(() => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const h = box.current?.offsetHeight ?? 0;
    // horizontal: prefer right; flip to left if the right side overflows.
    let left = nodeX + POP_GAP;
    if (left + POP_W > vw - 8) left = nodeX - POP_GAP - POP_W;
    left = clamp(left, 8, Math.max(8, vw - POP_W - 8));
    // vertical: centre on the node, then pull up if it would cross the bottom.
    let top = nodeY - h / 2;
    if (top + h > vh - 8) top = vh - 8 - h;
    top = Math.max(8, top);
    setPos({ left, top });
  }, [nodeX, nodeY, c.sha, version?.rationale]);

  const switchable = version != null || c.parents.length === 0;
  const kindLabel = active ? "Active — running now"
    : version ? "Switchable version"
    : isEvo ? "In-flight evolution"
    : c.parents.length === 0 ? "Root (v0)"
    : c.parents.length > 1 ? "Merge" : "Commit";

  const activate = async () => {
    if (active) return;
    setActivating(true); setNote(null);
    try { await onActivate(version?.id ?? c.sha); onClose(); }
    catch (e) {
      setNote(e && typeof e === "object" && "status" in e && (e as { status?: number }).status === 409
        ? "Can't switch now — stop the running turn first." : "Switch failed. Try again.");
    } finally { setActivating(false); }
  };

  const maxH = Math.min(window.innerHeight * 0.7, window.innerHeight - 16);
  return (
    <div ref={box}
      style={{ position: "fixed", left: pos?.left ?? -9999, top: pos?.top ?? 0, width: POP_W, maxHeight: maxH, overflowY: "auto", visibility: pos ? "visible" : "hidden", background: "var(--bg1)", border: "1px solid var(--border-hi)", borderRadius: 12, boxShadow: "0 12px 34px rgba(0,0,0,.45)", padding: 14, zIndex: 40 }}
      onMouseEnter={onEnter} onMouseLeave={onLeave}
      onMouseDown={(e) => e.stopPropagation()} onWheel={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 10.5, fontWeight: 700, color: active ? "var(--accent)" : version ? "var(--ok)" : isEvo ? "var(--evo)" : "var(--mid)" }}>{kindLabel}</div>
          <div style={{ marginTop: 5, fontSize: 13.5, fontWeight: 600, color: "var(--hi)", lineHeight: 1.35, wordBreak: "break-word" }}>{version?.summary || c.subject}</div>
        </div>
        <button onClick={onClose} style={{ flex: "0 0 auto", color: "var(--lo)", fontSize: 16, lineHeight: 1, padding: 2 }}>×</button>
      </div>

      <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6, fontSize: 10.5, color: "var(--lo)" }}>
        <span style={{ fontFamily: "var(--mono)" }}>{short(c.sha)}</span>
        <span>· {c.author}</span>
        <span>· {c.ts}</span>
      </div>
      {version?.rationale && (
        <div style={{ marginTop: 8 }}>
          <Markdown text={version.rationale} style={{ fontSize: 11.5, color: "var(--mid)", lineHeight: 1.45 }} />
        </div>
      )}
      {version?.smoke?.ran && (
        <div style={{ marginTop: 6, fontSize: 10.5, color: version.smoke.ok ? "var(--ok)" : "var(--err)" }}>{version.smoke.ok ? "smoke ✓" : "smoke ✗"}</div>
      )}

      {/* Actions only on a switchable node (a version or the v0 root): both
          activating and evolving take a coherent version as their base. A plain
          commit / in-flight branch is inspect-only. */}
      {switchable ? (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 7 }}>
          <button onClick={activate} disabled={active || activating}
            style={{ width: "100%", padding: 8, borderRadius: 8, fontWeight: 700, fontSize: 12, background: active ? "var(--bg3)" : "var(--accent)", color: active ? "var(--lo)" : "#0a0f1c", cursor: active ? "default" : "pointer" }}>
            {active ? "✓ Currently active" : activating ? "Switching…" : "Activate this version"}
          </button>
          <button onClick={() => { onEvolveFrom(c.sha, version?.summary || c.subject); onClose(); }}
            style={{ width: "100%", padding: 8, borderRadius: 8, fontWeight: 700, fontSize: 12, background: "var(--evo-soft)", color: "var(--evo)", border: "1px solid var(--border)" }}>
            Evolve from here →
          </button>
        </div>
      ) : (
        <div style={{ marginTop: 12, fontSize: 10.5, color: "var(--lo)", lineHeight: 1.4 }}>
          Inspect-only — activate or evolve from a version node.
        </div>
      )}
      {/* R17 provenance: open the evolution conversation that produced this version */}
      {version?.origin_session && (
        <button onClick={() => onViewConversation(version.origin_session!, version.summary || short(c.sha))}
          style={{ width: "100%", marginTop: 7, padding: 8, borderRadius: 8, fontWeight: 600, fontSize: 12, background: "var(--bg2)", color: "var(--mid)", border: "1px solid var(--border)" }}>
          View conversation ↗
        </button>
      )}
      {note && <div style={{ marginTop: 7, fontSize: 11, color: "var(--err)" }}>{note}</div>}
    </div>
  );
}
