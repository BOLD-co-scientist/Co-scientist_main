import { useState } from "react";
import { useApp } from "../state/store";
import HypothesisCards from "./HypothesisCards";

// Hypotheses page — parallel-set flow.
//
// The human states a research goal and gets a few PARALLEL competing hypotheses.
// They either SELECT one, or pick one and say what to do differently — which
// spawns a fresh parallel set derived from that choice. Rounds accumulate so the
// human can step back through the exploration. Backed by real generations
// (api/hypothesis.py — Fable, falling back to Opus on refusal).
//
// The onboarding phase (O1, OnboardingView) runs this same search seeded from
// the whole problem brief BEFORE a session exists; the cards are shared
// (HypothesisCards). This page remains for free-form exploration.
//
// TODO(H1): swap the interim generator for the Google AI co-scientist protocol
// (generate → reflect → rank via Elo tournament → evolve → meta-review). Contract
// + plan: docs/plans/H1-hypothesis-coscientist.md.

const EXAMPLE = "Why do some bacterial populations tolerate antibiotics without genetic resistance?";

export default function HypothesisView() {
  // The hypothesis session lives in the store so it persists across page
  // switches and reloads; the view keeps only transient UI state.
  const { api, hyp: session, setHyp: setSession, startNewSession } = useApp();
  const [goal, setGoal] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    const g = goal.trim();
    if (!g) return;
    setLoading("Generating hypotheses…");
    setError(null);
    try {
      setSession(await api.startHypothesis(g));
    } catch {
      setError("Generation failed. Try rephrasing the goal.");
    } finally {
      setLoading(null);
    }
  };

  const refine = async (parentId: string, feedback: string) => {
    if (!session) return;
    setLoading("Generating a new set from your pick…");
    setError(null);
    try {
      setSession(await api.refineHypothesis(session.id, parentId, feedback || undefined));
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
            {session ? session.goal : "Free-form exploration. To start a session, use New session — the onboarding phase runs this search from your full problem brief."}
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
        <GoalPrompt goal={goal} setGoal={setGoal} onGenerate={generate} loading={loading} onOnboarding={startNewSession} />
      ) : (
        <div style={{ flex: 1, overflowY: "auto", padding: "16px 24px 28px" }}>
          <HypothesisCards session={session} loading={loading} onSelect={(id) => void select(id)} onRefine={(p, f) => void refine(p, f)} />
        </div>
      )}
    </div>
  );
}

function GoalPrompt({ goal, setGoal, onGenerate, loading, onOnboarding }: { goal: string; setGoal: (s: string) => void; onGenerate: () => void; loading: string | null; onOnboarding: () => void }) {
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
        <div style={{ marginTop: 18, fontSize: 12.5, color: "var(--mid)", lineHeight: 1.5 }}>
          Starting a research session? Use <button onClick={onOnboarding} style={{ color: "var(--accent)", fontWeight: 600 }}>New session</button> — the onboarding phase runs this search from your full problem brief (question, data, constraints) and carries the pick into the session.
        </div>
        <div style={{ marginTop: 14, fontSize: 11.5, color: "var(--lo)", lineHeight: 1.5 }}>
          {"⚠"} Interim generator. The full Google AI co-scientist protocol (generate → reflect → rank via tournament → evolve) is planned — see <span style={{ fontFamily: "var(--mono)" }}>docs/plans/H1-hypothesis-coscientist.md</span>.
        </div>
      </div>
    </div>
  );
}
