import type { Ev } from "./types";
import { actorColor, actorGlyph, actorLabel, timeOf } from "./format";

// Maps a raw event record to a view model the timeline can render. This is the
// trickiest, most reusable piece of the port — keep it pure and total so every
// `kind` in the taxonomy (brief §4.D) renders something legible and no record
// ever degrades to a blank bubble.
export type EventVM =
  | { variant: "spine"; id: string; time: string; kind: string; title: string; dot: string }
  | {
      variant: "bubble";
      id: string;
      time: string;
      showActor: boolean;
      actorColor: string;
      actorLabel: string;
      glyph: string;
      label?: string;
      body: string;
      tone: "human" | "agent" | "ok";
    }
  | {
      variant: "tool";
      id: string;
      time: string;
      actorColor: string;
      actorLabel: string;
      glyph: string;
      tool: string;
      arg: string;
    }
  | { variant: "dispatch"; id: string; time: string; actorColor: string; actorLabel: string; target: string; body: string }
  | { variant: "hitl"; id: string; time: string; title: string; body: string }
  | { variant: "job"; id: string; time: string; title: string };

// Session-lifecycle + checkpoint kinds → the timeline spine.
const SPINE: Record<string, string> = {
  "session.start": "session started",
  "turn.start": "new turn",
  "session.turn_result": "turn complete",
  "session.idle": "idle — awaiting you",
  "session.end": "session ended",
  "session.crashed": "crashed",
  "research.crashed": "research crashed",
  "session.interrupted": "interrupted",
  "bus.drain": "messages drained",
  "checkpoint.triggered": "checkpoint",
  "checkpoint": "checkpoint",
  "checkpoint.resolved": "checkpoint resolved",
  "hitl.ask.resolved": "approval resolved",
};
const ERR_KINDS = new Set(["session.crashed", "research.crashed"]);

// Evolution lifecycle (brief §4.J) — self-modification, rendered on the spine
// with the evolution accent so it reads apart from research activity.
const EVO_LABEL: Record<string, string> = {
  "evolution.start": "evolution started",
  "evolution.proposal": "merge proposed",
  "evolution.tool": "worktree edit",
  "evolution.note": "note",
  "evolution.merged": "merged to main",
  "evolution.rejected": "merge rejected",
  "evolution.auto_reject": "auto-rejected",
  "evolution.error": "evolution error",
  "evolution.end": "evolution ended",
  "evolution_live_check": "live check",
  "evolution_merge": "merge",
};
const EVO_ERR = new Set(["evolution.rejected", "evolution.auto_reject", "evolution.error"]);

// Skill lifecycle (brief §4, Skills group).
const SKILL_LABEL: Record<string, string> = {
  "skill.proposed": "skill proposed",
  "skill_proposal": "skill proposed",
  "skill.approved": "skill approved",
  "skill.rejected": "skill rejected",
  "skill.reflection_error": "skill reflection error",
};

const humanize = (kind: string) => kind.replace(/[._]/g, " ");

export function toVM(e: Ev, prev?: Ev): EventVM {
  const time = timeOf(e.ts);
  const showActor = !prev || prev.actor !== e.actor;
  const s = (k: string) => (e as Record<string, unknown>)[k] as string | undefined;
  // First present, non-empty string among candidate fields.
  const firstStr = (...keys: string[]): string | undefined => {
    for (const k of keys) {
      const v = s(k);
      if (typeof v === "string" && v.trim()) return v;
    }
    return undefined;
  };

  if (e.kind in SPINE) {
    const extra = firstStr("summary", "note", "detail");
    const title = extra ? `${SPINE[e.kind]} · ${extra}` : SPINE[e.kind];
    const dot = ERR_KINDS.has(e.kind)
      ? "var(--err)"
      : e.kind === "session.idle"
        ? "var(--accent)"
        : "var(--lo)";
    return { variant: "spine", id: e.id, time, kind: e.kind, title, dot };
  }

  if (e.kind === "tool.use" || e.kind === "tool.missing")
    return {
      variant: "tool",
      id: e.id,
      time,
      actorColor: e.kind === "tool.missing" ? "var(--err)" : actorColor(e.actor),
      actorLabel: actorLabel(e.actor),
      glyph: actorGlyph(e.actor),
      tool: (s("tool") ?? "tool") + (e.kind === "tool.missing" ? " (missing)" : ""),
      arg: firstStr("arg", "error", "detail") ?? "",
    };

  if (e.kind === "bus.send")
    return {
      variant: "dispatch",
      id: e.id,
      time,
      actorColor: actorColor(e.actor),
      actorLabel: actorLabel(e.actor),
      target: s("target") ?? "subagent",
      body: firstStr("text", "msg_kind") ?? "",
    };

  if (e.kind === "hitl.pending" || e.kind === "ask")
    return {
      variant: "hitl",
      id: e.id,
      time,
      title: "The agent is waiting for your approval.",
      body: "A gated action is pending — see the approval panel on the right.",
    };

  if (e.kind === "hitl.answer") {
    const decision = s("decision");
    const decided =
      decision === "approve"
        ? "You approved the request."
        : decision === "reject"
          ? "You rejected the request."
          : `Decision recorded: ${decision ?? "answered"}.`;
    return { variant: "spine", id: e.id, time, kind: e.kind, title: decided, dot: decision === "reject" ? "var(--err)" : "var(--ok)" };
  }

  // All long-job kinds → a job chip.
  if (e.kind.startsWith("longjob") ) {
    const state = e.kind.split(/[._]/).pop();
    const title = firstStr("title", "job", "summary") ?? "long job";
    return { variant: "job", id: e.id, time, title: state && state !== "longjob" ? `${title} · ${state}` : title };
  }

  // Evolution + skill lifecycle → spine with the evolution accent.
  if (e.kind in EVO_LABEL) {
    const extra = firstStr("summary", "note", "detail", "text", "error");
    const title = extra ? `${EVO_LABEL[e.kind]} · ${extra}` : EVO_LABEL[e.kind];
    return { variant: "spine", id: e.id, time, kind: e.kind, title, dot: EVO_ERR.has(e.kind) ? "var(--err)" : "var(--evo)" };
  }
  if (e.kind in SKILL_LABEL) {
    const extra = firstStr("summary", "note", "detail", "text", "error");
    const title = extra ? `${SKILL_LABEL[e.kind]} · ${extra}` : SKILL_LABEL[e.kind];
    return { variant: "spine", id: e.id, time, kind: e.kind, title, dot: e.kind === "skill.approved" ? "var(--ok)" : e.kind.includes("error") || e.kind.includes("rejected") ? "var(--err)" : "var(--evo)" };
  }

  // Default: a chat bubble (report / message / research.* / anything textual).
  const base = {
    variant: "bubble" as const,
    id: e.id,
    time,
    showActor,
    actorColor: actorColor(e.actor),
    actorLabel: actorLabel(e.actor),
    glyph: actorGlyph(e.actor),
    tone: (e.actor === "human" ? "human" : "agent") as "human" | "agent" | "ok",
  };
  if (e.kind === "research.requested")
    return { ...base, label: "Research task", body: s("task") ?? "" };
  if (e.kind === "research.complete")
    return { ...base, tone: "ok", label: "Research complete", body: firstStr("summary", "text") ?? "" };

  const body = firstStr("text", "summary", "note", "detail", "msg_kind", "error", "message");
  // Totality guarantee: a record with no textual field must not render as a
  // blank bubble — fall back to a spine that names the kind.
  if (!body) return { variant: "spine", id: e.id, time, kind: e.kind, title: humanize(e.kind), dot: "var(--lo)" };
  return { ...base, body };
}
