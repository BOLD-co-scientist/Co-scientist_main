import type { Ev } from "./types";
import { actorColor, actorGlyph, actorLabel, timeOf } from "./format";

// A tool call's outcome (from a tool.result event), paired to its tool.use by
// `ref`, plus whether the timeline is live (so a resultless call can pulse).
export type ToolResult = { summary: string; full: string; isError: boolean };
export type VMCtx = { results?: Map<string, ToolResult>; live?: boolean };

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
      verb: string; // Claude-Code-style action verb: Read / Run / Search / Task …
      arg: string; // the key argument on one line (path, query, script intent)
      full: string; // full input, revealed on expand
      continuation: boolean; // prev row was the same tool+actor → render tighter
      result?: ToolResult; // outcome, once its tool.result has arrived
      running: boolean; // live + no result yet → pulse
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

// SDK MCP tool names arrive as "mcp__<server>__<method>" (e.g.
// "mcp__py_exec__run"). Show just the server ("py_exec"), which is the part a
// human cares about; leave plain tool names (web_search, TodoWrite) untouched.
export function cleanTool(name: string): string {
  const m = /^mcp__([^_].*?)__[^_].*$/.exec(name);
  return m ? m[1] : name;
}

// Low-signal "system" events hidden from the default timeline (revealed with the
// ⌘/Ctrl-O shortcut). These are SDK/harness bookkeeping or duplicates of a
// richer row, NOT agent work. The spine keeps only true session state/boundary
// markers (idle / end / start / crashed / interrupted, evolution + skill).
const NOISE_KINDS = new Set([
  "session.turn_result", // per-stream "[result success]" marker
  "turn.start", // "new turn" — your own message already marks it
  "bus.drain", // internal message-bus bookkeeping
  "checkpoint.triggered", // the pending approval card already represents it
  // deep_research lifecycle: the "Search · query" action row already represents
  // the search; these bracket it with bare spine lines. Hidden as a unit (a
  // start/complete pair must not be split, or the survivor is an orphan).
  "deep_research.started",
  "deep_research.completed",
  "deep_research.fallback",
  "deep_research.cancelled",
]);

export function isSystemNoise(e: Ev): boolean {
  if (NOISE_KINDS.has(e.kind)) return true;
  if (e.kind === "tool.use") {
    // ToolSearch = deferred-schema loading; bus tool.use just duplicates the
    // report bubble / dispatch row it produced.
    const t = cleanTool(((e as Record<string, unknown>).tool as string) ?? "");
    if (t === "ToolSearch" || t === "bus") return true;
    // The deferred placeholder deferred at a checkpoint (renders "mcp__… · {}").
    const raw = (((e as Record<string, unknown>).input_summary as string) ?? "").trim();
    if (raw === "" || raw === "{}") return true;
  }
  return false;
}

function firstLine(text: string, max = 72): string {
  const line = text
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  if (!line) return "";
  const s = line.replace(/\s+/g, " ");
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

function trunc(s: string, max: number): string {
  const t = s.replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max - 1) + "…" : t;
}

// A meaningful one-liner for a python snippet, à la Claude Code showing intent
// rather than boilerplate: prefer a leading intent comment (`# compute X`), else
// the first statement that isn't an import/comment/blank. Fixes the "every row
// says `import pandas as pd`" complaint.
function pySummary(code: string, max = 72): string {
  const lines = code.split("\n").map((l) => l.trim());
  const firstNonBlank = lines.find((l) => l.length > 0) ?? "";
  if (firstNonBlank.startsWith("#")) return trunc(firstNonBlank.replace(/^#+\s*/, ""), max);
  const stmt = lines.find(
    (l) => l.length > 0 && !l.startsWith("#") && !/^(import|from)\b/.test(l),
  );
  return trunc(stmt || firstNonBlank, max);
}

// Map a tool call to a compact { verb, arg } — Claude Code's Read(path) /
// Bash(cmd) / Task(agent) shape. Tools we share with CC get CC's verbs; our own
// tools (memory, deep_research, latex_compile, the bus) get the same visual
// language so the timeline reads as one system. `full` is the raw input for the
// expand view.
function describeTool(toolName: string, raw: string): { verb: string; arg: string; full: string } {
  const clean = cleanTool(toolName);
  let input: Record<string, unknown> | null = null;
  let full = raw;
  if (raw.trim()) {
    try {
      const v = JSON.parse(raw);
      if (v && typeof v === "object" && !Array.isArray(v)) {
        input = v as Record<string, unknown>;
        full = JSON.stringify(v, null, 2);
      } else {
        full = String(v);
      }
    } catch {
      /* raw stays */
    }
  }
  const S = (k: string): string => {
    const v = input?.[k];
    return typeof v === "string" ? v : "";
  };
  const firstStrField = (): string => {
    if (!input) return firstLine(raw);
    const e = Object.entries(input).find(([, v]) => typeof v === "string" && (v as string).trim());
    return e ? firstLine(String(e[1])) : firstLine(JSON.stringify(input));
  };

  switch (clean) {
    case "fs_read":
      return { verb: "Read", arg: S("path") || S("file") || firstStrField(), full };
    case "fs_write_workspace":
      return { verb: "Write", arg: S("path") || S("file") || firstStrField(), full };
    case "py_exec":
      // Prefer the agent-authored intent (semantic, like CC's Bash description);
      // fall back to a heuristic summary of the code.
      return { verb: "Run", arg: S("intent") || pySummary(S("code") || raw), full: S("code") || full };
    case "memory":
      return { verb: "Memory", arg: firstLine(S("query") || S("text") || S("key")) || firstStrField(), full };
    case "deep_research":
    case "web_search":
      return { verb: "Search", arg: firstLine(S("query")) || firstStrField(), full };
    case "latex_compile":
      return { verb: "Compile", arg: S("path") || S("file") || firstStrField(), full };
    case "Task":
      return {
        verb: "Task",
        arg:
          [S("subagent_type"), firstLine(S("description") || S("prompt"))]
            .filter(Boolean)
            .join(" · ") || firstStrField(),
        full,
      };
    default:
      // Built-in tools (Read/Edit/Write/Bash…) we can't add an intent to, but
      // several (Bash, Task) carry a native `description` — prefer it, else the
      // first meaningful field (usually a path).
      return { verb: clean, arg: firstLine(S("description")) || firstStrField(), full };
  }
}

export function toVM(e: Ev, prev?: Ev, ctx?: VMCtx): EventVM {
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

  if (e.kind === "tool.use" || e.kind === "tool.missing") {
    const rawTool = s("tool") ?? "tool";
    const raw = firstStr("input_summary", "arg", "error", "detail") ?? "";
    const { verb, arg, full } = describeTool(rawTool, raw);
    // A run of same-tool, same-actor calls renders as a tighter block (no
    // repeated glyph) so consecutive steps read as one action group.
    const continuation =
      !!prev &&
      prev.kind === e.kind &&
      prev.actor === e.actor &&
      cleanTool((prev as Record<string, unknown>).tool as string ?? "") === cleanTool(rawTool);
    const ref = s("ref");
    const result = ref ? ctx?.results?.get(ref) : undefined;
    // Still-running iff the timeline is live and no tool.result has landed yet.
    const running = e.kind === "tool.use" && !result && !!ctx?.live;
    return {
      variant: "tool",
      id: e.id,
      time,
      actorColor: e.kind === "tool.missing" ? "var(--err)" : actorColor(e.actor),
      actorLabel: actorLabel(e.actor),
      glyph: actorGlyph(e.actor),
      verb: verb + (e.kind === "tool.missing" ? " (missing)" : ""),
      arg,
      full,
      continuation,
      result,
      running,
    };
  }

  if (e.kind === "bus.send") {
    // The text is nested under `payload.text` (the bus wraps the message).
    const payload = (e as Record<string, unknown>).payload;
    const payloadText =
      payload && typeof payload === "object" ? (payload as Record<string, unknown>).text : undefined;
    const busBody = (typeof payloadText === "string" && payloadText.trim() ? payloadText : firstStr("text")) ?? "";
    // A message addressed to the human IS the supervisor's reply — render it as a
    // full response bubble so it's actually readable, not a truncated one-liner.
    if (s("target") === "human" && busBody.trim()) {
      return {
        variant: "bubble",
        id: e.id,
        time,
        showActor,
        actorColor: actorColor(e.actor),
        actorLabel: actorLabel(e.actor),
        glyph: actorGlyph(e.actor),
        tone: "agent",
        body: busBody,
      };
    }
    return {
      variant: "dispatch",
      id: e.id,
      time,
      actorColor: actorColor(e.actor),
      actorLabel: actorLabel(e.actor),
      target: s("target") ?? "subagent",
      body: busBody || (s("msg_kind") ?? ""),
    };
  }

  if (e.kind === "hitl.pending" || e.kind === "ask") {
    // hitl.pending events carry the request `summary` (the actual question /
    // what's being approved) — show it here instead of a generic placeholder so
    // the timeline says WHAT is pending, not just that something is.
    const q = firstStr("summary", "text", "detail");
    return {
      variant: "hitl",
      id: e.id,
      time,
      title: q ?? "The agent is waiting for your approval.",
      body: q
        ? "Approve or reject in the approval panel on the right."
        : "A gated action is pending — see the approval panel on the right.",
    };
  }

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
  // The human's evolution command — echo it as their message bubble so the
  // conversation opens with what was asked, not a bare spine line.
  if (e.kind === "evolution.requested")
    return { ...base, tone: "human", label: "Evolution command", body: s("command") ?? "" };
  if (e.kind === "research.complete")
    return { ...base, tone: "ok", label: "Research complete", body: firstStr("summary", "text") ?? "" };

  const body = firstStr("text", "summary", "note", "detail", "msg_kind", "error", "message");
  // Totality guarantee: a record with no textual field must not render as a
  // blank bubble — fall back to a spine that names the kind.
  if (!body) return { variant: "spine", id: e.id, time, kind: e.kind, title: humanize(e.kind), dot: "var(--lo)" };
  return { ...base, body };
}
