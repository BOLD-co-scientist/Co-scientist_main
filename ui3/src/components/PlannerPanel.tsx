import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useApp } from "../state/store";
import type { BriefSummary, EvoModeInfo, EvoProposalNode, GoalLedger, JudgeInfo, JudgeVerdict, ProposalList } from "../lib/types";

// R19 — the Plan panel of the Evolution tab: the researcher's sequential goals
// (derived from the onboarding brief, reordered by hand), the planner's judged
// proposals (Run / Explore both / Decline / Edit), the Manual/Automatic mode
// toggle, and the judge's calibration. Everything here reads and writes the
// platform API; nothing runs an evolution without a human or judge decision.

export const PLAN_W = 400;
const OPEN = new Set(["proposed", "queued", "implementing"]);
const POLL_MS = 4000;

const STATUS_COLOR: Record<string, string> = {
  proposed: "var(--mid)", queued: "var(--warn)", implementing: "var(--evo)",
  merged: "var(--ok)", declined: "var(--err)", rejected: "var(--err)", ended: "var(--lo)",
};
const SUBGOAL_DOT: Record<string, string> = { done: "var(--ok)", current: "var(--evo)", pending: "var(--lo)", skipped: "var(--lo)" };

function verdictColor(j: JudgeVerdict | null | undefined): string {
  if (!j || j.verdict === "unavailable") return "var(--lo)";
  return j.verdict === "approve" ? "var(--ok)" : "var(--err)";
}

function Badge({ children, color = "var(--mid)", bg }: { children: React.ReactNode; color?: string; bg?: string }) {
  return (
    <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 5, color, background: bg ?? "var(--bg2)", border: "1px solid var(--border)", whiteSpace: "nowrap" }}>
      {children}
    </span>
  );
}

function SectionHead({ title, right }: { title: string; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "14px 0 8px" }}>
      <span style={{ fontSize: 11, fontWeight: 700, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".07em", flex: 1 }}>{title}</span>
      {right}
    </div>
  );
}

const btn = (kind: "primary" | "ghost" | "danger" | "evo", disabled = false): React.CSSProperties => ({
  padding: "6px 10px", borderRadius: 7, fontSize: 11.5, fontWeight: 700, cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1,
  background: kind === "primary" ? "var(--ok)" : kind === "evo" ? "var(--evo)" : kind === "danger" ? "var(--err)" : "var(--bg2)",
  color: kind === "ghost" ? "var(--mid)" : kind === "evo" ? "#100a1c" : "#04160c",
  border: kind === "ghost" ? "1px solid var(--border)" : "none",
});

function JudgeBox({ j }: { j: JudgeVerdict | null }) {
  if (!j) return <div style={{ marginTop: 8, fontSize: 11, color: "var(--lo)" }}>Judge: not run.</div>;
  const color = verdictColor(j);
  if (j.verdict === "unavailable")
    return <div style={{ marginTop: 8, fontSize: 11, color: "var(--lo)" }}>Judge unavailable{j.error ? ` — ${j.error}` : ""}.</div>;
  return (
    <div style={{ marginTop: 8, padding: "8px 10px", borderRadius: 8, border: `1px solid ${color}`, background: "var(--bg2)" }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, color, textTransform: "uppercase", letterSpacing: ".06em" }}>
        Judge · {j.verdict} · {Math.round((j.score ?? 0) * 100)}%{j.served_by ? ` · ${j.served_by}` : ""}
      </div>
      {j.recommendation && <div style={{ marginTop: 4, fontSize: 12.5, color: "var(--hi)", fontWeight: 600 }}>{j.recommendation}</div>}
      {j.why && <div style={{ marginTop: 3, fontSize: 12, color: "var(--mid)", lineHeight: 1.5 }}>{j.why}</div>}
      {j.risks?.length > 0 && <div style={{ marginTop: 4, fontSize: 11, color: "var(--lo)" }}>Risks: {j.risks.join("; ")}</div>}
    </div>
  );
}

export default function PlannerPanel({ open, onOpenDrawer }: { open: boolean; onOpenDrawer: () => void }) {

  const { api, sessionBrief, setEvolutionCommand, setEvolutionProposalId, adoptEvolution, evoRunning } = useApp();
  const [briefs, setBriefs] = useState<BriefSummary[]>([]);
  const [bid, setBid] = useState<string | null>(null);
  const [goals, setGoals] = useState<GoalLedger | null>(null);
  const [plist, setPlist] = useState<ProposalList | null>(null);
  const [mode, setMode] = useState<EvoModeInfo | null>(null);
  const [judge, setJudge] = useState<JudgeInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [compare, setCompare] = useState<string[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [showGoals, setShowGoals] = useState(true);
  const [hints, setHints] = useState("");
  const [hintsDirty, setHintsDirty] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const bidRef = useRef<string | null>(null);
  bidRef.current = bid;

  // Briefs → the problem this panel plans for. Default: the active session's
  // brief, else the most recently launched brief, else the first draft.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api.listBriefs().then((bs) => {
      if (cancelled) return;
      setBriefs(bs);
      setBid((cur) => {
        if (cur && bs.some((b) => b.id === cur)) return cur;
        const active = sessionBrief?.id ? bs.find((b) => b.id === sessionBrief.id) : undefined;
        const launched = bs.filter((b) => b.status === "launched").sort((a, b) => (b.updated ?? "").localeCompare(a.updated ?? ""));
        return active?.id ?? launched[0]?.id ?? bs[0]?.id ?? null;
      });
    }).catch(() => { if (!cancelled) setBriefs([]); });
    return () => { cancelled = true; };
  }, [api, open, sessionBrief?.id]);

  const refresh = useCallback(async () => {
    if (!bid) return;
    const forBid = bid;
    try {
      const [g, p] = await Promise.all([api.getGoals(forBid), api.listProposals(forBid)]);
      // An in-flight poll for the PREVIOUS brief must not overwrite the new
      // one's plan (it would show brief A's proposals under brief B's title).
      if (forBid !== bidRef.current) return;
      setGoals(g);
      setPlist(p);
      setErr(null);
      if (!hintsDirty) setHints(g.approach_hints ?? "");
    } catch (e) {
      if (forBid !== bidRef.current) return;
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [api, bid, hintsDirty]);

  const refreshMode = useCallback(async () => {
    try {
      const [m, j] = await Promise.all([api.evoMode(), api.judgeInfo()]);
      setMode(m);
      setJudge(j);
    } catch { /* ignore */ }
  }, [api]);

  useEffect(() => {
    if (!open) return;
    void refreshMode();
  }, [open, refreshMode]);

  useEffect(() => {
    if (!open || !bid) return;
    void refresh();
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [open, bid, refresh]);

  const withBusy = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setErr(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [refresh]);

  const proposals = plist?.proposals ?? [];

  const openProps = useMemo(() => proposals.filter((p) => OPEN.has(p.status)).slice().sort((a, b) => (b.created ?? "").localeCompare(a.created ?? "")), [proposals]);
  const pastProps = useMemo(() => proposals.filter((p) => !OPEN.has(p.status)).slice().sort((a, b) => (b.updated ?? b.created ?? "").localeCompare(a.updated ?? a.created ?? "")), [proposals]);
  const subgoals = useMemo(() => (goals?.subgoals ?? []).slice().sort((a, b) => a.order - b.order), [goals]);
  // Ids of proposals that were declined, launched or belong to a brief we have
  // since switched away from must drop out of the compare selection, or
  // "Explore both" appears after ticking a single visible card.
  useEffect(() => {
    const selectable = new Set(openProps.filter((p) => p.status === "proposed" || p.status === "queued").map((p) => p.id));
    setCompare((c) => (c.every((id) => selectable.has(id)) ? c : c.filter((id) => selectable.has(id))));
  }, [openProps]);
  const goalText = useCallback((gid: string) => subgoals.find((s) => s.id === gid)?.text ?? gid, [subgoals]);
  // A platform proposal cannot run while platform evolution is disabled; the
  // server answers 501, so ask it rather than guessing from past proposals.
  const platformEnabled = plist?.platform_evolution ?? mode?.platform_evolution ?? false;

  if (!open) return null;

  const toggleMode = () => {
    if (!effectiveMode) return;
    const next = effectiveMode === "manual" ? "automatic" : "manual";
    void withBusy("mode", async () => { setMode(await api.setEvoMode(next)); });
  };

  const move = (gid: string, dir: -1 | 1) => {
    const ids = subgoals.map((s) => s.id);
    const i = ids.indexOf(gid);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    if (!bid) return;
    void withBusy("order", () => api.updateGoals(bid, { order: ids }));
  };

  const run = (p: EvoProposalNode) => {
    void withBusy(p.id, async () => {
      const r = await api.decideProposal(p.id, "pick");
      if (r.launched[0]) { await adoptEvolution(r.launched[0].session_id); onOpenDrawer(); }
    });
  };
  const decline = (p: EvoProposalNode) => {
    // `?? ""` used to turn Cancel/Esc into a confirmed decline.
    const note = window.prompt("Why decline? One line helps the judge learn your taste (optional).");
    if (note === null) return;
    void withBusy(p.id, () => api.decideProposal(p.id, "decline", note));
  };
  const edit = (p: EvoProposalNode) => {
    // Re-seed even when the text is identical to last time (the drawer keys off
    // a change of this value), and carry the proposal so the launch links back.
    setEvolutionCommand("");
    setEvolutionProposalId(p.id);
    window.setTimeout(() => setEvolutionCommand(p.command), 0);
    onOpenDrawer();
  };
  const explore = () => {
    if (compare.length !== 2) return;
    const [a, b] = compare;
    void withBusy("both", async () => {
      const r = await api.decideProposal(a, "both", undefined, b);
      setCompare([]);
      if (r.launched[0]) { await adoptEvolution(r.launched[0].session_id); onOpenDrawer(); }
    });
  };

  const drift = goals?.drift && !goals.drift.answered ? goals.drift : null;
  const judgeOk = mode?.judge.available ?? false;
  // The header is fetched once per open; the 4 s proposals poll also carries the
  // mode, so prefer the fresher value rather than asserting a stale guess.
  const effectiveMode = plist?.mode ?? mode?.mode ?? null;

  return (
    <div style={{ flex: `0 0 ${PLAN_W}px`, width: PLAN_W, borderRight: "1px solid var(--border)", background: "var(--bg1)", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--hi)" }}>Plan</span>
        <select value={bid ?? ""} onChange={(e) => { setBid(e.target.value || null); setHintsDirty(false); }}
          style={{ flex: 1, minWidth: 0, fontSize: 11.5, padding: "5px 7px", background: "var(--bg0)", color: "var(--hi)", border: "1px solid var(--border)", borderRadius: 7 }}>
          {briefs.length === 0 && <option value="">No problem briefs yet</option>}
          {briefs.map((b) => <option key={b.id} value={b.id}>{b.title || "(untitled)"}{b.status === "launched" ? "" : " · draft"}</option>)}
        </select>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "4px 16px 16px", minHeight: 0 }}>
        {/* Mode + judge */}
        <div style={{ marginTop: 10, padding: "9px 11px", borderRadius: 9, border: "1px solid var(--border)", background: "var(--bg2)", display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--hi)" }}>
              {effectiveMode === null ? "Mode unknown" : effectiveMode === "automatic" ? "Automatic — the judge decides" : "Manual — you decide"}
            </div>
            <div style={{ fontSize: 11, color: "var(--lo)", marginTop: 2 }}>
              {mode === null ? "checking the judge…" : judgeOk ? `Judge: ${mode.judge.model}` : "Judge unavailable (no OpenAI key) — recommendations off"}
              {judge?.calibration.gate1.pairs ? ` · agrees with you ${Math.round((judge.calibration.gate1.agreement ?? 0) * 100)}% (${judge.calibration.gate1.pairs})` : ""}
            </div>
          </div>
          <button onClick={toggleMode} disabled={busy === "mode" || (!judgeOk && effectiveMode !== "automatic")} title={judgeOk ? "Switch mode" : "Automatic mode needs the judge"}
            style={btn(effectiveMode === "automatic" ? "ghost" : "evo", busy === "mode" || (!judgeOk && effectiveMode !== "automatic"))}>
            {effectiveMode === "automatic" ? "Switch to manual" : "Go automatic"}
          </button>
        </div>
        {err && <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--err)" }}>{err}</div>}

        {/* Drift question */}
        {drift && (
          <div style={{ marginTop: 12, padding: "10px 12px", borderRadius: 9, border: "1px solid var(--warn)", background: "var(--warn-soft)" }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".06em" }}>Has the plan changed?</div>
            <div style={{ marginTop: 5, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.5 }}>{drift.question}</div>
            {drift.why && <div style={{ marginTop: 3, fontSize: 11.5, color: "var(--mid)" }}>{drift.why}</div>}
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <button style={btn("primary")} onClick={() => bid && void withBusy("drift", () => api.answerDrift(bid, true))}>Yes — update the plan</button>
              <button style={btn("ghost")} onClick={() => bid && void withBusy("drift", () => api.answerDrift(bid, false))}>No — keep it</button>
            </div>
          </div>
        )}

        {/* Goals */}
        <SectionHead title="Sequential goals" right={
          <div style={{ display: "flex", gap: 6 }}>
            <button style={btn("ghost")} onClick={() => setShowGoals((v) => !v)}>{showGoals ? "Hide" : "Show"}</button>
            <button
              style={btn("ghost", !bid || busy === "derive")}
              disabled={!bid || busy === "derive"}
              title={goals?.researcher_ordered ? "Replaces the subgoals with a freshly derived plan (your edits are lost)" : "Derive the plan from the problem brief"}
              onClick={() => {
                if (!bid) return;
                // Once the researcher has ordered the plan the server keeps it
                // unless told otherwise, so a button labelled "Re-derive" that
                // silently refreshed only the wishlist has to ask first.
                const replace = !goals?.researcher_ordered || window.confirm("Replace your ordered subgoals with a freshly derived plan?");
                void withBusy("derive", () => api.deriveGoals(bid, hintsDirty ? hints : undefined, !replace));
              }}
            >
              {busy === "derive" ? "Deriving…" : subgoals.length ? "Re-derive" : "Derive from brief"}
            </button>
          </div>
        } />
        {showGoals && (
          <>
            {goals?.derived?.error && (
              <div style={{ marginBottom: 8, fontSize: 11.5, color: "var(--err)" }}>
                Deriving the plan failed: {goals.derived.error}
              </div>
            )}
            {subgoals.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--lo)", lineHeight: 1.5 }}>No plan yet. Derive the sequential subgoals from the problem brief, then reorder them and mark the one you are on.</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {subgoals.map((sg, i) => (
                  <div key={sg.id} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "7px 9px", borderRadius: 8, border: `1px solid ${sg.status === "current" ? "var(--evo)" : "var(--border)"}`, background: sg.status === "current" ? "var(--evo-soft)" : "transparent" }}>
                    <span style={{ width: 8, height: 8, borderRadius: "50%", background: SUBGOAL_DOT[sg.status] ?? "var(--lo)", marginTop: 5, flex: "0 0 auto" }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12.5, color: sg.status === "done" ? "var(--lo)" : "var(--hi)", textDecoration: sg.status === "done" ? "line-through" : "none", lineHeight: 1.45 }}>
                        <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--lo)", marginRight: 6 }}>{sg.id}</span>{sg.text}
                      </div>
                      {sg.acceptance.length > 0 && <div style={{ marginTop: 2, fontSize: 11, color: "var(--lo)" }}>accept: {sg.acceptance.join("; ")}</div>}
                      {sg.capabilities_needed.length > 0 && <div style={{ marginTop: 2, fontSize: 11, color: "var(--lo)" }}>needs: {sg.capabilities_needed.join(", ")}</div>}
                      {goals?.inferred_current === sg.id && goals.current !== sg.id && <div style={{ marginTop: 2, fontSize: 10.5, color: "var(--warn)" }}>planner thinks you are here</div>}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: "0 0 auto" }}>
                      <button title="Move up" disabled={i === 0} onClick={() => move(sg.id, -1)} style={{ fontSize: 10, color: "var(--lo)", padding: 1, opacity: i === 0 ? 0.3 : 1 }}>▲</button>
                      <button title="Move down" disabled={i === subgoals.length - 1} onClick={() => move(sg.id, 1)} style={{ fontSize: 10, color: "var(--lo)", padding: 1, opacity: i === subgoals.length - 1 ? 0.3 : 1 }}>▼</button>
                      {sg.status !== "current" && <button title="I am working on this now" onClick={() => bid && void withBusy("current", () => api.updateGoals(bid, { current: sg.id }))} style={{ fontSize: 9.5, color: "var(--evo)", padding: 1 }}>now</button>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 11, color: "var(--lo)", marginBottom: 4 }}>Your intuition — what method / pipeline / search space should work</div>
              <textarea value={hints} onChange={(e) => { setHints(e.target.value); setHintsDirty(true); }} rows={2} placeholder="e.g. start from the panel Kd data; dock only the top-5 candidates"
                style={{ width: "100%", resize: "vertical", padding: "7px 9px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12, outline: "none", fontFamily: "var(--ui)" }} />
              {hintsDirty && <button style={{ ...btn("ghost"), marginTop: 4 }} onClick={() => bid && void withBusy("hints", async () => { await api.updateGoals(bid, { approach_hints: hints }); setHintsDirty(false); })}>Save intuition</button>}
            </div>
            {(goals?.capability_wishlist?.length ?? 0) > 0 && (
              <div style={{ marginTop: 10 }}>
                <div style={{ fontSize: 11, color: "var(--lo)", marginBottom: 4 }}>Capabilities the plan needs</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                  {goals!.capability_wishlist.map((w) => {
                    const missing = w.status === "missing";
                    const proposed = w.status.startsWith("proposed");
                    return (
                      <span key={w.name} title={`${w.why}${w.candidates.length ? " · candidates: " + w.candidates.join(", ") : ""} · ${w.status}`}
                        style={{ fontSize: 11, padding: "3px 8px", borderRadius: 999, border: `1px solid ${missing ? "var(--warn)" : proposed ? "var(--evo)" : "var(--ok)"}`, color: missing ? "var(--warn)" : proposed ? "var(--evo)" : "var(--ok)" }}>
                        {w.name}{missing ? " · missing" : proposed ? " · proposed" : " ✓"}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
            {goals?.phase?.evidence && <div style={{ marginTop: 8, fontSize: 11, color: "var(--lo)" }}>Planner's read of the sessions: {goals.phase.evidence}</div>}
          </>
        )}

        {/* Proposals */}
        <SectionHead title="Proposed evolutions" right={
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            {plist?.running && <span style={{ fontSize: 10.5, color: "var(--evo)" }}>● planning</span>}
            {compare.length === 2 && (() => {
              const chosen = proposals.filter((p) => compare.includes(p.id));
              const blocked = chosen.some((p) => p.scope === "platform") && !platformEnabled;
              return (
                <button style={btn("evo", busy !== null || blocked)} disabled={busy !== null || blocked}
                  title={blocked ? "One of these is a platform-scope proposal, which is disabled on this deployment" : "Explore both branches from the same parent"}
                  onClick={explore}>Explore both</button>
              );
            })()}
            <button style={btn("ghost", !bid || !!plist?.running)} disabled={!bid || !!plist?.running} onClick={() => bid && void withBusy("plan", () => api.plan(bid, "manual"))}>{busy === "plan" ? "Starting…" : "Plan now"}</button>
          </div>
        } />
        {plist?.latest_run && (
          <div style={{ fontSize: 10.5, color: "var(--lo)", marginBottom: 6 }}>
            last run {plist.latest_run.created} · {plist.latest_run.trigger}{plist.latest_run.served_by ? ` · ${plist.latest_run.served_by}` : ""}{plist.latest_run.error ? ` · failed: ${plist.latest_run.error}` : ""}
          </div>
        )}
        {openProps.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--lo)", lineHeight: 1.5 }}>
            {bid ? "Nothing proposed right now. The planner runs when a brief launches, when a research turn finishes, and after each merge — or press Plan now." : "Pick a problem brief above."}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {openProps.map((p) => {
              const isOpen = expanded === p.id;
              const canDecide = p.status === "proposed" || p.status === "queued";
              const selected = compare.includes(p.id);
              return (
                <div key={p.id} style={{ borderRadius: 10, border: `1px solid ${selected ? "var(--evo)" : "var(--border)"}`, background: "var(--bg0)", padding: "10px 12px" }}>
                  <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                    {canDecide && (
                      <input type="checkbox" title="Select two to explore both branches" checked={selected} onChange={(e) => setCompare((c) => e.target.checked ? [...c.filter((x) => x !== p.id), p.id].slice(-2) : c.filter((x) => x !== p.id))} style={{ marginTop: 3 }} />
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--hi)", lineHeight: 1.4 }}>{p.title}</div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 5 }}>
                        <Badge color={STATUS_COLOR[p.status]}>{p.status}{p.launched_by === "judge" ? " · by judge" : ""}</Badge>
                        <Badge color={p.scope === "platform" ? "var(--cyan)" : "var(--evo)"}>{p.scope}</Badge>
                        <Badge>{p.direction}</Badge>
                        <Badge>gain {p.expected_gain} · cost {p.cost}</Badge>
                        {p.goal_ids.map((g) => <Badge key={g} color="var(--evo)" bg="var(--evo-soft)">{g}</Badge>)}
                      </div>
                    </div>
                  </div>
                  {p.why_now && <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--mid)" }}><span style={{ color: "var(--lo)" }}>why now · </span>{p.why_now}</div>}
                  <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)", lineHeight: 1.5 }}>{p.rationale}</div>
                  {p.goal_ids.length > 0 && <div style={{ marginTop: 3, fontSize: 11, color: "var(--lo)" }}>serves: {p.goal_ids.map(goalText).join(" · ")}</div>}
                  <JudgeBox j={p.judge} />
                  <button onClick={() => setExpanded(isOpen ? null : p.id)} style={{ marginTop: 6, fontSize: 11, color: "var(--evo)", padding: 0 }}>{isOpen ? "Hide command" : "Show command"}</button>
                  {isOpen && (
                    <pre style={{ marginTop: 6, padding: "8px 10px", background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 8, fontFamily: "var(--mono)", fontSize: 11, color: "var(--mid)", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 220, overflowY: "auto" }}>{p.command}</pre>
                  )}
                  {p.status === "implementing" && p.session_id && (
                    <button style={{ ...btn("ghost"), marginTop: 8 }} onClick={() => { void adoptEvolution(p.session_id!); onOpenDrawer(); }}>Open its conversation</button>
                  )}
                  {p.status === "queued" && <div style={{ marginTop: 6, fontSize: 11, color: "var(--warn)" }}>Approved — waits for the running evolution to finish.</div>}
                  {canDecide && (
                    <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                      {p.status === "proposed" && (() => {
                        const running = evoRunning || !!plist?.evolution_running;
                        const blocked = p.scope === "platform" && !platformEnabled;
                        return (
                          <button
                            style={btn("primary", busy === p.id || blocked)}
                            disabled={busy === p.id || blocked}
                            title={blocked ? "Platform-scope evolutions are disabled on this deployment" : running ? "An evolution is running — this will be queued and start when it finishes" : "Launch the evolution agent with this command"}
                            onClick={() => run(p)}
                          >
                            {running ? "Queue" : "Run"}
                          </button>
                        );
                      })()}
                      <button style={btn("ghost", busy === p.id)} disabled={busy === p.id} onClick={() => edit(p)}>Edit & run</button>
                      <button style={btn("ghost", busy === p.id)} disabled={busy === p.id} onClick={() => decline(p)}>Decline</button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {pastProps.length > 0 && (
          <>
            <SectionHead title={`History (${pastProps.length})`} right={<button style={btn("ghost")} onClick={() => setShowHistory((v) => !v)}>{showHistory ? "Hide" : "Show"}</button>} />
            {showHistory && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {pastProps.map((p) => (
                  <div key={p.id} style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid var(--border)", background: "transparent" }}>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <Badge color={STATUS_COLOR[p.status]}>{p.status}</Badge>
                      <span style={{ fontSize: 12, color: "var(--hi)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.title}</span>
                    </div>
                    <div style={{ marginTop: 3, fontSize: 11, color: "var(--lo)" }}>
                      {p.judge && p.judge.verdict !== "unavailable" ? `judge ${p.judge.verdict} ${Math.round((p.judge.score ?? 0) * 100)}%` : "judge n/a"}
                      {p.human ? ` · you: ${p.human.decision}${p.human.note ? ` — ${p.human.note}` : ""}` : ""}
                      {p.version_id ? ` · version ${p.version_id}` : ""}
                      {p.error ? ` · ${p.error}` : ""}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
