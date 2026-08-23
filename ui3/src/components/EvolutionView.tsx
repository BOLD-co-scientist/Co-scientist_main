import { useState } from "react";
import { useApp } from "../state/store";
import { toVM } from "../lib/eventVM";
import EvolutionGraph from "./EvolutionGraph";
import EventItem from "./EventItem";
import HitlPanel from "./HitlPanel";

// The live evolution conversation: issue a self-modification command, watch its
// lifecycle stream, approve/reject the merge — all on the independent evolution
// channel (never disturbs a running research session).
function EvoConversation() {
  const { evoEvents, evoPending, evoRunning, startEvolution, answerEvo } = useApp();
  const [cmd, setCmd] = useState("");

  const submit = () => {
    const c = cmd.trim();
    if (!c || evoRunning) return;
    void startEvolution(c);
    setCmd("");
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ flex: 1, overflowY: "auto", padding: "18px 24px 12px" }}>
        {evoEvents.length === 0 ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--lo)", gap: 8, textAlign: "center", padding: 24 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--mid)" }}>Evolve the system</div>
            <div style={{ fontSize: 12.5, maxWidth: 460, lineHeight: 1.55 }}>
              Describe a change to the harness — a new subagent role, a tool, a
              prompt tweak, a bug fix. The evolution agent makes it in an isolated
              worktree and asks you to approve the merge. A merge only lands once
              all your research sessions are idle.
            </div>
          </div>
        ) : (
          evoEvents.map((e, i) => <EventItem key={e.id} vm={toVM(e, evoEvents[i - 1])} />)
        )}
        {evoPending.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <HitlPanel pending={evoPending} answer={answerEvo} />
          </div>
        )}
        <div style={{ height: 6 }} />
      </div>

      <div style={{ borderTop: "1px solid var(--border)", padding: "12px 24px 16px", display: "flex", gap: 10, alignItems: "flex-end" }}>
        <textarea
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder={evoRunning ? "An evolution is running — wait for it to finish…" : "Describe a change to the system…  (Enter to run)"}
          disabled={evoRunning}
          style={{ flex: 1, resize: "none", padding: "10px 13px", background: "var(--bg1)", border: "1px solid var(--border)", borderRadius: 9, color: "var(--hi)", fontSize: 13, outline: "none", opacity: evoRunning ? 0.6 : 1, fontFamily: "var(--ui)" }}
        />
        <button
          onClick={submit}
          disabled={evoRunning || !cmd.trim()}
          style={{ padding: "10px 18px", background: "var(--evo)", color: "#100a1c", fontWeight: 700, borderRadius: 9, fontSize: 13, opacity: evoRunning || !cmd.trim() ? 0.5 : 1 }}
        >
          {evoRunning ? "Running…" : "Evolve"}
        </button>
      </div>
    </div>
  );
}

export default function EvolutionView() {
  const [tab, setTab] = useState<"conversation" | "graph">("conversation");
  const seg = (on: boolean) =>
    ({ padding: "6px 13px", borderRadius: 7, fontSize: 12.5, fontWeight: 600, color: on ? "var(--hi)" : "var(--mid)", background: on ? "var(--bg0)" : "transparent" }) as const;

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
            Evolution
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--evo-soft)", color: "var(--evo)" }}>self-modify</span>
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
            The system rewriting its own capabilities. Each change opens an isolated
            worktree and merges into main through your approval — merges wait until
            research sessions are idle, so they never disturb a run in progress.
          </div>
        </div>
        <div style={{ display: "flex", gap: 3, padding: 3, background: "var(--bg2)", border: "1px solid var(--border)", borderRadius: 10, flex: "0 0 auto" }}>
          <button style={seg(tab === "conversation")} onClick={() => setTab("conversation")}>
            Conversation
          </button>
          <button style={seg(tab === "graph")} onClick={() => setTab("graph")}>
            Lineage
          </button>
        </div>
      </div>
      {tab === "conversation" ? <EvoConversation /> : <EvolutionGraph />}
    </div>
  );
}
