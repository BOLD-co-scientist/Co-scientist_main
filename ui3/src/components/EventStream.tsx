import { useEffect, useRef } from "react";
import { useApp } from "../state/store";
import { toVM } from "../lib/eventVM";
import EventItem from "./EventItem";
import Composer from "./Composer";

export default function EventStream() {
  const { active, status, events, hasPending, pending, setRightTab, stop, workingHyp, clearWorkingHyp, setMainView } = useApp();
  const scRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = scRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  if (!active) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--lo)", fontSize: 13 }}>
        Select a session, or start a new one.
      </div>
    );
  }

  const running = active.running && !active.blocked;

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* working-hypothesis bar — pinned at the very top; passed to the agent as
          context and persists here until cleared */}
      {workingHyp && (
        <div style={{ padding: "10px 24px", background: "var(--accent-soft)", borderBottom: "1px solid var(--accent-dim)", display: "flex", alignItems: "center", gap: 11, animation: "fade .3s ease" }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, fontWeight: 700, color: "var(--accent)", textTransform: "uppercase", letterSpacing: ".06em", flex: "0 0 auto" }}>Working hypothesis</span>
          <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={workingHyp.statement}>
            {workingHyp.statement}
          </span>
          <button onClick={() => setMainView("hypothesis")} style={{ fontSize: 11.5, fontWeight: 600, color: "var(--accent)", fontFamily: "var(--mono)", flex: "0 0 auto" }}>
            view
          </button>
          <button onClick={clearWorkingHyp} title="Stop focusing on this hypothesis" style={{ fontSize: 12, color: "var(--lo)", flex: "0 0 auto" }}>
            {"✕"}
          </button>
        </div>
      )}

      {/* session header */}
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.4 }}>{active.task}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 5, fontFamily: "var(--mono)", fontSize: 11, color: "var(--lo)" }}>
            <span>{active.session_id}</span>
            <span>&#183;</span>
            <span>{events.length} events</span>
            <span>&#183;</span>
            <span style={{ color: status?.color }}>{status?.text}</span>
          </div>
        </div>
        {running && (
          <button onClick={() => void stop()} style={{ padding: "7px 14px", border: "1px solid var(--err)", color: "var(--err)", borderRadius: 8, fontSize: 12.5, fontWeight: 600 }}>
            Stop
          </button>
        )}
      </div>

      {/* pinned HITL banner */}
      {hasPending && (
        <div style={{ margin: "12px 24px 0", padding: "11px 14px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 10, display: "flex", alignItems: "center", gap: 11, animation: "fade .3s ease" }}>
          <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--warn)", animation: "pulse 1.6s infinite" }} />
          <span style={{ flex: 1, fontSize: 13, color: "var(--hi)" }}>
            <b style={{ fontWeight: 600 }}>Approval needed</b> — {pending[0]?.title}
          </span>
          <button onClick={() => setRightTab("hitl")} style={{ fontSize: 12, fontWeight: 600, color: "var(--warn)", fontFamily: "var(--mono)" }}>
            review &rarr;
          </button>
        </div>
      )}

      {/* timeline */}
      <div
        ref={scRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
        }}
        style={{ flex: 1, overflowY: "auto", padding: "18px 24px 12px" }}
      >
        {events.map((e, i) => (
          <EventItem key={e.id} vm={toVM(e, events[i - 1])} />
        ))}
        <div style={{ height: 6 }} />
      </div>

      <Composer />
    </div>
  );
}
