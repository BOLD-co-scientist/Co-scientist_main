import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, SVGProps } from "react";
import { EVODIR, SKILLS } from "../lib/mock";
import { useApp } from "../state/store";
import type { Skill, SkillStatus } from "../lib/types";

const BW = 200, BH = 62, SLOT = 82, DX = 250, X0 = 34, Y0 = 34;

function layout(items: Skill[]) {
  const kids: Record<string, string[]> = {};
  items.forEach((s) => {
    if (s.parent) (kids[s.parent] ||= []).push(s.id);
  });
  const pos: Record<string, { x: number; cy: number }> = {};
  let leaf = 0, maxD = 0;
  const walk = (id: string, d: number): number => {
    maxD = Math.max(maxD, d);
    const cs = kids[id] ?? [];
    const x = X0 + d * DX;
    let cy: number;
    if (!cs.length) {
      cy = Y0 + leaf * SLOT + BH / 2;
      leaf++;
    } else {
      const ys = cs.map((c) => walk(c, d + 1));
      cy = (Math.min(...ys) + Math.max(...ys)) / 2;
    }
    pos[id] = { x, cy };
    return cy;
  };
  items.filter((s) => !s.parent).forEach((r) => walk(r.id, 0));
  return { pos, W: X0 + maxD * DX + BW + 40, H: Math.max(1, leaf) * SLOT + Y0 * 2 };
}

function edgeAttrs(s: SkillStatus): SVGProps<SVGPathElement> {
  if (s === "merged" || s === "core") return { stroke: "var(--grn)", strokeWidth: 2, opacity: 0.5 };
  if (s === "inflight") return { stroke: "var(--warn)", strokeWidth: 2, opacity: 0.7 };
  return { stroke: "var(--border-hi)", strokeWidth: 1.4, opacity: 0.55, strokeDasharray: "5 6" };
}

const GLYPH: Record<SkillStatus, string> = { core: "\u2b22", merged: "\u2713", inflight: "\u25d0", proposed: "\u25cb" };

export default function EvolutionGraph({ onViewLog }: { onViewLog: () => void }) {
  const { setMainView, setRightTab } = useApp();
  const [skills, setSkills] = useState<Skill[]>(() => JSON.parse(JSON.stringify(SKILLS)));
  const [sel, setSel] = useState<string | null>(null);
  const [scale, setScale] = useState(0.72);
  const wrapRef = useRef<HTMLDivElement>(null);

  const TL = useMemo(() => layout(skills), [skills]);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const fit = () => setScale(Math.max(0.5, Math.min(1, (el.clientWidth - 32) / TL.W)));
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [TL.W]);

  const startEvolution = (id: string) => {
    setSkills((prev) => prev.map((s) => (s.id === id && s.status === "proposed" ? { ...s, status: "inflight" as SkillStatus } : s)));
    setSel(id);
  };

  const selected = sel ? skills.find((s) => s.id === sel) ?? null : null;
  const counts = { merged: 0, inflight: 0, proposed: 0 };
  skills.forEach((s) => {
    if (s.status in counts) (counts as Record<string, number>)[s.status]++;
  });

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      <div ref={wrapRef} style={{ flex: 1, overflow: "auto", position: "relative", background: "var(--bg0)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: 16 }}>
        <div style={{ width: TL.W * scale, height: TL.H * scale, position: "relative", flex: "0 0 auto" }}>
          <div style={{ position: "absolute", top: 0, left: 0, width: TL.W, height: TL.H, transform: `scale(${scale})`, transformOrigin: "0 0", backgroundImage: "radial-gradient(var(--border) 1px, transparent 1px)", backgroundSize: "22px 22px" }}>
            <svg viewBox={`0 0 ${TL.W} ${TL.H}`} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", overflow: "visible" }}>
              {skills.map((s) => {
                if (!s.parent) return null;
                const p = TL.pos[s.parent];
                const c = TL.pos[s.id];
                const x1 = p.x + BW, y1 = p.cy, x2 = c.x, y2 = c.cy, mx = (x1 + x2) / 2;
                return <path key={s.id} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" strokeLinecap="round" {...edgeAttrs(s.status)} />;
              })}
            </svg>
            {skills.map((s) => {
              const p = TL.pos[s.id];
              const dm = s.dir ? EVODIR[s.dir] : null;
              const on = sel === s.id;
              const glyphColor = s.status === "core" ? "var(--grn)" : s.status === "merged" ? (dm ? dm.color : "var(--grn)") : s.status === "inflight" ? "var(--warn)" : "var(--lo)";
              const border =
                s.status === "merged"
                  ? `1.5px solid ${dm ? dm.color : "var(--grn)"}`
                  : s.status === "inflight"
                    ? "1.5px solid var(--warn)"
                    : s.status === "core"
                      ? "1.5px solid var(--border-hi)"
                      : "1px dashed var(--border-hi)";
              const bg = s.status === "proposed" ? "var(--bg1)" : "var(--bg2)";
              const ring = on ? "0 0 0 3px var(--accent-dim)" : s.status === "inflight" ? "0 0 16px -5px var(--warn)" : "none";
              const boxStyle: CSSProperties = {
                position: "absolute", left: p.x, top: p.cy - BH / 2, width: BW, height: BH, boxSizing: "border-box", padding: "8px 11px",
                border, background: bg, borderRadius: 11, cursor: "pointer", display: "flex", flexDirection: "column", gap: 3, zIndex: 2,
                opacity: s.status === "proposed" ? 0.85 : 1, boxShadow: ring, transition: "box-shadow .15s",
              };
              return (
                <div key={s.id}>
                  {s.status === "inflight" && (
                    <span style={{ position: "absolute", left: p.x - 5, top: p.cy - BH / 2 - 5, width: BW + 10, height: BH + 10, borderRadius: 14, border: "1px solid var(--warn)", opacity: 0.3, animation: "pulse 1.9s infinite", zIndex: 1, pointerEvents: "none" }} />
                  )}
                  <div onClick={() => setSel(s.id)} style={boxStyle}>
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: glyphColor }}>{GLYPH[s.status]}</span>
                      <span style={{ flex: 1 }} />
                      {dm && <span style={{ fontFamily: "var(--mono)", fontSize: 8, fontWeight: 600, letterSpacing: ".05em", color: dm.color }}>{dm.label}</span>}
                    </span>
                    <span style={{ fontSize: 11.5, fontWeight: 600, lineHeight: 1.22, color: "var(--hi)", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>{s.title}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div style={{ width: 340, flex: "0 0 340px", borderLeft: "1px solid var(--border)", background: "var(--bg1)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        {selected ? (
          <SkillInspector skill={selected} onViewLog={onViewLog} onReview={() => { setMainView("session"); setRightTab("hitl"); }} onStart={() => startEvolution(selected.id)} />
        ) : (
          <Overview counts={counts} />
        )}
      </div>
    </div>
  );
}

function SkillInspector({ skill, onViewLog, onReview, onStart }: { skill: Skill; onViewLog: () => void; onReview: () => void; onStart: () => void }) {
  const dm = skill.dir ? EVODIR[skill.dir] : null;
  const tag: Record<SkillStatus, [string, string]> = {
    core: ["Core", "var(--grn)"],
    merged: ["Merged into main", dm ? dm.color : "var(--grn)"],
    inflight: ["In-flight worktree", "var(--warn)"],
    proposed: ["Proposed", "var(--lo)"],
  };
  let btn: { label: string; style: CSSProperties; onClick: () => void } | null = null;
  if (skill.status === "merged") btn = { label: "View in activity log \u2197", style: { border: "1px solid var(--border)", color: "var(--mid)", background: "var(--bg2)", fontWeight: 600, fontSize: 12.5 }, onClick: onViewLog };
  else if (skill.status === "inflight") btn = { label: "Review diff & decide \u2192", style: { background: "var(--warn)", color: "#1a1305", fontWeight: 700, fontSize: 13 }, onClick: onReview };
  else if (skill.status === "proposed") btn = { label: "Start evolution \u2192", style: { background: "var(--evo)", color: "#14091f", fontWeight: 700, fontSize: 13 }, onClick: onStart };

  return (
    <div style={{ padding: "18px 18px 22px", overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {dm && <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, fontWeight: 600, letterSpacing: ".08em", padding: "3px 8px", borderRadius: 6, background: "var(--bg2)", color: dm.color }}>{dm.label}</span>}
        <span style={{ fontSize: 11, fontWeight: 700, color: tag[skill.status][1] }}>{tag[skill.status][0]}</span>
      </div>
      <div style={{ marginTop: 12, fontSize: 16, fontWeight: 700, color: "var(--hi)", lineHeight: 1.3 }}>{skill.title}</div>
      <div style={{ marginTop: 9, fontSize: 13, color: "var(--mid)", lineHeight: 1.55 }}>{skill.desc}</div>
      <div style={{ marginTop: 14 }}>
        <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>Contributes</div>
        <div style={{ marginTop: 3, fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--cyan)" }}>{skill.adds}</div>
      </div>
      {skill.sha && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>Commit</div>
          <div style={{ marginTop: 3, fontFamily: "var(--mono)", fontSize: 12.5, color: "var(--accent)" }}>{skill.sha}</div>
        </div>
      )}
      {btn && (
        <button onClick={btn.onClick} style={{ width: "100%", marginTop: 16, padding: 11, borderRadius: 9, ...btn.style }}>
          {btn.label}
        </button>
      )}
    </div>
  );
}

function Overview({ counts }: { counts: { merged: number; inflight: number; proposed: number } }) {
  const stat = (n: number, label: string, color: string) => (
    <div style={{ flex: 1, padding: 11, border: "1px solid var(--border)", borderRadius: 10, textAlign: "center", background: "var(--bg2)" }}>
      <div style={{ fontSize: 19, fontWeight: 700, color }}>{n}</div>
      <div style={{ fontSize: 10, color: "var(--lo)", marginTop: 2 }}>{label}</div>
    </div>
  );
  const legend = (color: string, glyph: string, name: string, desc: string) => (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span style={{ width: 20, height: 20, borderRadius: "50%", border: `${glyph === "\u25cb" ? "1px dashed var(--border-hi)" : "1.5px solid " + color}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, color: glyph === "\u25cb" ? "var(--lo)" : color, flex: "0 0 auto" }}>{glyph}</span>
      <span style={{ fontSize: 12, color: "var(--mid)" }}>
        <b style={{ color: "var(--hi)", fontWeight: 600 }}>{name}</b> — {desc}
      </span>
    </div>
  );
  return (
    <div style={{ padding: 18 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--hi)" }}>Capability graph</div>
      <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>
        The lineage of skills the system has taught itself, rooted at the harness core and branching by research direction. Each generation refines the last; merges into <span style={{ fontFamily: "var(--mono)" }}>main</span> pass through the same human approval gate.
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 15 }}>
        {stat(counts.merged, "in main", "var(--grn)")}
        {stat(counts.inflight, "in-flight", "var(--warn)")}
        {stat(counts.proposed, "proposed", "var(--lo)")}
      </div>
      <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", margin: "20px 0 11px" }}>States</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {legend("var(--grn)", "\u2713", "Merged", "live in the running system")}
        {legend("var(--warn)", "\u25d0", "In-flight", "worktree open, awaiting a merge")}
        {legend("var(--lo)", "\u25cb", "Proposed", "a capability it could grow next")}
      </div>
      <div style={{ marginTop: 20, fontSize: 12, color: "var(--lo)", lineHeight: 1.5 }}>
        Click any skill to inspect it. Open a <span style={{ color: "var(--evo)", fontWeight: 600 }}>proposed</span> one and press <span style={{ color: "var(--evo)", fontWeight: 600 }}>Start evolution</span> to spin up a worktree.
      </div>
    </div>
  );
}
