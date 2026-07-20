import { useEffect, useRef, useState } from "react";
import { useApp } from "../state/store";
import { actorColor, actorGlyph } from "../lib/format";
import type { MemoryHit } from "../lib/types";

const K_OPTIONS = [3, 5, 10];

export default function ContextPanel() {
  const { roles, loadRoles, api, activeId } = useApp();
  const [q, setQ] = useState("");
  const [k, setK] = useState(5);
  const [hits, setHits] = useState<MemoryHit[]>([]);
  const [searching, setSearching] = useState(false);
  const kRef = useRef(k);
  kRef.current = k;

  // Keep the agent roster live: refetch on mount and poll, so an evolution that
  // adds an agent shows up here without a manual reload.
  useEffect(() => {
    void loadRoles();
    const id = setInterval(() => void loadRoles(), 12000);
    return () => clearInterval(id);
  }, [loadRoles]);

  const runSearch = async () => {
    setSearching(true);
    try {
      const kk = kRef.current;
      // Global memory always; project memory too when a session is open.
      const calls: Promise<{ hits: MemoryHit[] }>[] = [api.searchMemory("global", q, kk)];
      if (activeId) calls.push(api.searchMemory("project", q, kk, activeId));
      const results = await Promise.all(calls.map((p) => p.catch(() => ({ hits: [] as MemoryHit[] }))));
      const merged = results.flatMap((r) => r.hits).sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, kk * calls.length);
      setHits(merged);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div style={{ padding: "14px 16px 20px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 9 }}>
        <div style={{ flex: 1, fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>
          Agent roles ({roles.length})
        </div>
        <span title="Auto-refreshed — reflects merged evolutions" style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 9.5, color: "var(--ok)", fontFamily: "var(--mono)" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--ok)", animation: "pulse 2s infinite" }} /> live
        </span>
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
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void runSearch();
          }}
          placeholder="Query global + project memory…"
          style={{ flex: 1, minWidth: 0, padding: "9px 11px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12.5, outline: "none" }}
        />
        <button
          onClick={() => void runSearch()}
          disabled={searching}
          style={{ padding: "9px 14px", background: "var(--accent)", color: "#06121c", border: "none", borderRadius: 8, fontSize: 12.5, fontWeight: 600, cursor: searching ? "default" : "pointer", flex: "0 0 auto" }}
        >
          {searching ? "…" : "Search"}
        </button>
      </div>
      {/* k toggle */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 11 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>results (k)</span>
        <div style={{ display: "flex", gap: 3, padding: 2, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 7 }}>
          {K_OPTIONS.map((opt) => (
            <button
              key={opt}
              onClick={() => setK(opt)}
              style={{ padding: "3px 9px", borderRadius: 5, fontFamily: "var(--mono)", fontSize: 11, fontWeight: 600, background: k === opt ? "var(--accent-soft)" : "transparent", color: k === opt ? "var(--accent)" : "var(--mid)" }}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>
      {hits.length === 0 && q && !searching && (
        <div style={{ fontSize: 12, color: "var(--lo)" }}>No matches.</div>
      )}
      {hits.map((m, i) => (
        <div key={i} style={{ padding: "9px 11px", borderLeft: "2px solid var(--accent-dim)", background: "var(--bg2)", borderRadius: "0 8px 8px 0", marginBottom: 6 }}>
          <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>
            {m.layer} &#183; score {(m.score ?? 0).toFixed(2)}
          </div>
          <div style={{ marginTop: 3, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.45 }}>{m.text}</div>
        </div>
      ))}
    </div>
  );
}
