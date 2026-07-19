// Hypotheses page — awaiting-backend stub.
//
// This page is meant to drive the Google "AI co-scientist" protocol: the
// backend generates competing hypotheses for a goal, reviews and ranks them via
// a tournament (Elo), evolves the best, and the human selects from a ranked
// list. That engine is not implemented yet (no POST /hypothesis/* endpoints),
// so rather than show a fake interactive tree we describe the real pipeline and
// mark it not-yet-wired. Contract + plan: docs/plans/H1-hypothesis-coscientist.md.

interface Stage {
  key: string;
  title: string;
  desc: string;
  color: string;
}

const STAGES: Stage[] = [
  { key: "generate", title: "Generate", desc: "Propose N diverse hypotheses from the goal, grounded in literature and tools.", color: "var(--accent)" },
  { key: "review", title: "Reflect", desc: "Review each for correctness, novelty, testability, and safety.", color: "var(--cyan)" },
  { key: "rank", title: "Rank", desc: "Pairwise scientific-debate matches feed an Elo tournament rating.", color: "var(--lav)" },
  { key: "evolve", title: "Evolve", desc: "Combine and sharpen the top hypotheses; re-enter the tournament.", color: "var(--evo)" },
  { key: "select", title: "Select", desc: "You pick from a ranked, reviewed list — steering the research.", color: "var(--ok)" },
];

export default function HypothesisView() {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
          Hypotheses
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--warn-soft)", color: "var(--warn)" }}>awaiting backend</span>
        </div>
        <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
          Select the strongest AI-generated hypothesis via the co-scientist protocol — generate, review, rank, evolve, then you choose.
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "32px 24px", display: "flex", flexDirection: "column", alignItems: "center" }}>
        <div style={{ maxWidth: 760, width: "100%" }}>
          <div style={{ fontSize: 13.5, color: "var(--mid)", lineHeight: 1.6 }}>
            This page will run the <b style={{ color: "var(--hi)", fontWeight: 600 }}>Google AI co-scientist</b> protocol: the system generates competing hypotheses for your research goal, has reviewer agents critique them, ranks them in a pairwise <b style={{ color: "var(--hi)", fontWeight: 600 }}>tournament</b> (Elo), evolves the strongest, and presents you a ranked, reviewed list to choose from. The choice steers the research session.
          </div>

          {/* pipeline */}
          <div style={{ display: "flex", alignItems: "stretch", gap: 8, margin: "28px 0", flexWrap: "wrap" }}>
            {STAGES.map((s, i) => (
              <div key={s.key} style={{ display: "flex", alignItems: "center", gap: 8, flex: "1 1 120px" }}>
                <div style={{ flex: 1, border: `1px solid var(--border)`, borderTop: `2px solid ${s.color}`, borderRadius: 10, padding: "12px 12px 14px", background: "var(--bg2)", minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: s.color, fontWeight: 700 }}>{i + 1}</span>
                    <span style={{ fontSize: 13, fontWeight: 600, color: "var(--hi)" }}>{s.title}</span>
                  </div>
                  <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--mid)", lineHeight: 1.45 }}>{s.desc}</div>
                </div>
                {i < STAGES.length - 1 && <span style={{ color: "var(--lo)", fontSize: 14, flex: "0 0 auto" }}>→</span>}
              </div>
            ))}
          </div>

          <div style={{ padding: "14px 16px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 11 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".06em" }}>Not yet wired</div>
            <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.5 }}>
              The backend engine (<span style={{ fontFamily: "var(--mono)" }}>POST /hypothesis/sessions</span>, tournament ranking, <span style={{ fontFamily: "var(--mono)" }}>hypothesis.*</span> events) isn't implemented yet. The contract and step-by-step plan are pinned in <span style={{ fontFamily: "var(--mono)" }}>docs/plans/H1-hypothesis-coscientist.md</span>; the UI drops in once those endpoints land.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
