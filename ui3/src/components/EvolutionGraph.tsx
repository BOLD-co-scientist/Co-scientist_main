import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useApp } from "../state/store";
import type { CommitDetail, GitCommit } from "../lib/types";

// Real evolution lineage, rendered from the researcher's OWN harness repo
// (GET /git/history, tenant-scoped). Each commit is a node; parent→child edges
// are drawn from git parents; evo/* branch tips read as in-flight, main as
// merged/live. No mock data — a fresh root shows just its bootstrap commit.

const short = (sha: string) => sha.slice(0, 8);

interface Node {
  c: GitCommit;
  row: number;
  col: number;
}
interface Edge {
  fromCol: number;
  fromRow: number;
  toCol: number;
  toRow: number;
}

// Classic git-graph lane assignment, newest-first. `lanes[i]` holds the sha the
// lane is currently waiting to place; a commit takes the lane waiting for it (or
// a fresh one), then hands the lane to its first parent. Extra parents (merges)
// open new lanes; sibling lanes waiting on the same commit collapse into it.
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
    if (col === -1) {
      col = freeLane();
      lanes[col] = c.sha;
    }
    // Any other lane also waiting for this commit collapses into it (a merge
    // point from the child side).
    for (let i = 0; i < lanes.length; i++) {
      if (i !== col && lanes[i] === c.sha) lanes[i] = null;
    }
    nodes.push({ c, row, col });
    maxCol = Math.max(maxCol, col);

    const parents = c.parents.filter((p) => p in rowOf);
    if (parents.length === 0) {
      lanes[col] = null;
    } else {
      lanes[col] = parents[0];
      for (let k = 1; k < parents.length; k++) {
        const pl = freeLane();
        lanes[pl] = parents[k];
      }
    }
  });

  // Edges: from each commit to each of its (visible) parents.
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

const LANE = 26;
const ROW = 58;
const PADX = 26;
const PADY = 26;
const DOT = 6;

function laneColor(i: number): string {
  const palette = ["var(--grn)", "var(--evo)", "var(--cyan)", "var(--accent)", "var(--lav)", "var(--warn)"];
  return palette[i % palette.length];
}

export default function EvolutionGraph() {
  const { git, loadGit } = useApp();
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    loadGit();
  }, [loadGit]);

  const commits = git?.commits ?? [];
  const { nodes, edges, cols } = useMemo(() => layout(commits), [commits]);

  const isEvo = (c: GitCommit) => c.refs.some((r) => r.kind === "evo");
  const isMainTip = (c: GitCommit) => c.refs.some((r) => r.kind === "main");
  const isHead = (c: GitCommit) => c.refs.some((r) => r.kind === "head");
  const nodeColor = (c: GitCommit) =>
    isEvo(c) ? "var(--evo)" : isMainTip(c) ? "var(--grn)" : c.parents.length > 1 ? "var(--accent)" : "var(--mid)";

  const merges = commits.filter((c) => c.parents.length > 1).length;
  const evoTips = commits.filter(isEvo).length;
  const selected = sel ? commits.find((c) => c.sha === sel) ?? null : null;

  const width = PADX * 2 + cols * LANE + 320;
  const height = PADY * 2 + Math.max(1, nodes.length) * ROW;

  const graphEmpty = commits.length === 0 || (commits.length === 1 && commits[0].parents.length === 0 && !isEvo(commits[0]));

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      {/* graph canvas */}
      <div style={{ flex: 1, overflow: "auto", background: "var(--bg0)", position: "relative", minHeight: 0 }}>
        {graphEmpty ? (
          <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--lo)", textAlign: "center", padding: 40 }}>
            <div style={{ fontSize: 26, marginBottom: 12, opacity: 0.5 }}>{"⬢"}</div>
            <div style={{ fontSize: 14, color: "var(--mid)", fontWeight: 600 }}>No self-modifications yet</div>
            <div style={{ marginTop: 6, fontSize: 12.5, maxWidth: 380, lineHeight: 1.5 }}>
              When the system evolves a new capability, it opens a branch in your harness repo. Each evolution and its merge into <span style={{ fontFamily: "var(--mono)" }}>main</span> will appear here as a node in the lineage.
            </div>
          </div>
        ) : (
          <svg width={width} height={height} style={{ display: "block", minWidth: "100%" }}>
            {/* edges */}
            {edges.map((e, i) => {
              const x1 = PADX + e.fromCol * LANE;
              const y1 = PADY + e.fromRow * ROW;
              const x2 = PADX + e.toCol * LANE;
              const y2 = PADY + e.toRow * ROW;
              const my = (y1 + y2) / 2;
              return (
                <path
                  key={i}
                  d={`M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`}
                  fill="none"
                  stroke={laneColor(e.toCol)}
                  strokeWidth={2}
                  opacity={0.55}
                />
              );
            })}
            {/* nodes */}
            {nodes.map((n) => {
              const x = PADX + n.col * LANE;
              const y = PADY + n.row * ROW;
              const on = sel === n.c.sha;
              const color = nodeColor(n.c);
              return (
                <g key={n.c.sha} style={{ cursor: "pointer" }} onClick={() => setSel(n.c.sha)}>
                  {isHead(n.c) && <circle cx={x} cy={y} r={DOT + 4} fill="none" stroke="var(--accent-dim)" strokeWidth={3} />}
                  <circle cx={x} cy={y} r={DOT} fill={isEvo(n.c) || isMainTip(n.c) ? color : "var(--bg2)"} stroke={color} strokeWidth={2} />
                  {/* refs + subject to the right of the last lane */}
                  <foreignObject x={PADX + cols * LANE + 6} y={y - ROW / 2 + 6} width={width - (PADX + cols * LANE + 6) - 12} height={ROW - 8}>
                    <div
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        gap: 2,
                        padding: "4px 8px",
                        borderRadius: 8,
                        background: on ? "var(--bg2)" : "transparent",
                        border: `1px solid ${on ? "var(--border-hi)" : "transparent"}`,
                        overflow: "hidden",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{short(n.c.sha)}</span>
                        {n.c.refs.map((r) => (
                          <span key={r.name} style={refChip(r.kind)}>{r.name}</span>
                        ))}
                      </div>
                      <div style={{ fontSize: 12.5, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {n.c.subject}
                      </div>
                    </div>
                  </foreignObject>
                </g>
              );
            })}
          </svg>
        )}
      </div>

      {/* inspector */}
      <div style={{ width: 320, flex: "0 0 320px", borderLeft: "1px solid var(--border)", background: "var(--bg1)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        {selected ? (
          <CommitInspector c={selected} isEvo={isEvo(selected)} isMain={isMainTip(selected)} />
        ) : (
          <Overview total={commits.length} merges={merges} evoTips={evoTips} />
        )}
      </div>
    </div>
  );
}

function refChip(kind: string): CSSProperties {
  const base = { fontFamily: "var(--mono)", fontSize: 9.5, padding: "1px 6px", borderRadius: 5 } as const;
  if (kind === "evo") return { ...base, background: "var(--evo-soft)", color: "var(--evo)" };
  if (kind === "main") return { ...base, background: "var(--ok-soft)", color: "var(--ok)" };
  if (kind === "head") return { ...base, background: "var(--accent-soft)", color: "var(--accent)" };
  if (kind === "tag") return { ...base, background: "var(--bg3)", color: "var(--mid)" };
  return { ...base, background: "var(--bg3)", color: "var(--mid)" };
}

function CommitInspector({ c, isEvo, isMain }: { c: GitCommit; isEvo: boolean; isMain: boolean }) {
  const { api, refreshSessions, select, setMainView } = useApp();
  const [detail, setDetail] = useState<CommitDetail | null>(null);
  const [prompt, setPrompt] = useState("");
  const [spawning, setSpawning] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // "what is in there" — fetch the commit's changed files on select.
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    api.getCommit(c.sha).then((d) => !cancelled && setDetail(d)).catch(() => {});
    return () => { cancelled = true; };
  }, [c.sha, api]);

  const spawn = async () => {
    const cmd = prompt.trim();
    if (!cmd) return;
    setSpawning(true);
    setNote(null);
    try {
      const { session_id } = await api.spawnEvolution(cmd, c.sha);
      await refreshSessions();
      select(session_id); // switch to the new evolution session to watch it
      setMainView("session");
    } catch {
      setNote("Could not start the evolution. Try again.");
    } finally {
      setSpawning(false);
    }
  };

  const [tag, tagColor] = isEvo
    ? ["In-flight evolution branch", "var(--evo)"]
    : isMain
      ? ["Live on main", "var(--ok)"]
      : c.parents.length > 1
        ? ["Merge — evolution landed", "var(--accent)"]
        : ["Commit", "var(--mid)"];
  return (
    <div style={{ padding: "18px 18px 22px", overflowY: "auto" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: tagColor as string }}>{tag}</div>
      <div style={{ marginTop: 10, fontSize: 15, fontWeight: 600, color: "var(--hi)", lineHeight: 1.35 }}>{c.subject}</div>
      <div style={{ marginTop: 14 }}>
        <Field label="Commit" mono value={short(c.sha)} />
        <Field label="Author" value={c.author} />
        <Field label="When" value={c.ts} />
        {c.parents.length > 0 && <Field label="Parents" mono value={c.parents.map(short).join(", ")} />}
      </div>
      {c.refs.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>Refs</div>
          <div style={{ marginTop: 5, display: "flex", flexWrap: "wrap", gap: 5 }}>
            {c.refs.map((r) => (
              <span key={r.name} style={refChip(r.kind)}>{r.name}</span>
            ))}
          </div>
        </div>
      )}

      {/* what's in this commit */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>
          Files changed{detail ? ` (${detail.files.length})` : ""}
        </div>
        {!detail && <div style={{ marginTop: 5, fontSize: 12, color: "var(--lo)" }}>loading…</div>}
        {detail && detail.files.length === 0 && <div style={{ marginTop: 5, fontSize: 12, color: "var(--lo)" }}>No file changes (e.g. a merge or bootstrap).</div>}
        {detail && detail.files.map((f) => (
          <div key={f.path} style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5, fontSize: 11.5 }}>
            <span style={{ fontFamily: "var(--mono)", flex: 1, minWidth: 0, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.path}>{f.path}</span>
            {f.binary ? (
              <span style={{ fontFamily: "var(--mono)", color: "var(--lo)" }}>bin</span>
            ) : (
              <span style={{ fontFamily: "var(--mono)", flex: "0 0 auto" }}>
                <span style={{ color: "var(--ok)" }}>+{f.additions}</span> <span style={{ color: "var(--err)" }}>-{f.deletions}</span>
              </span>
            )}
          </div>
        ))}
      </div>

      {/* spawn a feature from this node */}
      <div style={{ marginTop: 18, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--evo)" }}>Spawn a feature from this node</div>
        <div style={{ marginTop: 4, fontSize: 11.5, color: "var(--mid)", lineHeight: 1.45 }}>
          The evolution agent branches a new <span style={{ fontFamily: "var(--mono)" }}>evo/*</span> worktree from this commit, attempts your change, and proposes a merge for your approval.
        </div>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder='e.g. "Add a plotting helper tool for the data_analyst"'
          rows={3}
          disabled={spawning}
          style={{ width: "100%", marginTop: 9, resize: "vertical", padding: "9px 11px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 9, color: "var(--hi)", fontSize: 12.5, outline: "none", lineHeight: 1.5 }}
        />
        <button
          onClick={spawn}
          disabled={!prompt.trim() || spawning}
          style={{ width: "100%", marginTop: 9, padding: 10, borderRadius: 9, fontWeight: 700, fontSize: 13, background: prompt.trim() && !spawning ? "var(--evo)" : "var(--bg2)", color: prompt.trim() && !spawning ? "#14091f" : "var(--lo)" }}
        >
          {spawning ? "Starting evolution…" : "Start evolution →"}
        </button>
        {note && <div style={{ marginTop: 7, fontSize: 11.5, color: "var(--err)" }}>{note}</div>}
      </div>
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ marginBottom: 9 }}>
      <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>{label}</div>
      <div style={{ marginTop: 2, fontSize: 12.5, color: "var(--hi)", fontFamily: mono ? "var(--mono)" : undefined, wordBreak: "break-word" }}>{value}</div>
    </div>
  );
}

function Overview({ total, merges, evoTips }: { total: number; merges: number; evoTips: number }) {
  const stat = (n: number, label: string, color: string) => (
    <div style={{ flex: 1, padding: 11, border: "1px solid var(--border)", borderRadius: 10, textAlign: "center", background: "var(--bg2)" }}>
      <div style={{ fontSize: 19, fontWeight: 700, color }}>{n}</div>
      <div style={{ fontSize: 10, color: "var(--lo)", marginTop: 2 }}>{label}</div>
    </div>
  );
  const legend = (color: string, name: string, desc: string) => (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span style={{ width: 12, height: 12, borderRadius: "50%", background: color, flex: "0 0 auto" }} />
      <span style={{ fontSize: 12, color: "var(--mid)" }}>
        <b style={{ color: "var(--hi)", fontWeight: 600 }}>{name}</b> — {desc}
      </span>
    </div>
  );
  return (
    <div style={{ padding: 18 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--hi)" }}>Evolution lineage</div>
      <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>
        The real commit graph of <b style={{ color: "var(--hi)", fontWeight: 600 }}>your</b> harness repo. Every self-modification the system makes opens a branch and merges into <span style={{ fontFamily: "var(--mono)" }}>main</span> through the human approval gate — the parent links show exactly what descended from what.
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 15 }}>
        {stat(total, "commits", "var(--hi)")}
        {stat(merges, "merges", "var(--accent)")}
        {stat(evoTips, "in-flight", "var(--evo)")}
      </div>
      <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", margin: "20px 0 11px" }}>Legend</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {legend("var(--grn)", "main", "live in the running system")}
        {legend("var(--evo)", "evo/*", "in-flight worktree, awaiting merge")}
        {legend("var(--accent)", "merge", "an evolution that landed")}
      </div>
      <div style={{ marginTop: 20, fontSize: 12, color: "var(--lo)", lineHeight: 1.5 }}>
        Click any node to inspect its commit, author, and parents.
      </div>
    </div>
  );
}
