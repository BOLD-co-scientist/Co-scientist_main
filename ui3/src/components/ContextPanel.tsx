import { useEffect, useState } from "react";
import { useApp } from "../state/store";
import { actorColor, actorGlyph } from "../lib/format";

export default function ContextPanel() {
  const { roles, memory, loadContext, searchMemory } = useApp();
  const [q, setQ] = useState("");

  useEffect(() => {
    loadContext();
  }, [loadContext]);

  return (
    <div style={{ padding: "14px 16px 20px" }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", marginBottom: 9 }}>
        Agent roles
      </div>
      {roles.map((r) => (
        <div key={r.name} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "11px 13px", marginBottom: 8, background: "var(--bg2)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 20, height: 20, borderRadius: 5, background: "var(--bg3)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, color: actorColor(r.name) }}>
              {actorGlyph(r.name)}
            </span>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--hi)" }}>{r.name}</span>
            <span style={{ flex: 1 }} />
            {r.can_spawn && (
              <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, padding: "2px 6px", borderRadius: 5, background: "var(--accent-soft)", color: "var(--accent)" }}>can spawn</span>
            )}
          </div>
          <div style={{ marginTop: 6, fontSize: 12, color: "var(--mid)", lineHeight: 1.45 }}>{r.description}</div>
          <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 5 }}>
            {r.tools.map((t) => (
              <span key={t} style={{ fontFamily: "var(--mono)", fontSize: 10, padding: "2px 7px", borderRadius: 5, background: "var(--bg3)", color: "var(--mid)" }}>
                {t}
              </span>
            ))}
          </div>
          <div style={{ marginTop: 8, fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>{r.model}</div>
        </div>
      ))}

      <div style={{ fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", margin: "18px 0 9px" }}>
        Memory search
      </div>
      <div style={{ display: "flex", gap: 6, marginBottom: 9 }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void searchMemory(q);
          }}
          placeholder="Query global + project memory…"
          style={{ flex: 1, padding: "9px 11px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12.5, outline: "none" }}
        />
        <button onClick={() => void searchMemory(q)} style={{ padding: "9px 13px", background: "var(--bg3)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, color: "var(--mid)", fontFamily: "var(--mono)" }}>
          k=5
        </button>
      </div>
      {memory.map((m, i) => (
        <div key={i} style={{ padding: "9px 11px", borderLeft: "2px solid var(--accent-dim)", background: "var(--bg2)", borderRadius: "0 8px 8px 0", marginBottom: 6 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>
            {m.layer} &#183; score {m.score.toFixed(2)}
          </div>
          <div style={{ marginTop: 3, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.45 }}>{m.text}</div>
        </div>
      ))}
    </div>
  );
}
