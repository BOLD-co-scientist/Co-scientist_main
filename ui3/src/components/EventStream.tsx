import { useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../state/store";
import type { Ev } from "../lib/types";
import { cleanTool, dedupeHumanEcho, isSystemNoise, toVM, type ToolResult } from "../lib/eventVM";
import EventItem from "./EventItem";
import ToolGroup from "./ToolGroup";
import Composer from "./Composer";

// Fold consecutive same-tool, same-actor tool.use runs (>=2) into one group;
// everything else stays a standalone unit. Each unit carries the previous
// VISIBLE event so actor/continuation styling is relative to what's shown.
type Unit =
  | { kind: "single"; e: Ev; prev?: Ev }
  | { kind: "group"; items: Ev[]; prev?: Ev };

function buildUnits(evs: Ev[]): Unit[] {
  const units: Unit[] = [];
  let i = 0;
  while (i < evs.length) {
    const e = evs[i];
    const prev = evs[i - 1];
    if (e.kind === "tool.use") {
      const tool = cleanTool((e as Record<string, unknown>).tool as string ?? "");
      let j = i + 1;
      while (
        j < evs.length &&
        evs[j].kind === "tool.use" &&
        evs[j].actor === e.actor &&
        cleanTool((evs[j] as Record<string, unknown>).tool as string ?? "") === tool
      )
        j++;
      if (j - i >= 2) units.push({ kind: "group", items: evs.slice(i, j), prev });
      else units.push({ kind: "single", e, prev });
      i = j;
    } else {
      units.push({ kind: "single", e, prev });
      i++;
    }
  }
  return units;
}

export default function EventStream() {
  const { active, status, events, hasPending, pending, setRightTab, stop, forkSession, workingHyp, clearWorkingHyp, setMainView, draftNew, reflectionNudge, dismissReflection, setEvolutionCommand } = useApp();
  const scRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // Default timeline hides low-signal system events (see isSystemNoise). ⌘/Ctrl-O
  // reveals them, mirroring Claude Code's Ctrl-O transcript view — a shortcut,
  // not an on-screen switch.
  const [showSystem, setShowSystem] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(true);
  const [selectedSuggestion, setSelectedSuggestion] = useState<number | null>(null);

  useEffect(() => {
    setSelectedSuggestion(null);
    setSuggestionsOpen(true);
  }, [reflectionNudge?.session_id]);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if ((ev.metaKey || ev.ctrlKey) && (ev.key === "o" || ev.key === "O")) {
        ev.preventDefault();
        setShowSystem((s) => !s);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Pair each tool.result to its tool.use via `ref`; tool.result rows never show
  // on their own — they surface inside the action row / group they belong to.
  const results = useMemo(() => {
    const m = new Map<string, ToolResult>();
    for (const e of events) {
      if (e.kind !== "tool.result") continue;
      const r = e as Record<string, unknown>;
      const ref = r.ref as string | undefined;
      if (ref) m.set(ref, { summary: (r.summary as string) ?? "", full: (r.detail as string) ?? "", isError: !!r.is_error });
    }
    return m;
  }, [events]);
  const live = !!active?.running && !active?.blocked;
  const ctx = useMemo(() => ({ results, live }), [results, live]);

  const hiddenCount = useMemo(() => events.filter(isSystemNoise).length, [events]);
  const units = useMemo(() => {
    const visible = (showSystem ? events : events.filter((e) => !isSystemNoise(e))).filter(
      (e) => e.kind !== "tool.result",
    );
    // Follow-up human messages are logged twice (message.received + an identical
    // human_directive echo); collapse the echo so a message shows once. Kept raw
    // in system view (⌘/Ctrl-O). See dedupeHumanEcho.
    const deduped = showSystem ? visible : dedupeHumanEcho(visible);
    return buildUnits(deduped);
  }, [events, showSystem]);

  useEffect(() => {
    const el = scRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  // New-session draft: an empty session with just the composer — the user's
  // first message creates the real session.
  if (!active && draftNew) {
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {workingHyp && (
          <div style={{ padding: "10px 24px", background: "var(--accent-soft)", borderBottom: "1px solid var(--accent-dim)", display: "flex", alignItems: "center", gap: 11 }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, fontWeight: 700, color: "var(--accent)", textTransform: "uppercase", letterSpacing: ".06em", flex: "0 0 auto" }}>Working hypothesis</span>
            <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--hi)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={workingHyp.statement}>{workingHyp.statement}</span>
            <button onClick={clearWorkingHyp} style={{ fontSize: 12, color: "var(--lo)" }}>{"✕"}</button>
          </div>
        )}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", color: "var(--lo)", gap: 6, padding: 24, textAlign: "center" }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--mid)" }}>New research session</div>
          <div style={{ fontSize: 13, maxWidth: 420, lineHeight: 1.5 }}>
            Describe your research task below and press Enter to start. {workingHyp ? "Your working hypothesis will frame it." : ""}
          </div>
        </div>
        <Composer />
      </div>
    );
  }

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

      {/* R17: read-only banner — this session was created by a newer version than
          the one now active, so it opens read-only. Fork to continue here. */}
      {active.readonly && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 24px", background: "var(--warn-soft, var(--bg2))", borderBottom: "1px solid var(--border)" }}>
          <span style={{ flex: 1, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.4 }}>
            <b style={{ color: "var(--hi)", fontWeight: 600 }}>Read-only.</b> This session was created by a newer version than the one now active — it can't run here. Fork it to continue on the current version (the original is preserved).
          </span>
          <button onClick={() => void forkSession(active.session_id)}
            style={{ flex: "0 0 auto", padding: "7px 14px", background: "var(--accent)", color: "#0a0f1c", borderRadius: 8, fontSize: 12.5, fontWeight: 700 }}>
            Fork to continue
          </button>
        </div>
      )}

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
        {units.map((u) =>
          u.kind === "group" ? (
            <ToolGroup key={u.items[0].id} items={u.items} prev={u.prev} ctx={ctx} />
          ) : (
            <EventItem key={u.e.id} vm={toVM(u.e, u.prev, ctx)} />
          ),
        )}
        {(hiddenCount > 0 || showSystem) && (
          <div style={{ margin: "10px 0 2px", textAlign: "center", fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--lo)" }}>
            {showSystem
              ? `showing all events · ${"⌘"}O to hide system`
              : `${hiddenCount} system event${hiddenCount === 1 ? "" : "s"} hidden · ${"⌘"}O to show`}
          </div>
        )}
        <div style={{ height: 6 }} />
      </div>

      {reflectionNudge && (
        <div style={{ margin: "0 24px 10px", padding: "11px 13px", background: "var(--evo-soft)", border: "1px solid var(--evo)", borderRadius: 10, animation: "fade .3s ease" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
            <button onClick={() => setSuggestionsOpen((open) => !open)} aria-expanded={suggestionsOpen} style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 9, textAlign: "left", background: "transparent" }}>
              <span style={{ fontSize: 13, color: "var(--evo)" }}>{suggestionsOpen ? "⌄" : "›"}</span>
              <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, fontWeight: 700, color: "var(--evo)", textTransform: "uppercase", letterSpacing: ".06em" }}>Evolution proposals</span>
              <span style={{ fontSize: 12, color: "var(--mid)" }}>{reflectionNudge.proposals.length} suggestion{reflectionNudge.proposals.length === 1 ? "" : "s"}</span>
            </button>
            <button onClick={dismissReflection} title="Close suggestions" aria-label="Close suggestions" style={{ flex: "0 0 auto", padding: "4px 7px", fontSize: 13, color: "var(--lo)", background: "transparent" }}>×</button>
          </div>
          {suggestionsOpen && (
            <>
              <div style={{ margin: "9px 0 8px 22px", fontSize: 12, color: "var(--mid)" }}>Select proposals to prepare an evolution request.</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginLeft: 22, maxHeight: 280, overflowY: "auto", paddingRight: 3 }}>
                {reflectionNudge.proposals.map((proposal, index) => {
                  const selected = selectedSuggestion === index;
                  return (
                    <button key={index} onClick={() => setSelectedSuggestion((current) => current === index ? null : index)} aria-pressed={selected} style={{ padding: "8px 10px", textAlign: "left", border: `1px solid ${selected ? "var(--evo)" : "var(--border)"}`, borderRadius: 8, background: selected ? "var(--bg1)" : "transparent", color: "var(--hi)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                        <span style={{ fontSize: 13, color: selected ? "var(--evo)" : "var(--lo)" }}>{selected ? "✓" : "○"}</span>
                        <span style={{ fontSize: 12.5, fontWeight: 600, flex: 1 }}>{proposal.title}</span>
                        <span style={{ fontFamily: "var(--mono)", fontSize: 9, color: "var(--evo)" }}>{proposal.direction}</span>
                      </div>
                      <div style={{ margin: "4px 0 0 20px", fontSize: 11.5, lineHeight: 1.4, color: "var(--mid)" }}>{proposal.rationale}</div>
                    </button>
                  );
                })}
              </div>
              <button onClick={() => { setEvolutionCommand(selectedSuggestion === null ? "" : reflectionNudge.proposals[selectedSuggestion].command); dismissReflection(); setMainView("evolution"); }} style={{ margin: "10px 0 0 22px", padding: "6px 11px", fontSize: 11.5, fontWeight: 700, borderRadius: 7, background: "var(--evo)", color: "#100a1c" }}>Go to evolution</button>
            </>
          )}
        </div>
      )}

      <Composer />
    </div>
  );
}
