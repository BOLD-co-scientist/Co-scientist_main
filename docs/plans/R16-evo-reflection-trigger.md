# R16 — Evolution reflection & suggestion trigger (human-gated RHI loop)

**Status:** Planned
**Owner:** anjunming1202
**Started:** —
**Done when:** When a research session completes, a reflection pass runs, records a per-session reflection, and — only if it found something worth changing — surfaces a slim nudge card in the research conversation and a set of concrete evolution suggestions (each with a ready-to-run command) in the Evolution tab; the human picks one, which pre-fills the evolution composer (editable, not auto-run) and flows into the existing evolution mechanism unchanged.

## Why

This is Rainforest-Wang's assigned feedback **item 4** ("自动启动 evo 的流程"): (4.1) after research finishes, remind the human about evolving the system; (4.2) before an evolution run, have the agent propose suggestions of its own first.

Framed against the recursive-self-improvement loop family (STOP / ADAS / Darwin-Gödel Machine / Reflexion): our **current evolution is only the modifier + safe-integration arm** — a coding agent that rewrites the repo in a worktree, runs tests, and merges through HITL. The direction comes 100% from a human-typed command. What's missing is the **reflect → propose arm**. R16 adds it, but keeps the human in the evaluator/selector seat instead of an automated fitness function. Net: a **human-gated RHI loop**.

```
run (research session)
  → reflect (R16 step A — NEW)      read the session + harness state → a diagnosis
  → propose (R16 step B — NEW)      derive ≤N candidate evolutions, each with an authored command
  → HUMAN selects (gate 1 — NEW)    reminder card / evo-tab suggestion cards
  → integrate (existing evolution)  worktree → propose_merge → HUMAN approves (gate 2) → ff-merge
  → recurse (next session on the evolved harness)
```

Two human gates are deliberate (HITL paramount): gate 1 = "evolve in this direction at all?", gate 2 = "is the concrete change correct?".

## Design decisions (locked in discussion 2026-08-24)

- **Two steps, not one "suggestion" step.**
  - **A — Reflect (diagnosis, durable):** read one session's trajectory + harness inventory (roles/tools/prompts) against an *open-ended* direction menu → a reflection record. Persisted at `state/sessions/<sid>/reflection.json`. This is the **gate**: empty diagnosis ⇒ no reminder. Also the **bridge to R2** (same per-session read can later emit research-lessons too).
  - **B — Propose (prescription):** from A, derive ≤N concrete candidate evolutions, each = `{title, direction, rationale (cites A), command}`. The **command is the authored `{{COMMAND}}` string** fed to the existing evolution entry — NOT a new system prompt. Well-specified (which files, what change, acceptance) so the modifier does a good job.
  - Implementation may be **one LLM call** emitting `{reflection, proposals[]}`, but reflection is persisted independently.
- **Direction menu is open-ended, examples not a closed taxonomy** (objective #3): agents / workflow / tools / methodology / memory. Must NOT collapse to "add a missing tool" (that's just a skill). Prompt is evolvable — evolution can later deepen the reflection methodology.
- **Trigger = at session completion** (`research.complete`, reason=finished), run A+B. A cheap **heuristic pre-skip** only skips trivial turns (no real work / no deliverable) to save a call — it does NOT judge friction-vs-not; the LLM reflection does the judging across all directions.
- **Presentation (agreed):**
  - Research conversation: a **slim, dismissible nudge card** at the end — "this run surfaced N ways to evolve → open Evolution". Entry point only, no details.
  - Evolution tab conversation: the **suggestion cards** (title + one-line rationale). Click "Use this" → **pre-fills the evo composer (editable), does NOT auto-run**. Human can ignore and type their own.
  - **No evo-tab status dot** (explicitly deferred).
- **Existing evolution mechanism unchanged.** Chosen command → `POST /evolution/commands`. Optional: attach `source_session` so the modifier can `bash_ro` that session's events instead of re-deriving.
- **Cost:** one reflection call per non-trivial completed turn, medium tier (needs reasoning, can't be the weakest model or it always suggests tools). Under the CLAUDE.md $5 self-verify bar.
- **Out of scope now:** cross-session aggregation of harness signals ("3 sessions hit the same friction → stronger signal"); the R2 global lessons buffer itself; multi-turn evolution plan-mode.

## Steps

### Backend — reflection node (A + B)

- [ ] 1. Write `prompts/evo_reflect.md`: inputs `{task, outcome, trajectory (numbered events), harness_inventory (roles+tools+prompt names)}`; output JSON `{reflection: str, proposals: [{title, direction, rationale, command}]}` with hard rules — open-ended direction menu as *examples*, ≤5 proposals, each command well-specified and self-contained, return `{"reflection": "...", "proposals": []}` when nothing is worth evolving (clean/trivial run).
- [ ] 2. Create the reflection runner (thin, read-only, subprocess) — e.g. `research/reflect.py` (`python -m research.reflect --session <sid>`): loads the session's `events.jsonl` + builds the harness inventory (read `roles/`, `tools/`, `prompts/`), renders `evo_reflect.md`, makes ONE SDK/model call, writes `state/sessions/<sid>/reflection.json`, and emits a `reflection.ready` event (with `proposal_count`) on the session stream. Emits nothing / count 0 when proposals empty.
- [ ] 3. Add the cheap heuristic pre-skip in the runner: if the completed turn did no real work (below a small threshold of tool calls / no results file), write an empty reflection and skip the LLM call. Knob in `scaffold/settings.py` (e.g. `EVO_REFLECT_MIN_TOOLCALLS`), and a master toggle `COSCIENTIST_EVO_REFLECT` (default on).
- [ ] 4. Kick the runner from the API when a research subprocess exits cleanly — in `api/server.py::_monitor_runtime`, on a completion (not cancel/crash), spawn `research.reflect` as a detached subprocess so it never delays the research turn and stays a separate node from the modifier.

### Backend — API surface

- [ ] 5. `GET /sessions/{sid}/reflection` → the parsed `reflection.json` (proposals + reflection text), 404/empty when none. Powers both the nudge card and the evo-tab suggestion cards.
- [ ] 6. `POST /sessions/{sid}/reflect` (optional, on-demand re-run) → re-runs the runner; lets the human refresh/force suggestions from the evo tab even if the auto pass was skipped.
- [ ] 7. Thread an optional `source_session` through `POST /evolution/commands` → `_spawn_evolution` → the evolution command payload, so a suggestion carries its origin (informational + lets the modifier `bash_ro` it). Backward-compatible (optional field).

### Frontend (ui3)

- [ ] 8. `api.ts`: `getReflection(sid)` + `reflect(sid)`; types for `Reflection` / `EvoProposal`. `reflection.ready` handled in the event stream (bumps a per-session "has suggestions" flag in the store).
- [ ] 9. Research conversation: a slim dismissible **nudge card** rendered at the end when the active session has `proposal_count > 0` (dismissal persisted in localStorage per sid). Clicking it switches `mainView` to the Evolution tab.
- [ ] 10. Evolution tab conversation: when the (most-recent, or a picked) session has proposals, render **suggestion cards** above the composer — title + one-line rationale + "Use this" that fills the composer with the proposal's `command` (editable). Typing your own still works; cards are optional.
- [ ] 11. `startEvolution` passes `source_session` when the command came from a suggestion card.

## Files touched

- `prompts/evo_reflect.md` (new)
- `research/reflect.py` (new) — reflection runner
- `scaffold/settings.py` — `EVO_REFLECT_MIN_TOOLCALLS`, `COSCIENTIST_EVO_REFLECT`
- `api/server.py` — kick runner in `_monitor_runtime`; `GET /sessions/{sid}/reflection`; `POST /sessions/{sid}/reflect`; optional `source_session` on evolution command
- `api/schemas.py` — reflection / proposal response models; `source_session` field
- `ui3/src/lib/api.ts`, `ui3/src/lib/types.ts` — reflection API + types + `reflection.ready`
- `ui3/src/state/store.tsx` — per-session `proposal_count`, dismissal state, `getReflection` wiring, `startEvolution(source_session)`
- `ui3/src/components/` — nudge card in the research conversation; suggestion cards in `EvolutionView`

## Verification

Per CLAUDE.md (curl-driven backend pass, not just unit tests):
1. Run a small research session to completion (the LTEM task). Confirm `state/sessions/<sid>/reflection.json` is written and a `reflection.ready` event appears in `events.jsonl`.
2. Force the empty path: a trivial/failed session → reflection with `proposals: []`, no nudge card.
3. `GET /sessions/<sid>/reflection` returns the proposals; `POST /sessions/<sid>/reflect` re-runs.
4. UI: nudge card shows in the research conversation; clicking → Evolution tab shows suggestion cards; "Use this" fills the composer without auto-running; issuing it flows through the existing evolution mechanism and reaches `evolution_merge` HITL.
5. Cost stays under $5 for the pass.

## Risks / open questions

- **Multi-turn re-reflection:** `research.complete` fires per completed turn, so a long session reflects several times. v1 overwrites `reflection.json` with the latest and the card is dismissible; the heuristic pre-skip limits waste. Revisit if it's noisy/costly.
- **Where the LLM call lives / which model:** subprocess runner keeps the API lean and matches the subprocess-per-task pattern; model tier must be strong enough to reason across directions (not the weakest). Settings-driven.
- **Reflection quality:** if suggestions are shallow ("add a tool" every time), the prompt's open-ended menu + evidence-citation requirement is the lever; iterate the prompt, it's evolvable.
- **R2 overlap:** keep the reflection read shaped so R2 can later reuse it to also emit research-lessons; don't hard-couple yet.

## Notes

- 2026-08-24: Design settled with the human. Current evolution confirmed to be modifier + safe-integration only (`evolution/runtime.py` + `propose_merge.py`); its only memory is `memory.recall_global` (recall of past evolutions, not reflection). R16 adds the reflect→propose arm with the human as evaluator — a human-gated RHI loop. Two steps (A reflect+record, B propose+author-command), not one; "evolution prompt" = the `{{COMMAND}}` string, not a new system prompt. Per-session reflection only for now; cross-session global lessons = R2's territory.
