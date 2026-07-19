import { useMemo } from "react";
import { useApp } from "../state/store";
import { timeOf } from "../lib/format";
import type { Ev } from "../lib/types";

// The evolution.* / skill.* lifecycle, read from the active session's real
// event stream (no mock). Evolution runs land their events in the session they
// were launched under; when none have run in the open session, we say so rather
// than invent a timeline.
const EVO_KINDS = /^(evolution|skill)/;

function tone(kind: string): string {
  if (/error|rejected|auto_reject|crash/.test(kind)) return "var(--err)";
  if (/merged|approved|end|live_check/.test(kind)) return "var(--grn)";
  return "var(--evo)";
}
function bodyOf(e: Ev): string {
  const s = (k: string) => (e as Record<string, unknown>)[k];
  for (const k of ["summary", "note", "detail", "text", "error"]) {
    const v = s(k);
    if (typeof v === "string" && v.trim()) return v;
  }
  return "";
}

export default function ActivityLog() {
  const { events, active } = useApp();

  const evo = useMemo(() => events.filter((e) => EVO_KINDS.test(e.kind)), [events]);

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <div style={{ padding: "13px 24px", borderBottom: "1px solid var(--border)", fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>
          Activity log &#183; evolution.* lifecycle{active ? ` · ${active.session_id}` : ""}
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 24px" }}>
          {!active && (
            <div style={{ fontSize: 12.5, color: "var(--lo)", lineHeight: 1.5 }}>
              Select a session to see any evolution activity it produced.
            </div>
          )}
          {active && evo.length === 0 && (
            <div style={{ fontSize: 12.5, color: "var(--lo)", lineHeight: 1.5 }}>
              No evolution activity in this session yet. When the system runs a self-modification, its <span style={{ fontFamily: "var(--mono)" }}>evolution.*</span> lifecycle (start → proposal → live check → merged) streams here, and merges appear in the lineage graph.
            </div>
          )}
          {evo.map((e, i) => {
            const c = tone(e.kind);
            const body = bodyOf(e);
            const last = i === evo.length - 1;
            return (
              <div key={e.id} style={{ display: "flex", gap: 12, paddingBottom: 2 }}>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: "0 0 auto" }}>
                  <span style={{ width: 11, height: 11, borderRadius: "50%", background: c, border: "2px solid var(--bg0)", marginTop: 3 }} />
                  {!last && <span style={{ width: 2, flex: 1, background: "var(--border)", minHeight: 14 }} />}
                </div>
                <div style={{ flex: 1, paddingBottom: 16 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: c }}>{e.kind}</span>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{timeOf(e.ts)}</span>
                  </div>
                  {body && <div style={{ marginTop: 3, fontSize: 13, color: "var(--hi)", lineHeight: 1.45 }}>{body}</div>}
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ padding: "12px 24px 16px", borderTop: "1px solid var(--border)", background: "var(--bg1)" }}>
          <div style={{ fontSize: 11, color: "var(--lo)", lineHeight: 1.5 }}>
            {"⚠"} Issuing evolution commands from the UI is not yet wired — it needs <span style={{ fontFamily: "var(--mono)" }}>POST /evolution/commands</span> on the backend. Evolution currently runs via the CLI; its lifecycle and merges are reflected above and in the lineage graph.
          </div>
        </div>
      </div>
    </div>
  );
}
