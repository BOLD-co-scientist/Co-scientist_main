import HypothesisTree from "./HypothesisTree";

export default function HypothesisView() {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)" }}>
        <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
          Hypotheses
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--accent-soft)", color: "var(--accent)" }}>you steer</span>
        </div>
        <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
          A branching map of research directions. The system proposes hypotheses at each fork — you decide which to pursue.
        </div>
      </div>
      <HypothesisTree />
    </div>
  );
}
