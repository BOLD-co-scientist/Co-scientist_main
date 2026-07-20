import { useState } from "react";
import EvolutionGraph from "./EvolutionGraph";
import ActivityLog from "./ActivityLog";

export default function EvolutionView() {
  const [tab, setTab] = useState<"graph" | "log">("graph");
  const seg = (on: boolean) =>
    ({ padding: "6px 13px", borderRadius: 7, fontSize: 12.5, fontWeight: 600, color: on ? "var(--hi)" : "var(--mid)", background: on ? "var(--bg0)" : "transparent" }) as const;

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
            Evolution
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--evo-soft)", color: "var(--evo)" }}>self-modify</span>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
            How the system has rewritten its own capabilities, from your harness repo. Each node is a commit; every self-modification opens a branch and merges into main through the same approval gate.
          </div>
        </div>
        <div style={{ display: "flex", gap: 3, padding: 3, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 10, flex: "0 0 auto" }}>
          <button style={seg(tab === "graph")} onClick={() => setTab("graph")}>
            Graph
          </button>
          <button style={seg(tab === "log")} onClick={() => setTab("log")}>
            Activity log
          </button>
        </div>
      </div>
      {tab === "graph" ? <EvolutionGraph /> : <ActivityLog />}
    </div>
  );
}
