import { useState } from "react";
import { useApp } from "../state/store";
import Markdown from "./Markdown";
import type { HitlPending } from "../lib/types";

type AnswerFn = (requestId: string, decision: "approve" | "reject", note?: string) => Promise<void>;

// Defaults to the research session's pending/answer, but the Evolution tab passes
// its own (evoPending / answerEvo) so the same approval UI serves both channels.
export default function HitlPanel({ pending: pendingProp, answer: answerProp }: { pending?: HitlPending[]; answer?: AnswerFn } = {}) {
  const app = useApp();
  const pending = pendingProp ?? app.pending;
  const answer = answerProp ?? app.answer;
  const [note, setNote] = useState("");

  if (pending.length === 0) {
    return (
      <div style={{ padding: "40px 16px", textAlign: "center", color: "var(--lo)" }}>
        <div style={{ width: 38, height: 38, borderRadius: "50%", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 12px", fontSize: 16, color: "var(--ok)" }}>
          {"\u2713"}
        </div>
        <div style={{ fontSize: 13, color: "var(--mid)" }}>No approvals pending.</div>
        <div style={{ marginTop: 4, fontSize: 11.5 }}>You'll be alerted here when the agent needs a decision.</div>
      </div>
    );
  }

  return (
    <div style={{ padding: "16px 16px 20px" }}>
      {pending.map((p) => (
        <div key={p.request_id} style={{ border: "1px solid var(--warn)", borderRadius: 12, overflow: "hidden", animation: "ring 2s 1", marginBottom: 12 }}>
          <div style={{ padding: "11px 14px", background: "var(--warn-soft)", borderBottom: "1px solid var(--warn)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--warn)", animation: "pulse 1.6s infinite" }} />
              <span style={{ fontSize: 11, fontWeight: 700, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".07em" }}>Approval required</span>
            </div>
            <div style={{ marginTop: 8, fontSize: 14, fontWeight: 600, color: "var(--hi)" }}>{p.title}</div>
          </div>
          <div style={{ padding: "13px 14px" }}>
            {p.action && (
              <>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".06em" }}>What will happen</div>
                <div style={{ marginTop: 5, fontSize: 13, color: "var(--hi)", lineHeight: 1.5 }}>{p.action}</div>
              </>
            )}
            {p.meta && (
              <div style={{ marginTop: 6, fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{p.meta}</div>
            )}
            {p.detail && (
              p.detailMarkdown ? (
                <Markdown
                  text={p.detail}
                  style={{ marginTop: 11, padding: "10px 13px", maxHeight: 340, overflowY: "auto", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12.5, color: "var(--mid)" }}
                />
              ) : (
                <div style={{ marginTop: 11, padding: "9px 11px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--mid)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                  {p.detail}
                </div>
              )
            )}
            {p.judge && p.judge.verdict !== "unavailable" && (
              <div style={{ marginTop: 10, padding: "9px 11px", borderRadius: 8, border: `1px solid ${p.judge.verdict === "approve" ? "var(--ok)" : "var(--err)"}`, background: "var(--bg2)" }}>
                <div style={{ fontSize: 10.5, fontWeight: 700, color: p.judge.verdict === "approve" ? "var(--ok)" : "var(--err)", textTransform: "uppercase", letterSpacing: ".06em" }}>
                  Judge · {p.judge.verdict} · {Math.round((p.judge.score ?? 0) * 100)}%{p.judge.served_by ? ` · ${p.judge.served_by}` : ""}
                </div>
                {p.judge.recommendation && <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--hi)", fontWeight: 600 }}>{p.judge.recommendation}</div>}
                {p.judge.why && <div style={{ marginTop: 3, fontSize: 12, color: "var(--mid)", lineHeight: 1.5 }}>{p.judge.why}</div>}
                {p.judge.risks?.length > 0 && <div style={{ marginTop: 4, fontSize: 11, color: "var(--lo)" }}>Risks: {p.judge.risks.join("; ")}</div>}
                {p.judge.mode === "automatic" && (
                  <div style={{ marginTop: 4, fontSize: 11, color: "var(--lo)" }}>
                    Automatic mode reviewed this and left the decision to you — either because it touches the
                    gate machinery (a stricter human gate) or because it has been sent back for changes already.
                  </div>
                )}
              </div>
            )}
            <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 7 }}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>requested by</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--cyan)" }}>{p.requester}</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)" }}>&#183; {p.request_id}</span>
            </div>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Optional note…"
              style={{ width: "100%", marginTop: 11, padding: "9px 11px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12.5, outline: "none" }}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button
                onClick={() => {
                  void answer(p.request_id, "approve", note);
                  setNote("");
                }}
                style={{ flex: 1, padding: 10, background: "var(--ok)", color: "#04160c", fontWeight: 700, borderRadius: 9, fontSize: 13 }}
              >
                Approve
              </button>
              <button
                onClick={() => {
                  void answer(p.request_id, "reject", note);
                  setNote("");
                }}
                style={{ flex: 1, padding: 10, background: "var(--err-soft)", border: "1px solid var(--err)", color: "var(--err)", fontWeight: 700, borderRadius: 9, fontSize: 13 }}
              >
                Reject
              </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
