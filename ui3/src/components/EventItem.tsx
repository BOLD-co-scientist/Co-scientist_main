import { useState } from "react";
import type { EventVM } from "../lib/eventVM";

export default function EventItem({ vm }: { vm: EventVM }) {
  const [open, setOpen] = useState(false);

  if (vm.variant === "spine") {
    return (
      <div style={{ animation: "fade .25s ease" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "8px 0", color: "var(--lo)" }}>
          <span style={{ height: 1, flex: "0 0 26px", background: "var(--border)" }} />
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: vm.dot, flex: "0 0 auto" }} />
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, letterSpacing: ".02em" }}>{vm.kind}</span>
          <span style={{ fontSize: 11, color: "var(--mid)" }}>{vm.title}</span>
          <span style={{ height: 1, flex: 1, background: "var(--border)" }} />
          <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{vm.time}</span>
        </div>
      </div>
    );
  }

  if (vm.variant === "tool") {
    return (
      <div style={{ margin: "5px 0 5px 6px", animation: "fade .25s ease" }}>
        <button
          onClick={() => setOpen((o) => !o)}
          style={{ display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left", padding: "7px 11px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 8 }}
        >
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: vm.actorColor, flex: "0 0 auto" }}>{vm.glyph}</span>
          <span style={{ fontSize: 12.5, color: "var(--mid)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {vm.actorLabel} used <span style={{ fontFamily: "var(--mono)", color: "var(--hi)" }}>{vm.tool}</span>
          </span>
          <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{vm.time}</span>
          <span style={{ color: "var(--lo)", fontSize: 10, flex: "0 0 auto" }}>{open ? "\u25BE" : "\u25B8"}</span>
        </button>
        {open && vm.arg && (
          <div style={{ margin: "4px 0 0", padding: "9px 12px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 8, fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--mid)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
            {vm.arg}
          </div>
        )}
      </div>
    );
  }

  if (vm.variant === "dispatch") {
    return (
      <div style={{ margin: "5px 0 5px 6px", padding: "8px 12px", background: "var(--bg1)", border: "1px dashed var(--border-hi)", borderRadius: 8, display: "flex", alignItems: "center", gap: 9, fontSize: 12.5, color: "var(--mid)", animation: "fade .25s ease" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--accent)" }}>&rarr;</span>
        <span>
          <span style={{ color: vm.actorColor, fontWeight: 600 }}>{vm.actorLabel}</span> dispatched{" "}
          <span style={{ color: "var(--cyan)", fontWeight: 600 }}>{vm.target}</span>
        </span>
        <span style={{ flex: 1, color: "var(--lo)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{vm.body}</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{vm.time}</span>
      </div>
    );
  }

  if (vm.variant === "hitl") {
    return (
      <div style={{ margin: "9px 0 9px 6px", padding: "12px 14px", background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: 10, animation: "fade .25s ease" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, fontWeight: 600, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".06em" }}>
          <span>{"\u23F8"} human-in-the-loop</span>
          <span style={{ fontFamily: "var(--mono)", textTransform: "none", letterSpacing: 0, color: "var(--lo)" }}>{vm.time}</span>
        </div>
        <div style={{ marginTop: 6, fontSize: 13.5, color: "var(--hi)" }}>{vm.title}</div>
        <div style={{ marginTop: 3, fontSize: 12, color: "var(--mid)" }}>{vm.body}</div>
        <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--warn)" }}>Resolve in the approval panel &rarr;</div>
      </div>
    );
  }

  if (vm.variant === "job") {
    return (
      <div style={{ margin: "5px 0 5px 6px", padding: "8px 12px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 8, display: "flex", alignItems: "center", gap: 9, fontSize: 12.5, animation: "fade .25s ease" }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, padding: "2px 6px", borderRadius: 5, background: "var(--accent-soft)", color: "var(--accent)" }}>longjob</span>
        <span style={{ color: "var(--hi)" }}>{vm.title}</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{vm.time}</span>
      </div>
    );
  }

  // bubble
  const bubbleStyle =
    vm.tone === "human"
      ? { padding: "11px 14px", background: "var(--accent-soft)", border: "1px solid var(--accent-dim)", borderRadius: "12px 12px 4px 12px" }
      : vm.tone === "ok"
        ? { padding: "11px 14px", background: "var(--ok-soft)", border: "1px solid var(--ok)", borderRadius: "12px 12px 12px 4px" }
        : { padding: "11px 14px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: "12px 12px 12px 4px" };

  return (
    <div style={{ animation: "fade .25s ease" }}>
      <div style={{ margin: "10px 0", maxWidth: "82%", marginLeft: vm.tone === "human" ? "auto" : undefined }}>
        {vm.showActor && (
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 5 }}>
            <span style={{ width: 19, height: 19, borderRadius: 5, background: "var(--bg2)", border: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, color: vm.actorColor }}>
              {vm.glyph}
            </span>
            <span style={{ fontSize: 11.5, fontWeight: 600, color: vm.actorColor }}>{vm.actorLabel}</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>{vm.time}</span>
          </div>
        )}
        <div style={bubbleStyle}>
          {vm.label && (
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 5 }}>{vm.label}</div>
          )}
          <div style={{ fontSize: 13.5, color: "var(--hi)", whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{vm.body}</div>
        </div>
      </div>
    </div>
  );
}
