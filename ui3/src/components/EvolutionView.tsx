import { useEffect, useRef, useState } from "react";
import { useApp } from "../state/store";
import { toVM } from "../lib/eventVM";
import EvolutionCanvas from "./EvolutionGraph";
import EventItem from "./EventItem";
import HitlPanel from "./HitlPanel";

// R17 Evolution surface: ONE zoomable version tree (the whole canvas) + ONE
// on-demand conversation drawer that slides in over it. No sub-tabs, no fixed
// sidebar, and — crucially — evolving from a node streams into THIS drawer, never
// into the research session rail (that was the "(no task)" duplicate-surface bug).

const DRAWER_W = 440;

// The single evolution conversation surface: issue a self-modification command
// (optionally branched from a chosen node), watch its lifecycle stream, and
// approve/reject the merge — on the independent evolution channel.
function ConversationDrawer({
  open, base, seed, onClose, clearBase,
}: {
  open: boolean;
  base: { sha: string; label: string } | null;
  seed: string;
  onClose: () => void;
  clearBase: () => void;
}) {
  const { evoEvents, evoPending, evoRunning, startEvolution, answerEvo } = useApp();
  const [cmd, setCmd] = useState("");
  const scroller = useRef<HTMLDivElement | null>(null);

  // A reflection "Go to evolution" suggestion seeds the composer (lifted to the
  // parent so opening the drawer and prefilling stay in sync).
  useEffect(() => { if (seed) setCmd(seed); }, [seed]);

  useEffect(() => {
    if (open) scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [evoEvents.length, open]);

  const submit = () => {
    const c = cmd.trim();
    if (!c || evoRunning) return;
    void startEvolution(c, base?.sha);
    setCmd("");
    clearBase();
  };

  return (
    <div style={{ flex: `0 0 ${open ? DRAWER_W : 0}px`, width: open ? DRAWER_W : 0, transition: "flex-basis .18s ease, width .18s ease", borderLeft: open ? "1px solid var(--border)" : "none", background: "var(--bg1)", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
      <div style={{ width: DRAWER_W, display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", borderBottom: "1px solid var(--border)" }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--hi)", flex: 1 }}>Evolution conversation</span>
          <button onClick={onClose} style={{ color: "var(--lo)", fontSize: 17, lineHeight: 1, padding: 2 }}>×</button>
        </div>

        {base && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 16px", background: "var(--evo-soft)", borderBottom: "1px solid var(--border)", fontSize: 11.5, color: "var(--evo)" }}>
            <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              Branching from <span style={{ fontFamily: "var(--mono)" }}>{base.label}</span>
            </span>
            <button onClick={clearBase} style={{ flex: "0 0 auto", color: "var(--mid)", fontSize: 11 }}>use active tip</button>
          </div>
        )}

        <div ref={scroller} style={{ flex: 1, overflowY: "auto", padding: "16px 18px 10px", minHeight: 0 }}>
          {evoEvents.length === 0 ? (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--lo)", gap: 8, textAlign: "center", padding: 20 }}>
              <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--mid)" }}>Evolve the system</div>
              <div style={{ fontSize: 12, maxWidth: 360, lineHeight: 1.55 }}>
                Describe a change to the harness — a new subagent role, a tool, a prompt tweak, a bug fix, a skill. The evolution agent makes it in an isolated worktree and asks you to approve the merge. A merge only lands once your research sessions are idle.
              </div>
            </div>
          ) : (
            evoEvents.map((e, i) => <EventItem key={e.id} vm={toVM(e, evoEvents[i - 1])} />)
          )}
          {evoPending.length > 0 && (
            <div style={{ marginTop: 8 }}><HitlPanel pending={evoPending} answer={answerEvo} /></div>
          )}
        </div>

        <div style={{ borderTop: "1px solid var(--border)", padding: "12px 16px 16px", display: "flex", gap: 10, alignItems: "flex-end" }}>
          <textarea
            value={cmd}
            onChange={(e) => setCmd(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
            rows={1}
            placeholder={evoRunning ? "An evolution is running…" : "Describe a change…  (Enter to run)"}
            disabled={evoRunning}
            style={{ flex: 1, resize: "none", padding: "10px 13px", background: "var(--bg0)", border: "1px solid var(--border)", borderRadius: 9, color: "var(--hi)", fontSize: 13, outline: "none", opacity: evoRunning ? 0.6 : 1, fontFamily: "var(--ui)" }}
          />
          <button onClick={submit} disabled={evoRunning || !cmd.trim()}
            style={{ padding: "10px 16px", background: "var(--evo)", color: "#100a1c", fontWeight: 700, borderRadius: 9, fontSize: 13, opacity: evoRunning || !cmd.trim() ? 0.5 : 1 }}>
            {evoRunning ? "Running…" : "Evolve"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function EvolutionView() {
  const { evoRunning, evolutionCommand, setEvolutionCommand } = useApp();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [base, setBase] = useState<{ sha: string; label: string } | null>(null);
  const [seed, setSeed] = useState("");

  // A reflection suggestion routes here with a prefilled command → open the
  // drawer and seed it (consume the store field once).
  useEffect(() => {
    if (evolutionCommand) { setSeed(evolutionCommand); setDrawerOpen(true); setEvolutionCommand(""); }
  }, [evolutionCommand, setEvolutionCommand]);

  const evolveFrom = (sha: string, label: string) => {
    setBase({ sha, label });
    setDrawerOpen(true);
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{ padding: "15px 24px 13px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 9 }}>
            Evolution
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, fontWeight: 600, padding: "2px 7px", borderRadius: 5, background: "var(--evo-soft)", color: "var(--evo)" }}>self-modify</span>
            {evoRunning && <span style={{ fontSize: 10.5, color: "var(--evo)" }}>● running</span>}
          </div>
          <div style={{ marginTop: 4, fontSize: 12, color: "var(--mid)" }}>
            Your harness's version tree. Scroll to zoom, drag to pan; click a node to inspect it, switch to it, or evolve a new version from it. Merges wait until research sessions are idle.
          </div>
        </div>
        <button
          onClick={() => { setDrawerOpen((o) => !o); if (drawerOpen) setBase(null); }}
          style={{ flex: "0 0 auto", padding: "8px 14px", borderRadius: 9, fontSize: 12.5, fontWeight: 700, background: drawerOpen ? "var(--bg2)" : "var(--evo)", color: drawerOpen ? "var(--mid)" : "#100a1c", border: drawerOpen ? "1px solid var(--border)" : "none" }}
        >
          {drawerOpen ? "Hide conversation" : "＋ New evolution"}
        </button>
      </div>

      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <EvolutionCanvas onEvolveFrom={evolveFrom} />
        <ConversationDrawer
          open={drawerOpen}
          base={base}
          seed={seed}
          onClose={() => { setDrawerOpen(false); setBase(null); }}
          clearBase={() => setBase(null)}
        />
      </div>
    </div>
  );
}
