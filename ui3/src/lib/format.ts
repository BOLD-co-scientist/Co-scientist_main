import type { SessionSummary } from "./types";

// ---- actor identity ----
const ACTOR_COLORS: Record<string, string> = {
  human: "var(--accent)",
  supervisor: "var(--cyan)",
  data_analyst: "var(--grn)",
  generalist_researcher: "var(--lav)",
  system: "var(--lo)",
  evolution: "var(--evo)",
  judge: "var(--warn)",
};
const ACTOR_GLYPHS: Record<string, string> = {
  human: "U",
  supervisor: "SV",
  data_analyst: "DA",
  generalist_researcher: "GR",
  system: "\u2022\u2022",
  evolution: "EV",
  judge: "JD",
};

export const actorColor = (a: string): string => ACTOR_COLORS[a] ?? "var(--mid)";
export const actorGlyph = (a: string): string => ACTOR_GLYPHS[a] ?? "?";
export const actorLabel = (a: string): string => (a === "human" ? "You" : a === "judge" ? "Judge" : a);

// ---- files ----
export function fmtSize(b: number): string {
  if (b >= 1_048_576) return (b / 1_048_576).toFixed(1) + " MB";
  if (b >= 1024) return (b / 1024).toFixed(0) + " KB";
  return b + " B";
}
export function extOf(name: string): string {
  const p = name.split(".");
  return p.length > 1 ? p.pop()!.toUpperCase() : "FILE";
}
const EXT_COLORS: Record<string, string> = {
  MD: "var(--accent)",
  PNG: "var(--lav)",
  CSV: "var(--grn)",
  SDF: "var(--cyan)",
  PDF: "var(--err)",
  XLSX: "var(--grn)",
};
export const extColor = (e: string): string => EXT_COLORS[e] ?? "var(--mid)";

// ---- timestamps: server-local "YYYY-MM-DD HH:MM:SS" (brief §7.7) ----
/** Parse as LOCAL time — never assume UTC / no trailing Z. */
export function parseTs(ts: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(ts ?? "");
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
}
/** Just the HH:MM:SS portion, for the dense timeline. */
export function timeOf(ts: string): string {
  const m = /(\d{2}:\d{2}:\d{2})/.exec(ts ?? "");
  return m ? m[1] : ts ?? "";
}

// ---- session status → label + colors ----
export interface StatusView {
  label: string;
  color: string;
  dot: string;
  text: string;
}
export function statusOf(s: SessionSummary): StatusView {
  const last = s.last_kind;
  if (s.status === "crashed" || last === "session.crashed")
    return { label: "crashed", color: "var(--err)", dot: "var(--err)", text: "Session crashed" };
  if (s.status === "complete" || last === "research.complete" || last === "session.end")
    return { label: "done", color: "var(--mid)", dot: "var(--mid)", text: "Complete" };
  if (s.blocked)
    return { label: "needs you", color: "var(--warn)", dot: "var(--warn)", text: "Awaiting your approval" };
  if (s.running)
    return { label: "running", color: "var(--ok)", dot: "var(--ok)", text: "Working\u2026" };
  return { label: "idle", color: "var(--accent)", dot: "var(--accent)", text: "Idle \u2014 send a message" };
}
