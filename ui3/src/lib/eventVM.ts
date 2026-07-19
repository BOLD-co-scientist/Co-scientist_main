import type { Ev } from "./types";
import { actorColor, actorGlyph, actorLabel, timeOf } from "./format";

// Maps a raw event record to a view model the timeline can render. This is the
// trickiest, most reusable piece of the port — keep it pure and total so new
// `kind`s degrade gracefully to a chat bubble.
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

const SPINE: Record<string, string> = {
  "session.start": "session started",
  "turn.start": "new turn",
  "session.turn_result": "turn complete",
  "session.idle": "idle \u2014 awaiting you",
  "session.end": "session ended",
  "session.crashed": "crashed",
  "session.interrupted": "interrupted",
  "checkpoint.triggered": "checkpoint",
};

export function toVM(e: Ev, prev?: Ev): EventVM {
  const time = timeOf(e.ts);
  const showActor = !prev || prev.actor !== e.actor;
  const s = (k: string) => (e as Record<string, unknown>)[k] as string | undefined;

  if (e.kind in SPINE) {
    const title = e.kind === "checkpoint.triggered" ? s("summary") ?? SPINE[e.kind] : SPINE[e.kind];
    const dot =
      e.kind === "session.crashed"
        ? "var(--err)"
        : e.kind === "session.idle"
          ? "var(--accent)"
          : "var(--lo)";
    return { variant: "spine", id: e.id, time, kind: e.kind, title, dot };
  }

  if (e.kind === "tool.use")
    return {
      variant: "tool",
      id: e.id,
      time,
      actorColor: actorColor(e.actor),
      actorLabel: actorLabel(e.actor),
      glyph: actorGlyph(e.actor),
      tool: s("tool") ?? "tool",
      arg: s("arg") ?? "",
    };

  if (e.kind === "bus.send")
    return {
      variant: "dispatch",
      id: e.id,
      time,
      actorColor: actorColor(e.actor),
      actorLabel: actorLabel(e.actor),
      target: s("target") ?? "subagent",
      body: s("text") ?? s("msg_kind") ?? "",
    };

  if (e.kind === "hitl.pending" || e.kind === "ask")
    return {
      variant: "hitl",
      id: e.id,
      time,
      title: "The agent is waiting for your approval.",
      body: "A gated action is pending \u2014 see the approval panel on the right.",
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

  if (e.kind === "longjob.submitted" || e.kind === "longjob.proposed")
    return { variant: "job", id: e.id, time, title: s("title") ?? "long job" };

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
    return { ...base, tone: "ok", label: "Research complete", body: s("summary") ?? "" };
  return { ...base, body: s("text") ?? s("summary") ?? "" };
}
