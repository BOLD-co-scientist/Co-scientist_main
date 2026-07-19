import { useApp } from "../state/store";
import HitlPanel from "./HitlPanel";
import FilesPanel from "./FilesPanel";
import ContextPanel from "./ContextPanel";

export default function RightRail() {
  const { rightTab, setRightTab, hasPending, pending } = useApp();
  const tab = (on: boolean) =>
    ({ flex: 1, padding: 8, borderRadius: "8px 8px 0 0", fontSize: 12.5, fontWeight: 600, color: on ? "var(--hi)" : "var(--lo)", background: on ? "var(--bg2)" : "transparent" }) as const;

  return (
    <div style={{ width: 344, flex: "0 0 344px", background: "var(--bg1)", borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ display: "flex", padding: "12px 12px 0", gap: 2 }}>
        <button style={tab(rightTab === "hitl")} onClick={() => setRightTab("hitl")}>
          Approvals
          {hasPending && (
            <span style={{ marginLeft: 5, fontFamily: "var(--mono)", fontSize: 10, padding: "1px 6px", borderRadius: 9, background: "var(--warn)", color: "#1a1305" }}>{pending.length}</span>
          )}
        </button>
        <button style={tab(rightTab === "files")} onClick={() => setRightTab("files")}>
          Files
        </button>
        <button style={tab(rightTab === "context")} onClick={() => setRightTab("context")}>
          Context
        </button>
      </div>
      <div style={{ height: 1, background: "var(--border)", marginTop: 12 }} />
      <div style={{ flex: 1, overflowY: "auto" }}>
        {rightTab === "hitl" && <HitlPanel />}
        {rightTab === "files" && <FilesPanel />}
        {rightTab === "context" && <ContextPanel />}
      </div>
    </div>
  );
}
