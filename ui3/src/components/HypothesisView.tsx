import { useMemo, useState } from "react";
import { useApp } from "../state/store";
import type { HypSession } from "../lib/types";

// Hypotheses page — parallel-set flow.
//
// The human states a research goal and gets a few PARALLEL competing hypotheses.
// They either SELECT one, or pick one and say what to do differently — which
// spawns a fresh parallel set derived from that choice. Rounds accumulate so the
// human can step back through the exploration. Backed by real generations
// (api/hypothesis.py — Fable, falling back to Opus on refusal).
//
// TODO(H1): swap the interim generator for the Google AI co-scientist protocol
// (generate → reflect → rank via Elo tournament → evolve → meta-review). Contract
// + plan: docs/plans/H1-hypothesis-coscientist.md.

const EXAMPLE = "Why do some bacterial populations tolerate antibiotics without genetic resistance?";

export default function HypothesisView() {
  // The hypothesis session lives in the store so it persists across page
  // switches and reloads; the view keeps only transient UI state.
  const { api, hyp: session, setHyp: setSession } = useApp();
  const [goal, setGoal] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewRound, setViewRound] = useState(0);
  const [refiningId, setRefiningId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");

  const round = session?.rounds[viewRound] ?? null;
  const parentCard = useMemo(() => {
    if (!session || !round || round.parent_id == null) return null;
    for (const r of session.rounds) {
      const p = r.hypotheses.find((h) => h.id === round.parent_id);
      if (p) return p;
    }
    return null;
  }, [session, round]);

  const generate = async () => {
    const g = goal.trim();
    if (!g) return;
    setLoading("Generating hypotheses…");
    setError(null);
    try {
      const rec = await api.startHypothesis(g);
      setSession(rec);
      setViewRound(0);
    } catch {
      setError("Generation failed. Try rephrasing the goal.");
    } finally {
      setLoading(null);
    }
  };

  const refine = async (parentId: string) => {
    if (!session) return;
    setLoading("Generating a new set from your pick…");
    setError(null);
    try {
      const rec = await api.refineHypothesis(session.id, parentId, feedback.trim() || undefined);
      setSession(rec);
      setViewRound(rec.rounds.length - 1);
      setRefiningId(null);
      setFeedback("");
    } catch {
      setError("Refinement failed. Try different feedback.");
    } finally {
      setLoading(null);
    }
  };

  const select = async (hypId: string) => {
    if (!session) return;
    try {
      setSession(await api.selectHypothesis(session.id, hypId));
    } catch {
      setError("Could not record the selection.");
    }
  };

  const reset = () => {
    setSession(null);
    setGoal("");
    setError(null);
    setViewRound(0);
    setRefiningId(null);
    setFeedback("");
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      {/* header */}
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
            Hypotheses
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--accent-soft)", color: "var(--accent)" }}>you steer</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--bg2)", color: "var(--lo)" }} title="Interim generator; the Google co-scientist protocol is planned (docs/plans/H1)">interim</span>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {session ? session.goal : "State a goal; the AI proposes parallel hypotheses. Pick one, or pick one and say what to do differently."}
          </div>
        </div>
        {session && (
          <button onClick={reset} style={{ padding: "7px 14px", border: "1px solid var(--border)", color: "var(--mid)", borderRadius: 8, fontSize: 12.5, fontWeight: 600, flex: "0 0 auto" }}>
            New goal
          </button>
        )}
      </div>

      {error && (
        <div style={{ margin: "12px 24px 0", padding: "9px 13px", background: "var(--err-soft)", border: "1px solid var(--err)", borderRadius: 9, fontSize: 12.5, color: "var(--err)" }}>
          {error}
        </div>
      )}

      {/* body */}
      {!session ? (
        <GoalPrompt goal={goal} setGoal={setGoal} onGenerate={generate} loading={loading} />
      ) : (
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 24px 28px" }}>
          {/* round nav */}
          {session.rounds.length > 1 && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
              {session.rounds.map((r, i) => (
                <button
                  key={i}
                  onClick={() => { setViewRound(i); setRefiningId(null); }}
                  style={{ fontFamily: "var(--mono)", fontSize: 11, padding: "4px 10px", borderRadius: 7, border: `1px solid ${i === viewRound ? "var(--accent)" : "var(--border)"}`, background: i === viewRound ? "var(--accent-soft)" : "transparent", color: i === viewRound ? "var(--accent)" : "var(--mid)" }}
                >
                  {i === 0 ? "Round 1 · goal" : `Round ${i + 1}`}
                </button>
              ))}
            </div>
          )}

          {/* lineage for refined rounds */}
          {parentCard && (
            <div style={{ marginBottom: 14, padding: "10px 13px", background: "var(--bg2)", border: "1px solid var(--border)", borderLeft: "2px solid var(--accent)", borderRadius: "0 9px 9px 0" }}>
              <div style={{ fontSize: 10.5, fontWeight: 600, color: "var(--lo)", textTransform: "uppercase", letterSpacing: ".06em" }}>Refined from</div>
              <div style={{ marginTop: 3, fontSize: 12.5, color: "var(--hi)", lineHeight: 1.4 }}>{parentCard.statement}</div>
              {round?.feedback && (
                <div style={{ marginTop: 5, fontSize: 12, color: "var(--mid)" }}>
                  <span style={{ color: "var(--lo)" }}>your steer: </span>{round.feedback}
                </div>
              )}
            </div>
          )}

          {/* served-by note */}
          {round && (
            <div style={{ marginBottom: 10, fontSize: 11, color: "var(--lo)", fontFamily: "var(--mono)" }}>
              {round.hypotheses.length} parallel hypotheses · generated by {round.served_by}
            </div>
          )}

          {/* parallel cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 12, opacity: loading ? 0.5 : 1, transition: "opacity .2s", pointerEvents: loading ? "none" : "auto" }}>
            {round?.hypotheses.map((h, i) => {
              const selected = session.selected_id === h.id;
              const open = refiningId === h.id;
              return (
                <div key={h.id} style={{ border: `1.5px solid ${selected ? "var(--ok)" : "var(--border)"}`, background: selected ? "var(--ok-soft)" : "var(--bg1)", borderRadius: 12, padding: "14px 15px", display: "flex", flexDirection: "column", gap: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 700, color: "var(--accent)" }}>{String.fromCharCode(65 + i)}</span>
                    {selected && <span style={{ fontFamily: "var(--mono)", fontSize: 9.5, fontWeight: 700, padding: "1px 6px", borderRadius: 5, background: "var(--ok)", color: "#04160c", textTransform: "uppercase", letterSpacing: ".05em" }}>selected</span>}
                  </div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: "var(--hi)", lineHeight: 1.35 }}>{h.statement}</div>
                  {h.rationale && <div style={{ fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>{h.rationale}</div>}
                  <div style={{ flex: 1 }} />
                  <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
                    <button onClick={() => void select(h.id)} style={{ flex: 1, padding: "8px 10px", background: selected ? "var(--bg2)" : "var(--ok)", color: selected ? "var(--mid)" : "#04160c", border: selected ? "1px solid var(--border)" : "none", fontWeight: 700, borderRadius: 8, fontSize: 12.5 }}>
                      {selected ? "Selected ✓" : "Select"}
                    </button>
                    <button onClick={() => { setRefiningId(open ? null : h.id); setFeedback(""); }} style={{ flex: 1, padding: "8px 10px", background: open ? "var(--accent-soft)" : "var(--bg2)", color: "var(--accent)", border: `1px solid ${open ? "var(--accent)" : "var(--border)"}`, fontWeight: 600, borderRadius: 8, fontSize: 12.5 }}>
                      Explore from this →
                    </button>
                  </div>
                  {open && (
                    <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 7 }}>
                      <textarea
                        autoFocus
                        value={feedback}
                        onChange={(e) => setFeedback(e.target.value)}
                        placeholder="What should the next set do differently? (optional)"
                        rows={2}
                        style={{ resize: "none", padding: "8px 10px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--hi)", fontSize: 12.5, outline: "none" }}
                      />
                      <button onClick={() => void refine(h.id)} style={{ padding: "8px 10px", background: "var(--accent)", color: "#06121c", fontWeight: 700, borderRadius: 8, fontSize: 12.5 }}>
                        Generate a new set from this
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {loading && (
            <div style={{ marginTop: 16, display: "flex", alignItems: "center", gap: 10, color: "var(--mid)", fontSize: 13 }}>
              <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", animation: "pulse 1.4s infinite" }} />
              {loading} <span style={{ color: "var(--lo)", fontSize: 12 }}>(can take up to a minute)</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function GoalPrompt({ goal, setGoal, onGenerate, loading }: { goal: string; setGoal: (s: string) => void; onGenerate: () => void; loading: string | null }) {
  return (
    <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div style={{ maxWidth: 620, width: "100%" }}>
        <div style={{ fontSize: 17, fontWeight: 600, color: "var(--hi)" }}>What's your research goal?</div>
        <div style={{ marginTop: 6, fontSize: 13, color: "var(--mid)", lineHeight: 1.5 }}>
          The AI proposes a few <b style={{ color: "var(--hi)", fontWeight: 600 }}>parallel</b> hypotheses that attack it from different angles. Select the one you like, or pick one and tell the AI what to do differently — it generates a fresh set from your choice.
        </div>
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onGenerate(); }}
          placeholder={`e.g. ${EXAMPLE}`}
          rows={3}
          disabled={!!loading}
          style={{ width: "100%", marginTop: 16, resize: "vertical", padding: "12px 14px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 11, color: "var(--hi)", fontSize: 14, outline: "none", lineHeight: 1.5 }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
          <button
            onClick={onGenerate}
            disabled={!goal.trim() || !!loading}
            style={{ padding: "11px 20px", background: goal.trim() && !loading ? "var(--accent)" : "var(--bg2)", color: goal.trim() && !loading ? "#06121c" : "var(--lo)", fontWeight: 700, borderRadius: 9, fontSize: 13.5 }}
          >
            {loading ? "Generating…" : "Generate hypotheses"}
          </button>
          {!goal && (
            <button onClick={() => setGoal(EXAMPLE)} style={{ fontSize: 12.5, color: "var(--accent)", fontWeight: 600 }}>
              try an example
            </button>
          )}
          {loading && (
            <span style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--mid)", fontSize: 12.5 }}>
              <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", animation: "pulse 1.4s infinite" }} />
              {loading} <span style={{ color: "var(--lo)" }}>(up to a minute)</span>
            </span>
          )}
        </div>
        <div style={{ marginTop: 22, fontSize: 11.5, color: "var(--lo)", lineHeight: 1.5 }}>
          {"⚠"} Interim generator. The full Google AI co-scientist protocol (generate → reflect → rank via tournament → evolve) is planned — see <span style={{ fontFamily: "var(--mono)" }}>docs/plans/H1-hypothesis-coscientist.md</span>.
        </div>
      </div>
    </div>
  );
}
