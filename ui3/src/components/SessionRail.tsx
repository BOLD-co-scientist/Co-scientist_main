import { useApp } from "../state/store";
import { statusOf } from "../lib/format";

export default function SessionRail() {
  const { sessions, activeId, select, mainView, setMainView, startNewSession } = useApp();
  const onboarding = mainView === "onboarding";

  const navBtn = (on: boolean) =>
    ({
      display: "flex",
      alignItems: "center",
      gap: 10,
      width: "100%",
      textAlign: "left" as const,
      padding: "9px 12px",
      borderRadius: 8,
      fontSize: 13,
      fontWeight: 500,
      background: on ? "var(--bg2)" : "transparent",
      color: on ? "var(--hi)" : "var(--mid)",
    });

  return (
    <div style={{ width: 272, flex: "0 0 272px", background: "var(--bg1)", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "14px 14px 10px" }}>
        <button
          onClick={startNewSession}
          title="Define the problem, attach data, pick a hypothesis, then launch"
          style={{ width: "100%", padding: 10, background: onboarding ? "var(--accent)" : "var(--accent-soft)", border: "1px solid var(--accent-dim)", color: onboarding ? "#06121c" : "var(--accent)", borderRadius: 9, fontWeight: 600, fontSize: 13, display: "flex", alignItems: "center", justifyContent: "center", gap: 7 }}
        >
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span> New session
        </button>
      </div>

      <div style={{ padding: "2px 10px 10px", display: "flex", flexDirection: "column", gap: 1 }}>
        <button onClick={() => setMainView("session")} style={navBtn(mainView === "session")}>
          <span style={{ width: 15, textAlign: "center", opacity: 0.85 }}>{"\u25A4"}</span> Sessions
        </button>
        <button onClick={() => setMainView("hypothesis")} style={navBtn(mainView === "hypothesis")}>
          <span style={{ width: 15, textAlign: "center", opacity: 0.85 }}>{"\u22D4"}</span> Hypotheses
        </button>
        <button onClick={() => setMainView("evolution")} style={navBtn(mainView === "evolution")}>
          <span style={{ width: 15, textAlign: "center", opacity: 0.85 }}>{"\u25C7"}</span> Evolution
        </button>
        <button onClick={() => setMainView("projects")} title="The org-wide project tree: every shared problem and how they relate" style={navBtn(mainView === "projects")}>
          <span style={{ width: 15, textAlign: "center", opacity: 0.85 }}>{"\u2B21"}</span> Projects
        </button>
      </div>

      <div style={{ padding: "12px 18px 6px", fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".09em" }}>
        Sessions
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "0 10px 14px" }}>
        {sessions.map((s) => {
          const st = statusOf(s);
          const on = s.session_id === activeId && mainView === "session";
          return (
            <button
              key={s.session_id}
              onClick={() => select(s.session_id)}
              style={{ display: "flex", gap: 9, width: "100%", textAlign: "left", padding: "10px 11px", borderRadius: 9, marginBottom: 2, border: `1px solid ${on ? "var(--border-hi)" : "transparent"}`, background: on ? "var(--bg2)" : "transparent" }}
            >
              <span style={{ width: 7, height: 7, borderRadius: "50%", flex: "0 0 auto", marginTop: 6, background: st.dot, animation: s.running && !s.blocked ? "pulse 1.6s infinite" : undefined }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: "-webkit-box", fontSize: 12.5, color: "var(--hi)", lineHeight: 1.35, overflow: "hidden", textOverflow: "ellipsis", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                  {s.task}
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 4 }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{s.session_id}</span>
                  <span style={{ fontSize: 10, color: st.color, fontWeight: 600 }}>{st.label}</span>
                  {s.has_brief && (
                    <span title="Started from a problem brief" style={{ fontFamily: "var(--mono)", fontSize: 9, fontWeight: 700, padding: "0 5px", borderRadius: 4, background: "var(--accent-soft)", color: "var(--accent)", letterSpacing: ".04em" }}>
                      brief
                    </span>
                  )}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
