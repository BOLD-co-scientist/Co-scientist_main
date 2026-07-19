import { useState } from "react";
import { useApp } from "../state/store";

export default function Composer() {
  const { status, send, sendNotice, clearNotice } = useApp();
  const [text, setText] = useState("");
  const label = status?.label;
  const blocked = label === "needs you";
  const idle = label === "idle";
  const canSend = blocked || idle;

  const placeholder = blocked
    ? "Message the agent while it waits\u2026 (Approve/Reject at right to finalize)"
    : idle
      ? "Reply to continue the conversation\u2026"
      : label === "crashed"
        ? "Session ended."
        : "Working\u2026 interact via the approval panel.";
  const hint = blocked
    ? "POST /interject \u2014 reaches the agent mid-run."
    : idle
      ? "POST /messages \u2014 resumes the turn. \u23CE to send."
      : "Composer unlocks when the turn finishes or a HITL prompt opens.";

  const submit = async () => {
    const t = text.trim();
    if (!t || !canSend) return;
    const ok = await send(t);
    if (ok) setText(""); // keep text on 409 (§7.6)
  };

  return (
    <div style={{ padding: "12px 24px 16px", borderTop: "1px solid var(--border)", background: "var(--bg1)" }}>
      {sendNotice && (
        <div
          style={{ marginBottom: 10, padding: "8px 12px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 9, fontSize: 12, color: "var(--hi)", display: "flex", alignItems: "center", gap: 8 }}
        >
          <span style={{ flex: 1 }}>{sendNotice}</span>
          <button onClick={clearNotice} style={{ color: "var(--lo)", fontSize: 13 }}>
            {"\u2715"}
          </button>
        </div>
      )}
      <div
        style={{ border: `1px solid ${canSend ? "var(--border-hi)" : "var(--border)"}`, borderRadius: 12, background: "var(--bg0)", padding: "4px 4px 4px 14px", display: "flex", alignItems: "flex-end", gap: 8 }}
      >
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
          placeholder={placeholder}
          rows={1}
          disabled={!canSend}
          style={{ flex: 1, resize: "none", background: "transparent", border: "none", outline: "none", color: "var(--hi)", fontSize: 13.5, padding: "9px 0", maxHeight: 120, lineHeight: 1.5 }}
        />
        <button
          onClick={() => void submit()}
          disabled={!canSend}
          style={{ padding: "9px 17px", borderRadius: 9, fontSize: 13, fontWeight: 600, alignSelf: "stretch", background: canSend ? "var(--accent)" : "var(--bg3)", color: canSend ? "#06121c" : "var(--lo)" }}
        >
          Send
        </button>
      </div>
      <div style={{ marginTop: 7, fontSize: 11, color: "var(--lo)" }}>{hint}</div>
    </div>
  );
}
