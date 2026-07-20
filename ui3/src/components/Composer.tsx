import { useState } from "react";
import { useApp } from "../state/store";

export default function Composer() {
  const { status, send, sendNotice, clearNotice, draftNew, active, queue } = useApp();
  const [text, setText] = useState("");
  const label = status?.label;
  const blocked = label === "needs you";
  const idle = label === "idle";
  const running = !!active?.running && !active?.blocked;
  // Can always compose except on a crashed/ended session that isn't a new draft.
  const canSend = draftNew || (!!active && label !== "crashed");

  const placeholder = draftNew
    ? "Describe your research task and press Enter to start…"
    : blocked
      ? "Message the agent while it waits… (Approve/Reject at right to finalize)"
      : idle
        ? "Reply to continue the conversation…"
        : running
          ? "Queue a message… (sends when the agent is free)"
          : label === "crashed"
            ? "Session ended."
            : "Working…";
  const hint = draftNew
    ? "⏎ starts the session with your first message."
    : blocked
      ? "Reaches the agent mid-run. ⏎ to send."
      : running
        ? "The agent is working — your message queues and sends when it's free. ⏎ to queue."
        : idle
          ? "Resumes the turn. ⏎ to send · ⇧⏎ for a newline."
          : "Composer unlocks when the turn finishes or a HITL prompt opens.";

  const submit = async () => {
    const t = text.trim();
    if (!t || !canSend) return;
    const ok = await send(t);
    if (ok) setText(""); // keep text only if the send was rejected outright
  };

  return (
    <div style={{ padding: "12px 24px 16px", borderTop: "1px solid var(--border)", background: "var(--bg1)" }}>
      {queue.length > 0 && (
        <div style={{ marginBottom: 9, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".06em" }}>Queued</span>
          {queue.map((q, i) => (
            <span key={i} style={{ maxWidth: 260, fontSize: 11.5, padding: "3px 9px", borderRadius: 12, background: "var(--bg3)", color: "var(--mid)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={q}>
              {q}
            </span>
          ))}
        </div>
      )}
      {sendNotice && (
        <div
          style={{ marginBottom: 10, padding: "8px 12px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 9, fontSize: 12, color: "var(--hi)", display: "flex", alignItems: "center", gap: 8 }}
        >
          <span style={{ flex: 1 }}>{sendNotice}</span>
          <button onClick={clearNotice} style={{ color: "var(--lo)", fontSize: 13 }}>
            {"✕"}
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
          disabled={!canSend || !text.trim()}
          style={{ padding: "9px 17px", borderRadius: 9, fontSize: 13, fontWeight: 600, alignSelf: "stretch", background: canSend && text.trim() ? "var(--accent)" : "var(--bg3)", color: canSend && text.trim() ? "#06121c" : "var(--lo)" }}
        >
          {draftNew ? "Start" : running ? "Queue" : "Send"}
        </button>
      </div>
      <div style={{ marginTop: 7, fontSize: 11, color: "var(--lo)" }}>{hint}</div>
    </div>
  );
}
