import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useApp } from "../state/store";
import { EVO_LOG } from "../lib/mock";
import type { GitRef } from "../lib/types";

const toneColor = (t: "evo" | "grn" | "mid") => (t === "grn" ? "var(--grn)" : t === "evo" ? "var(--evo)" : "var(--mid)");

const refStyle = (kind: GitRef["kind"]): CSSProperties => {
  const base = { fontFamily: "var(--mono)", fontSize: 9.5, padding: "1px 6px", borderRadius: 5 } as const;
  if (kind === "evo") return { ...base, background: "var(--warn-soft)", color: "var(--warn)" };
  if (kind === "main") return { ...base, background: "var(--ok-soft)", color: "var(--ok)" };
  return { ...base, background: "var(--accent-soft)", color: "var(--accent)" };
};

export default function ActivityLog() {
  const { git, loadGit, setRightTab, setMainView, mock } = useApp();
  const [evoCmd, setEvoCmd] = useState("");

  useEffect(() => {
    loadGit();
  }, [loadGit]);

  const gotoHitl = () => {
    setMainView("session");
    setRightTab("hitl");
  };

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      {/* console */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0, borderRight: "1px solid var(--border)" }}>
        <div style={{ padding: "13px 24px", borderBottom: "1px solid var(--border)", fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em" }}>
          Activity log &#183; evolution.* lifecycle
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 24px" }}>
          {!mock && (
            <div style={{ fontSize: 12.5, color: "var(--lo)", lineHeight: 1.5 }}>
              No evolution activity yet. The <span style={{ fontFamily: "var(--mono)" }}>evolution.*</span> lifecycle timeline will appear here once evolution sessions run.
            </div>
          )}
          {mock && EVO_LOG.map((e, i) => (
            <div key={i} style={{ display: "flex", gap: 12, paddingBottom: 2 }}>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: "0 0 auto" }}>
                <span style={{ width: 11, height: 11, borderRadius: "50%", background: toneColor(e.tone), border: "2px solid var(--bg0)", marginTop: 3 }} />
                <span style={{ width: 2, flex: 1, background: "var(--border)", minHeight: 14 }} />
              </div>
              <div style={{ flex: 1, paddingBottom: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: toneColor(e.tone) }}>{e.kind}</span>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{e.time}</span>
                </div>
                <div style={{ marginTop: 3, fontSize: 13, color: "var(--hi)" }}>{e.body}</div>
              </div>
            </div>
          ))}
          {mock && (
          <div style={{ margin: "6px 0 0 23px", padding: "13px 15px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 11 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".06em" }}>Merge proposal &#183; awaiting approval</div>
            <div style={{ marginTop: 6, fontSize: 13.5, color: "var(--hi)", fontFamily: "var(--mono)" }}>evo/faster-docking</div>
            <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--mid)" }}>Batch the docking backend to submit all poses in one GPU job. Strict smoke gated &#183; 3 files changed.</div>
            <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
              <button onClick={gotoHitl} style={{ padding: "7px 15px", background: "var(--warn)", color: "#1a1305", fontWeight: 600, borderRadius: 8, fontSize: 12.5 }}>
                Review diff &amp; decide
              </button>
            </div>
          </div>
          )}
        </div>
        <div style={{ padding: "12px 24px 16px", borderTop: "1px solid var(--border)", background: "var(--bg1)" }}>
          <div style={{ border: "1px solid var(--border)", borderRadius: 12, background: "var(--bg0)", padding: "4px 4px 4px 14px", display: "flex", alignItems: "flex-end", gap: 8 }}>
            <textarea
              value={evoCmd}
              onChange={(e) => setEvoCmd(e.target.value)}
              placeholder='Issue an evolution command… e.g. "speed up the docking tool"'
              rows={1}
              style={{ flex: 1, resize: "none", background: "transparent", border: "none", outline: "none", color: "var(--hi)", fontSize: 13.5, padding: "9px 0", lineHeight: 1.5 }}
            />
            <button onClick={() => setEvoCmd("")} style={{ padding: "9px 17px", background: "var(--evo)", color: "#14091f", fontWeight: 600, borderRadius: 9, fontSize: 13, alignSelf: "stretch" }}>
              Run
            </button>
          </div>
          <div style={{ marginTop: 7, fontSize: 11, color: "var(--lo)" }}>
            {"\u26A0"} Not-yet-wired — designed against the <span style={{ fontFamily: "var(--mono)" }}>evolution.*</span> lifecycle; drops in when <span style={{ fontFamily: "var(--mono)" }}>POST /evolution/commands</span> lands.
          </div>
        </div>
      </div>

      {/* git graph */}
      <div style={{ width: 340, flex: "0 0 340px", display: "flex", flexDirection: "column", background: "var(--bg1)", minHeight: 0 }}>
        <div style={{ padding: "15px 18px 12px", borderBottom: "1px solid var(--border)", fontSize: 13, fontWeight: 600 }}>
          System repo <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 500, color: "var(--lo)" }}>git/history</span>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 18px" }}>
          {(git?.commits ?? []).map((c) => {
            const isHead = c.refs.some((r) => r.kind === "head");
            const isMain = c.refs.some((r) => r.kind === "main");
            const isEvo = c.refs.some((r) => r.kind === "evo");
            const fill = isEvo ? "var(--warn)" : isMain ? "var(--grn)" : "var(--bg1)";
            const stroke = isEvo ? "var(--warn)" : isMain ? "var(--grn)" : "var(--border-hi)";
            return (
              <div key={c.sha} style={{ display: "flex", gap: 12 }}>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: "0 0 auto", width: 16 }}>
                  <span style={{ width: 12, height: 12, borderRadius: "50%", background: fill, border: `2px solid ${stroke}`, boxShadow: isHead ? "0 0 0 3px var(--accent-dim)" : "none" }} />
                  <span style={{ width: 2, flex: 1, background: "var(--border)", minHeight: 22 }} />
                </div>
                <div style={{ flex: 1, paddingBottom: 14, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--mid)" }}>{c.sha}</span>
                    {c.refs.map((r) => (
                      <span key={r.name} style={refStyle(r.kind)}>
                        {r.name}
                      </span>
                    ))}
                  </div>
                  <div style={{ marginTop: 3, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.4 }}>{c.subject}</div>
                  <div style={{ marginTop: 2, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>
                    {c.author} · {c.ts}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ padding: "11px 18px", borderTop: "1px solid var(--border)", display: "flex", gap: 14, fontSize: 10.5, color: "var(--lo)" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--grn)" }} />
            main
          </span>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--warn)" }} />
            in-flight evo/*
          </span>
        </div>
      </div>
    </div>
  );
}
