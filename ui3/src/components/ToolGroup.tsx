import { useState } from "react";
import type { Ev } from "../lib/types";
import { toVM, type VMCtx } from "../lib/eventVM";
import EventItem from "./EventItem";

// A run of consecutive same-tool, same-actor calls, collapsed into one row like
// Claude Code's "Called slack 3 times". Collapsed by default to kill the
// back-to-back spam; expands to the individual meaningful rows (each keeps its
// own one-line intent, so no information is lost — just tucked away).
export default function ToolGroup({ items, prev, ctx }: { items: Ev[]; prev?: Ev; ctx?: VMCtx }) {
  const [open, setOpen] = useState(false);
  const head = toVM(items[0], prev, ctx);
  if (head.variant !== "tool") {
    return (
      <>
        {items.map((e, i) => (
          <EventItem key={e.id} vm={toVM(e, i === 0 ? prev : items[i - 1], ctx)} />
        ))}
      </>
    );
  }

  // Group is still working if the timeline is live and its LAST call has no
  // result yet (earlier calls in the run have typically already resolved).
  const lastRef = (items[items.length - 1] as Record<string, unknown>).ref as string | undefined;
  const running = !!ctx?.live && !(lastRef && ctx?.results?.get(lastRef));

  return (
    <div style={{ margin: "3px 0 1px 6px", animation: "fade .25s ease" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left", padding: "3px 8px", background: open ? "var(--bg1)" : "transparent", border: "none", borderRadius: 6 }}
      >
        <span style={{ width: 14, flex: "0 0 auto", fontFamily: "var(--mono)", fontSize: 10.5, color: head.actorColor, textAlign: "center", animation: running ? "pulse 1.4s infinite" : undefined }}>{head.glyph}</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 12, fontWeight: 600, color: "var(--hi)", flex: "0 0 auto" }}>{head.verb}</span>
        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)", flex: "0 0 auto", padding: "0 6px", border: "1px solid var(--border)", borderRadius: 10 }}>×{items.length}</span>
        {!open && head.arg && (
          <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--mid)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{head.arg}</span>
        )}
        <span style={{ flex: !open && head.arg ? "0 0 auto" : 1 }} />
        <span style={{ color: "var(--lo)", fontSize: 10, flex: "0 0 auto" }}>{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div style={{ borderLeft: "1px solid var(--border)", marginLeft: 13 }}>
          {items.map((e, i) => (
            <EventItem key={e.id} vm={toVM(e, i === 0 ? undefined : items[i - 1], ctx)} />
          ))}
        </div>
      )}
    </div>
  );
}
