import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, SVGProps } from "react";
import { HYP_CHILDREN, SEED_HYPS } from "../lib/mock";
import type { Hyp, HypDir, HypStatus } from "../lib/types";

const DIR: Record<string, { color: string; label: string }> = {
  triage: { color: "var(--accent)", label: "SELECTIVITY" },
  offtarget: { color: "var(--cyan)", label: "OFF-TARGET" },
  redesign: { color: "var(--lav)", label: "MED-CHEM" },
  profile: { color: "var(--grn)", label: "PROFILING" },
  synergy: { color: "var(--evo)", label: "PHARMACOLOGY" },
};

const STt: Record<HypStatus, { tag: string; tagColor: string; border: string; bg: string; title: string; strike: boolean }> = {
  root: { tag: "SEED", tagColor: "var(--mid)", border: "1.5px solid var(--border-hi)", bg: "var(--bg2)", title: "var(--hi)", strike: false },
  path: { tag: "PURSUED", tagColor: "var(--accent)", border: "1.5px solid var(--accent)", bg: "var(--accent-soft)", title: "var(--hi)", strike: false },
  supported: { tag: "SUPPORTED", tagColor: "var(--ok)", border: "1.5px solid var(--ok)", bg: "var(--ok-soft)", title: "var(--hi)", strike: false },
  refuted: { tag: "REFUTED", tagColor: "var(--err)", border: "1px dashed var(--err)", bg: "var(--err-soft)", title: "var(--mid)", strike: true },
  parked: { tag: "PARKED", tagColor: "var(--lo)", border: "1px dashed var(--border-hi)", bg: "transparent", title: "var(--mid)", strike: false },
  proposed: { tag: "PROPOSED", tagColor: "var(--accent)", border: "1.5px dashed var(--accent)", bg: "var(--bg1)", title: "var(--hi)", strike: false },
};

const BW = 200, BH = 62, SLOT = 82, DX = 250, X0 = 34, Y0 = 34;

function layout(hyps: Hyp[]) {
  const kids: Record<string, string[]> = {};
  hyps.forEach((h) => {
    if (h.parent) (kids[h.parent] ||= []).push(h.id);
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
  hyps.filter((h) => !h.parent).forEach((r) => walk(r.id, 0));
  return { pos, W: X0 + maxD * DX + BW + 40, H: Math.max(1, leaf) * SLOT + Y0 * 2 };
}

function edgeAttrs(s: HypStatus): SVGProps<SVGPathElement> {
  switch (s) {
    case "root":
    case "path":
      return { stroke: "var(--accent)", strokeWidth: 2.4, opacity: 0.85 };
    case "supported":
      return { stroke: "var(--ok)", strokeWidth: 1.8, opacity: 0.6 };
    case "refuted":
      return { stroke: "var(--err)", strokeWidth: 1.4, opacity: 0.32, strokeDasharray: "2 5" };
    case "parked":
      return { stroke: "var(--border-hi)", strokeWidth: 1.4, opacity: 0.55, strokeDasharray: "4 5" };
    default:
      return { stroke: "var(--accent)", strokeWidth: 2, opacity: 0.5, strokeDasharray: "5 6" };
  }
}

const maxNum = (hyps: Hyp[]) => hyps.reduce((m, h) => Math.max(m, parseInt(h.id.slice(1)) || 0), 0);

export default function HypothesisTree() {
  const [hyps, setHyps] = useState<Hyp[]>(() => JSON.parse(JSON.stringify(SEED_HYPS)));
  const [sel, setSel] = useState<string | null>(null);
  const [scale, setScale] = useState(0.72);
  const wrapRef = useRef<HTMLDivElement>(null);

  const TL = useMemo(() => layout(hyps), [hyps]);

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const fit = () => setScale(Math.max(0.5, Math.min(1, (el.clientWidth - 32) / TL.W)));
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [TL.W]);

  const addChildren = (list: Hyp[], parentId: string): Hyp[] => {
    if (list.some((x) => x.parent === parentId)) return list;
    const par = list.find((x) => x.id === parentId);
    const tpl = HYP_CHILDREN[parentId] ?? [
      { dir: par?.dir ?? "triage", title: "Design an experiment to test this" },
      { dir: par?.dir ?? "triage", title: "Search prior literature for precedent" },
    ];
    let base = maxNum(list);
    const kids = tpl.map((t) => {
      base++;
      return {
        id: "n" + base,
        parent: parentId,
        dir: (t.dir ?? par?.dir ?? "triage") as HypDir,
        status: "proposed" as HypStatus,
        title: t.title,
        detail: "Proposed next step — pursue it to commit the system to this direction.",
      };
    });
    return [...list, ...kids];
  };

  const pursue = (id: string) => {
    setHyps((prev) => {
      const h = prev.find((x) => x.id === id);
      if (!h || h.status !== "proposed") return prev;
      let next = prev.map((x) =>
        x.parent === h.parent && x.id !== id && x.status === "proposed" ? { ...x, status: "parked" as HypStatus } : x,
      );
      next = next.map((x) => (x.id === id ? { ...x, status: "path" as HypStatus } : x));
      return addChildren(next, id);
    });
    setSel(id);
  };
  const revisit = (id: string) => {
    setHyps((prev) => prev.map((x) => (x.id === id && x.status === "parked" ? { ...x, status: "proposed" as HypStatus } : x)));
    setSel(id);
  };
  const exploreFurther = (id: string) => {
    setHyps((prev) => addChildren(prev, id));
    setSel(id);
  };

  const selected = sel ? hyps.find((h) => h.id === sel) ?? null : null;
  const counts = { open: 0, pursued: 0, resolved: 0, parked: 0 };
  hyps.forEach((h) => {
    if (h.status === "proposed") counts.open++;
    else if (h.status === "path" || h.status === "root") counts.pursued++;
    else if (h.status === "supported" || h.status === "refuted") counts.resolved++;
    else if (h.status === "parked") counts.parked++;
  });

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      {/* canvas */}
      <div ref={wrapRef} style={{ flex: 1, overflow: "auto", position: "relative", background: "var(--bg0)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: 16 }}>
        <div style={{ width: TL.W * scale, height: TL.H * scale, position: "relative", flex: "0 0 auto" }}>
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: TL.W,
              height: TL.H,
              transform: `scale(${scale})`,
              transformOrigin: "0 0",
              backgroundImage: "radial-gradient(var(--border) 1px, transparent 1px)",
              backgroundSize: "22px 22px",
            }}
          >
            <svg viewBox={`0 0 ${TL.W} ${TL.H}`} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", overflow: "visible" }}>
              {hyps.map((h) => {
                if (!h.parent) return null;
                const p = TL.pos[h.parent];
                const c = TL.pos[h.id];
                const x1 = p.x + BW, y1 = p.cy, x2 = c.x, y2 = c.cy, mx = (x1 + x2) / 2;
                return <path key={h.id} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" strokeLinecap="round" {...edgeAttrs(h.status)} />;
              })}
            </svg>
            {hyps.map((h) => {
              const p = TL.pos[h.id];
              const t = STt[h.status];
              const on = sel === h.id;
              const dm = h.dir ? DIR[h.dir] : null;
              const ring = on ? "0 0 0 3px var(--accent-dim)" : h.status === "proposed" ? "0 0 16px -5px var(--accent)" : "none";
              return (
                <div key={h.id}>
                  {h.status === "proposed" && (
                    <span style={{ position: "absolute", left: p.x - 5, top: p.cy - BH / 2 - 5, width: BW + 10, height: BH + 10, borderRadius: 14, border: "1px solid var(--accent)", opacity: 0.3, animation: "pulse 1.9s infinite", zIndex: 1, pointerEvents: "none" }} />
                  )}
                  <div
                    onClick={() => setSel(h.id)}
                    style={{ position: "absolute", left: p.x, top: p.cy - BH / 2, width: BW, height: BH, boxSizing: "border-box", padding: "8px 11px", border: t.border, background: t.bg, borderRadius: 11, cursor: "pointer", display: "flex", flexDirection: "column", gap: 3, zIndex: 2, boxShadow: ring, transition: "box-shadow .15s" }}
                  >
                    <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={{ fontFamily: "var(--mono)", fontSize: 8.5, fontWeight: 700, letterSpacing: ".08em", color: t.tagColor }}>{t.tag}</span>
                      <span style={{ flex: 1 }} />
                      {dm && <span style={{ fontFamily: "var(--mono)", fontSize: 8, fontWeight: 600, letterSpacing: ".05em", color: dm.color }}>{dm.label}</span>}
                    </span>
                    <span style={{ fontSize: 11.5, fontWeight: 600, lineHeight: 1.22, color: t.title, textDecoration: t.strike ? "line-through" : "none", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                      {h.title}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* inspector */}
      <div style={{ width: 340, flex: "0 0 340px", borderLeft: "1px solid var(--border)", background: "var(--bg1)", display: "flex", flexDirection: "column", minHeight: 0 }}>
        {selected ? (
          <Inspector hyp={selected} hyps={hyps} onPursue={pursue} onRevisit={revisit} onExplore={exploreFurther} />
        ) : (
          <Overview counts={counts} />
        )}
      </div>
    </div>
  );
}

function Inspector({ hyp, hyps, onPursue, onRevisit, onExplore }: { hyp: Hyp; hyps: Hyp[]; onPursue: (id: string) => void; onRevisit: (id: string) => void; onExplore: (id: string) => void }) {
  const dm = hyp.dir ? DIR[hyp.dir] : null;
  const t = STt[hyp.status];
  const parent = hyp.parent ? hyps.find((h) => h.id === hyp.parent) : null;
  const tagLabel: Record<HypStatus, string> = { root: "Seed question", path: "On the pursued path", supported: "Supported", refuted: "Refuted", parked: "Parked", proposed: "Proposed" };

  let btn: { label: string; style: CSSProperties; onClick: () => void } | null = null;
  let hint = "";
  if (hyp.status === "proposed") {
    const sibs = hyps.filter((x) => x.parent === hyp.parent && x.status === "proposed" && x.id !== hyp.id).length;
    btn = { label: "Pursue this direction \u2192", style: { background: "var(--accent)", color: "#06121c" }, onClick: () => onPursue(hyp.id) };
    hint = sibs ? `Commits the system to this branch and parks ${sibs} sibling ${sibs === 1 ? "proposal" : "proposals"} — revisit them anytime.` : "Commits the system to this branch and generates the next set of hypotheses.";
  } else if (hyp.status === "parked") {
    btn = { label: "Revisit this direction", style: { background: "var(--bg2)", color: "var(--hi)", border: "1px solid var(--border-hi)" }, onClick: () => onRevisit(hyp.id) };
    hint = "Brings this fork back into play as a pursuable proposal.";
  } else {
    btn = { label: "Explore further \u2192", style: { background: "var(--evo)", color: "#14091f" }, onClick: () => onExplore(hyp.id) };
    hint = "Generates the next set of candidate hypotheses branching from here.";
  }

  return (
    <div style={{ padding: "18px 18px 22px", overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {dm && <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, fontWeight: 600, letterSpacing: ".08em", padding: "3px 8px", borderRadius: 6, background: "var(--bg2)", color: dm.color }}>{dm.label}</span>}
        <span style={{ fontSize: 11, fontWeight: 700, color: t.tagColor }}>{tagLabel[hyp.status]}</span>
      </div>
      <div style={{ marginTop: 12, fontSize: 16, fontWeight: 700, color: "var(--hi)", lineHeight: 1.3 }}>{hyp.title}</div>
      <div style={{ marginTop: 9, fontSize: 13, color: "var(--mid)", lineHeight: 1.55 }}>{hyp.detail}</div>
      {hyp.evidence && (
        <div style={{ marginTop: 13, padding: "10px 12px", background: "var(--bg2)", border: "1px solid var(--border)", borderLeft: "2px solid var(--ok)", borderRadius: "0 8px 8px 0" }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>Evidence</div>
          <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.5 }}>{hyp.evidence}</div>
        </div>
      )}
      {parent && (
        <div style={{ marginTop: 14 }}>
          <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>Branches from</div>
          <div style={{ marginTop: 3, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.4 }}>{parent.title}</div>
        </div>
      )}
      <button onClick={btn.onClick} style={{ width: "100%", marginTop: 16, padding: 11, borderRadius: 9, fontWeight: 700, fontSize: 13, ...btn.style }}>
        {btn.label}
      </button>
      <div style={{ marginTop: 9, fontSize: 11, color: "var(--lo)", lineHeight: 1.5 }}>{hint}</div>
    </div>
  );
}

function Overview({ counts }: { counts: { open: number; pursued: number; resolved: number; parked: number } }) {
  const stat = (n: number, label: string, color: string) => (
    <div style={{ flex: 1, padding: "10px 5px", border: "1px solid var(--border)", borderRadius: 10, textAlign: "center", background: "var(--bg2)" }}>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{n}</div>
      <div style={{ fontSize: 9.5, color: "var(--lo)", marginTop: 2 }}>{label}</div>
    </div>
  );
  const legend = (border: string, bg: string, name: string, desc: string) => (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span style={{ width: 26, height: 17, borderRadius: 5, border, background: bg, flex: "0 0 auto" }} />
      <span style={{ fontSize: 12, color: "var(--mid)" }}>
        <b style={{ color: "var(--hi)", fontWeight: 600 }}>{name}</b> — {desc}
      </span>
    </div>
  );
  return (
    <div style={{ padding: 18 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--hi)" }}>Hypothesis explorer</div>
      <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>
        A living map of the research directions this session could take. The system proposes hypotheses; <b style={{ color: "var(--hi)", fontWeight: 600 }}>you pick which branch to pursue</b>. Pursuing one commits the system and reveals the next fork; the roads not taken stay parked and revisitable.
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 15 }}>
        {stat(counts.open, "open", "var(--accent)")}
        {stat(counts.pursued, "pursued", "var(--hi)")}
        {stat(counts.resolved, "resolved", "var(--ok)")}
        {stat(counts.parked, "parked", "var(--lo)")}
      </div>
      <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", margin: "20px 0 11px" }}>States</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {legend("1.5px solid var(--accent)", "var(--accent-soft)", "Pursued", "on the path you chose")}
        {legend("1.5px solid var(--ok)", "var(--ok-soft)", "Supported", "evidence in favor")}
        {legend("1px dashed var(--err)", "var(--err-soft)", "Refuted", "evidence against")}
        {legend("1.5px dashed var(--accent)", "var(--bg1)", "Proposed", "awaiting your pick")}
        {legend("1px dashed var(--border-hi)", "transparent", "Parked", "a fork not taken")}
      </div>
      <div style={{ marginTop: 20, fontSize: 12, color: "var(--lo)", lineHeight: 1.5 }}>
        Click any hypothesis to inspect it. Open a <span style={{ color: "var(--accent)", fontWeight: 600 }}>proposed</span> one and press <span style={{ color: "var(--accent)", fontWeight: 600 }}>Pursue</span> to steer the research down that branch.
      </div>
    </div>
  );
}
