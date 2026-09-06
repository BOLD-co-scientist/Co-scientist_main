# R19 — Guided evolution search (proactive, goal-conditioned, human-gated)

**Status:** Design in discussion — report + proposed design + open questions recorded 2026-09-06; **no code yet**. Answer the open questions before Phase 0.
**Owner:** yuhe
**Started:** 2026-09-06
**Branch:** `feat/R19-guided-evolution` (off `feat/O1-onboarding` @ `9dda59c`, which carries R16/R17 and the O2 design) — worktree `../coscientist-R19`
**Reference note:** [docs/research/self-improvement-archives.md](../research/self-improvement-archives.md) (how DGM / HyperAgents / MCTS-AHD store and search their trees; what we already have)
**Done when:** without a typed command, the system proposes goal-relevant evolutions (tools, roles, workflows, viewers, imports) ahead of the researcher's next subgoal; each proposal is a node in a durable proposal tree hanging off an R17 version node; the human picks one, or asks to explore two, and both flow through the unchanged evolution + merge gates; every merged version acquires a task-conditioned fitness from staged evaluation; fitness backpropagates so later proposals are ranked by what actually helped; and the whole loop can be run with gate 1 auto-answered in R15 autonomous mode for a Darwinian-vs-human-guided benchmark.

## Why

Self-improvement today is R7 skill discovery, R16 single-session reflection cards, and human-typed evolution commands. Direction is 100 % human or 100 % one-session; nothing is measured; nothing conditions on the tree of past evolutions; nothing plans ahead of the researcher. The intent (from the "Heuristic Search for human-guided RSI" slide, transcribed in the reference note) is an agent that **proactively proposes** system changes appropriate to the task scenario — e.g. for a DNA-structure task, propose building or importing a structure viewer before anyone asks — and a **search over the evolution tree** in the manner of DGM / HyperAgents / MCTS-AHD, with the human as selector and approver.

The archive half of this is already built (R17). R19 is the layer those papers build *on top of* an archive: fitness on nodes, a selection rule, a proposer conditioned on the tree and on the researcher's goals, and an outer loop. It also connects the onboarding brief (O1/O2) to evolution: the brief's evaluation protocol is the only place a task-specific objective can come from, and its subgoals are what make proposals timely.

## Design (proposed; under the defaults in "Open questions")

### Principles

1. **Human-guided heuristic search, not autonomous RSI.** Two gates stay: gate 1 = *evolve in this direction?* (pick a proposal), gate 2 = *is the change correct?* (`propose_merge` HITL). The planner never edits code and never runs the evolution agent by itself, except in the R15 autonomous benchmark arm.
2. **The archive is R17.** No second version store. R19 adds *additive* fields to `meta.json` and new durable stores beside it.
3. **Fitness is task-conditioned.** A version's value is measured *for a problem node*; the same version may score differently on two problems. Selection uses fitness on the current problem, falling back to the tenant-wide aggregate.
4. **Propose cheap, materialize rarely.** MCTS-AHD's split: proposal nodes are thoughts (many, cheap); version nodes are materialized proposals (few, expensive). Progressive widening limits how many proposals a version may spawn before it has been used.
5. **Platform-side runner, tenant-owned methodology.** Tenant roots are frozen forks, so the planner runs from `api/` (the R16 `research/reflect.py` → O2 `api/advisor.py` pattern) and reads its prompt and policy knobs from the tenant root, so evolution can still rewrite *how it plans* (partial HyperAgents self-reference).
6. **Minimal scaffold.** New concepts: the goal ledger, the proposal node, the eval record. Nothing else.

### Two trees, cross-linked

- **Version tree** (exists): `ver/<id>` nodes, `base_sha` parent pointer, `diff.patch`, `rationale.md`, smoke result, `origin_session`.
- **Proposal tree** (new): thought nodes hanging off version nodes. A version node *is* a proposal that a human chose to materialize (`proposal.status = version:<id>`; `meta.json.proposal_id`).

All new data is durable Tier-3 under `state/`, platform-owned like `state/projects/` (O2), read only by `api/`:

```
state/evolution/
  goals/<pid>.json                 # goal ledger per problem node
  proposals/<pid>/<p_id>.json      # proposal nodes (thoughts)
  evals/<version_id>/<e_id>.json   # eval records (fitness evidence)
  runs/<pid>/<run_id>/             # planner runs: snapshot.json (exact inputs), planner.log, running.lock
  events.jsonl                     # goal.*, proposal.*, eval.*, planner.*
```

**Goal ledger** — derived from the O2 brief v2 (five questions) plus two new brief fields:

```json
{
  "problem_node": "pid", "brief_id": "bid", "updated": 0,
  "approach_hints": "the researcher's intuition: what method / pipeline / search space should work (slide item 1d)",
  "subgoals": [
    {"id": "g1", "order": 1, "text": "…", "acceptance": ["… from evaluation_protocol / success_criteria …"],
     "status": "done | current | pending", "capabilities_needed": ["3D structure viewer", "…"]}
  ],
  "capability_wishlist": [
    {"name": "3D structure viewer", "why": "deliverable D2 is a structure; nothing renders it",
     "candidates": ["open-source X (pip)", "…"], "status": "missing | present:<tool> | proposed:<p_id> | version:<id>"}
  ]
}
```

**Proposal node:**

```json
{
  "id": "p_…", "problem_node": "pid", "parent_version": "<ver id or bootstrap sha>", "parent_proposal": "p_… | null",
  "goal_ids": ["g2"], "operator": "new_capability | refine | retune | merge_versions | import | path_continue",
  "title": "…", "rationale": "… cites goals, tree evidence, session evidence …",
  "command": "self-contained evolution command (files, change, acceptance) — verbatim to the evolution agent",
  "prior": {"gain": 0.6, "cost_usd": 4, "confidence": 0.5, "why_now": "subgoal g2 is current"},
  "provenance": ["reflection:<sid>", "eval:<e_id>", "web:<url>"],
  "status": "proposed | declined | picked | implementing | version:<id> | measured",
  "Q": null, "N": 0,
  "human": {"decision": "pick | both | decline | null", "note": "", "ts": 0},
  "created": 0, "run_id": "…"
}
```

**Eval record** (one per stage, per version, per problem):

```json
{
  "id": "e_…", "version": "<id>", "problem_node": "pid", "baseline_version": "<parent id> | null",
  "stage": "smoke | replay | checks | judge | human", "session": "<sid> | null",
  "checks": [{"name": "deliverable .pdf compiled", "pass": true, "source": "evaluation_protocol"}],
  "judge": {"score": 0.7, "rubric": "Q3", "served_by": "…"}, "human_rating": 4,
  "cost_usd": 3.2, "delta_vs_baseline": 0.15, "ts": 0
}
```

**Version meta, additive:** `parent_version` (resolved from `base_sha`), `proposal_id`, `fitness: {"<pid>": {"F": 0.62, "n": 3}, "_all": {...}}`, `children` (derived on read). Written by `api/` only, so no tenant runtime change is needed and frozen forks are not an issue. Because these are new durable schemas, they are registered in `scaffold/contract.py` with a golden fixture (R17 rule).

### Operators (MCTS-AHD's actions mapped onto our directions)

| MCTS-AHD | Meaning there | R19 proposal operator |
|---|---|---|
| i1 | new heuristic from scratch | `new_capability` — a tool, a role, a workflow phase, an artifact viewer, a wrapper for a found open-source package |
| m1 / m2 | change mechanism / tune parameters | `refine` (rewrite a prompt, role wiring, methodology) / `retune` (models, tiers, budgets, thresholds) |
| e1 | blend heuristics from different subtrees | `merge_versions` — combine two of the tenant's own versions into a git merge node (R17 DAG-capable) |
| e2 | parent + an elite reference | `import` — a version or tool from another tenant (the O2 import operators, unchanged) |
| s1 | reason over the root → leaf path | `path_continue` — continue this lineage's line of thought, conditioned on every ancestor's rationale, decision and fitness |

R16's five *directions* (agents / workflow / tools / methodology / memory) remain as the `direction` tag on each proposal; operators say *how* a proposal relates to the tree, directions say *what* it touches.

### Selection

Two decisions, both with a human override.

**Which version to plan from** (DGM rule; only matters when the human is not sitting on the node they want to grow):

```
P(v) ∝ sigmoid(λ · (F̂(v, pid) − m)) / (1 + children(v))        λ = 10, m = mean of top-3 F̂
```

**How to rank proposals under that version** (UCT-style; MCTS-AHD rule adapted to priors):

```
rank(p) = F̂(parent(p), pid) + gain(p) − κ · cost(p) + c · sqrt( ln(N(parent(p)) + 1) / (1 + N(p)) )
F̂(v)   = normalized fitness, backed up as max over descendants (Q ← max children, N ← sum)
c      = c0 · (B − b) / B      (decays with merges b spent of the per-problem budget B)
```

Progressive widening: a version may hold at most `floor(N(v)^0.5) + k0` open proposals (`k0` = 3), so an unused version does not accumulate an ever-growing card pile. Declines carry a reason and are fed back into the next planner run.

With tens of nodes, not thousands, these are **ranking heuristics for the cards**, not a search algorithm; the human is the main selector. The formulas earn their keep by (a) making "explore both" and "switch back" ordinary tree operations, (b) keeping stepping stones alive, (c) giving the benchmark arm a policy to run on its own.

**Bifurcation ("explore both").** The human ticks two proposals on the same base: two evolution commands are spawned sequentially off the same commit (separate `evo/*` worktrees; the merge guard serializes the merges; both children keep `parent_version` = the base). Both are evaluated with the same replay; a comparison card shows checks / judge / cost side by side with *Keep A active* / *Keep B active* / *Switch back*. The loser stays in the tree (DGM stepping stone).

### The loop

1. **Goals from onboarding** (Phase 0). Brief v2 → goal ledger: ordered subgoals with acceptance derived from the evaluation protocol; capability wishlist derived from prior work + the deep-research landscape; `approach_hints` from the researcher.
2. **Propose** (planner run). Inputs, all bounded: goal ledger (current subgoal first); active version's harness inventory (so it never proposes what exists); tree summary (lineage with fitness, prior proposals + human decisions + decline reasons — the s1 path); latest R16 reflections for this problem's sessions; O2 importable catalogue; optional capability-discovery results (Phase 4). Output: ≤ `2k+2` typed proposals with priors. Ids validated against the registry; unknown ids dropped; exact inputs kept as `snapshot.json`.
3. **Select** (gate 1). Ranked cards in the Evolution tab; "Use this" prefills the composer (editable, never auto-run — R16 behaviour kept); "Explore both"; "Decline + why".
4. **Implement** — the existing evolution agent + `propose_merge` gate 2, unchanged. `proposal_id` and `source_session` travel on the command.
5. **Evaluate in stages** (DGM staging; each stage gates the next, all recorded as eval records):
   - **smoke/compat** — exists (auto-reject before HITL);
   - **replay** — one representative session of this problem re-run on parent and child under R15 autonomous mode (`hitl.auto_answer`), same brief, cost capped; the human triggers it or it runs automatically in benchmark mode;
   - **checks** — deterministic checks compiled once from the brief's Q3 (an LLM turns the protocol into a checklist the researcher edits; then it is code/regex/file checks, not a model);
   - **judge** — an LLM judge with the Q3 rubric on the deliverable (served_by recorded; Fable → Opus fallback via `api/llm.py`);
   - **human** — a 1–5 rating card at session end ("did this version serve you better than before?").
   Fitness `F(v, pid)` = weighted aggregate (defaults: human 0.5, checks 0.3, judge 0.2, minus a cost penalty; weights in settings).
6. **Backpropagate.** `Q(v) ← max over descendants`, `N ← sum`; the version's fitness updates its proposal node and ancestors. Nothing is pruned.
7. **Re-plan, proactively.** Triggers: node registered on the O2 tree (before session 1 — this is where the "propose a viewer up front" example lands); `research.complete` (folds R16: reflection = evidence, planner = the proposer); `evolution.merged`; manual. Hash-deduped like the O2 advisor; one run per problem at a time; never on autosave.

### Onboarding integration (slide item 1 ↔ O2 brief v2)

| Slide item | O2 brief field | Status |
|---|---|---|
| 1a problem setting | Q0 `title`, `research_question`, `objectives[]`, `domain` | exists |
| 1b related literature | Q2 `prior_work` | exists |
| 1c methods to hold in the arsenal | **derived** `capability_wishlist` from Q2 + the R14 deep-research landscape | new, derived |
| 1d human intuition | **new** `approach_hints` | new field |
| 1e deliverables + verification | Q3 `evaluation_protocol`, `success_criteria`; `deliverables[]` | exists |
| "a set of subgoals" | **new** ordered `subgoals[]` with acceptance, `status` (current/done) | new field |

The onboarding wizard gets a small "Goals" panel (ordered subgoals, editable; capability wishlist with present/missing dots; approach hints). The planner's *why-now* is the current subgoal, so proposals arrive ahead of need instead of after a failure.

### What the human sees

- **Evolution tab:** the R16 suggestion cards become planner cards — operator badge, goal served, expected gain / cost / confidence, why-now, provenance chips; *Use this* / *Explore both* / *Decline (why?)*; a footer with served_by, spend, *Re-plan*.
- **Lineage canvas:** proposal ghosts as dashed children under a version (collapsed by default); version nodes show fitness for the current problem; a comparison card after a paired replay.
- **Research conversation:** the R16 nudge card, now "N goal-relevant evolutions proposed"; at session end a one-tap rating card.
- **Onboarding:** the Goals panel; the advisor pill also reports "planner: N proposals".

### REST (Bearer-authed; additive)

| Method | Path | Body → returns |
|---|---|---|
| GET / PUT | `/projects/{pid}/goals` | goal ledger (PUT: reorder, mark current, edit hints/wishlist) |
| POST | `/projects/{pid}/plan` | `{trigger?}` → `{ok, run_id, status}` (manual planner run) |
| GET | `/projects/{pid}/proposals` | ranked proposal tree for the active version (+ `running`) |
| POST | `/proposals/{p_id}/decide` | `{decision: pick\|both\|decline, note?, with?: p_id}` → spawns evolution(s) or records the decline |
| POST | `/versions/{id}/evals` | `{stage: human\|replay, problem_node, rating?, session?}` → eval record (replay: idle-guarded, cost-capped) |
| GET | `/versions/{id}/evals` | eval records; `GET /versions` gains `fitness` |
| POST | `/evolution/commands` | unchanged shape; optional `proposal_id` |

Events (additive): `goal.updated`, `planner.started/ready/failed/skipped`, `proposal.created/picked/declined`, `eval.recorded`, `version.fitness` — in `state/evolution/events.jsonl`, mirrored as slim cards into the owner's session stream like O2's `advisor.ready`.

### Autonomous benchmark arm (R15)

With `autonomous: true` the planner may answer gate 1 itself by the rank rule (top-1, or top-2 as a bifurcation every k-th step), run the replay stage automatically, and continue. Gate 2 (`evolution_merge`) stays human, as R15 already mandates. This gives a fully Darwinian arm to compare against the human-guided arm on a fixed brief — the experiment the slide is ultimately about.

### Constraints and limits (honest)

- **Fitness is expensive and noisy.** A paired replay ≈ two research sessions. The tree will be tens of nodes. Value of the MCTS machinery = bookkeeping + a policy for the benchmark arm, not deep search.
- **UI reach.** R17 Tier-2b: the tenant's evolution agent cannot edit `ui3/` or `api/`. "Build a DNA visualizer" is reachable today only as an **artifact viewer**: a tool that emits an HTML/JS file into `results/` which the shared UI renders in a sandboxed iframe panel. Reopening Tier-2b is a separate decision (open question 3).
- **Sandbox strips dependencies.** `py_exec` runs without third-party packages; a found open-source package must be wrapped as an MCP tool with its own environment or dispatched via the longjob spooler. `import`/`new_capability` proposals must name that path explicitly.
- **Frozen forks.** Planner in `api/`; tenant-side changes (a `prompts/evo_plan.md` the planner reads if present; the human-rating card is UI-side) reach existing tenants without a runtime edit.
- **Prompt injection.** Capability discovery reads the web; anything it returns is data. Proposals carry `provenance`; a command authored from web content is shown with its sources and still goes through gate 1 and gate 2. The planner itself never executes code.
- **Card fatigue.** Widening + "no proposal" being a valid planner output + decline reasons fed back. If cards are noise the prompt is the lever (evolvable), as with R16.

## Open questions (answer before Phase 0; defaults in bold)

1. **Fitness sources.** Human rating only / + deterministic checks + judge / + paired replays. **Default: all three; replays on request, automatic only in benchmark mode.**
2. **Autonomy.** May the planner ever run an evolution without gate 1? **Default: never in normal mode; yes in R15 autonomous mode for the benchmark arm.**
3. **UI features.** Reopen R17 Tier-2b (agent may edit `ui3`/`api`) or take the artifact-viewer route? **Default: artifact route now.**
4. **Scope.** Goals + proposal tree per O2 problem node; version tree per tenant; other tenants only via `import`. **Default: yes.**
5. **Planner location.** Platform `api/` with tenant-owned prompt/policy, vs inside the tenant runtime (true HyperAgents self-reference, but frozen forks). **Default: platform + tenant prompt.**
6. **Budget.** Per-problem weekly envelope for replays/judge/planner. **Default: 1 replay per merge, planner ≤ 3 runs/day/problem, spend on every record; caps in settings.**
7. **Sequential goals.** Researcher orders subgoals at onboarding vs planner infers the phase. **Default: researcher orders, planner tracks `current`, may suggest a change.**
8. **R16 / O2 relationship.** Fold R16's cards into planner cards and treat the O2 advisor as the `import` operator? **Default: yes — one card surface; reflection and advisor become evidence sources.**

## Steps

### Phase 0 — Goal ledger (onboarding → objective)
- [ ] 1. Brief v2 (`api/onboarding.py`, `api/schemas.py`): add `approach_hints`, ordered `subgoals[] {text, acceptance[], status}`; additive; O1-era records load with empties.
- [ ] 2. `api/goals.py`: derive the goal ledger from a brief (one `api/llm.py` call: subgoals with acceptance from Q3, `capability_wishlist` from Q2 + the deep-research landscape when present); `GET/PUT /projects/{pid}/goals`; register `state/evolution/goals` in `scaffold/contract.py` + golden fixture.
- [ ] 3. UI: Goals panel in the onboarding wizard (order, current, hints, wishlist dots).
- Verify: curl create brief → goals derived → PUT reorder → `GET` reflects; `pytest tests/test_onboarding.py -q`; an existing brief renders unchanged.

### Phase 1 — Fitness on version nodes
- [ ] 4. `api/evals.py`: eval-record store; human-rating stage (`POST /versions/{id}/evals {stage: human}`); checks compiled from Q3 (LLM → editable checklist → deterministic runner over `results/`); judge stage; aggregate `F(v, pid)` written additively into `meta.json` (`fitness`, `parent_version`); `GET /versions` returns it.
- [ ] 5. Paired replay: `POST /versions/{id}/evals {stage: replay, baseline, session}` → fork the session's brief, run parent and child under R15 autonomous mode with the busy/switch guards, cost cap, one at a time; write both records + `delta_vs_baseline`.
- [ ] 6. UI: rating card at session end; fitness on lineage nodes; comparison card.
- Verify: rate a version via curl → `meta.json.fitness` present; a replay on the R17 stress tenant produces two records; `tests/test_contract_compat.py` green with the new fixtures.

### Phase 2 — Proposal tree + planner + cards
- [ ] 7. Proposal store (`state/evolution/proposals`), contract + fixture; `POST /proposals/{p}/decide` (pick → `POST /evolution/commands` with `proposal_id`; both → two sequential spawns off the same base; decline → note stored).
- [ ] 8. `api/planner.py` runner (`python -m api.planner --node <pid> --user <uid> --trigger <t>`), `api/prompts/planner.md` (hard rules: JSON only, cite goal + tree/session evidence, never propose what exists, `[]` is valid, ≤ `2k+2`, typed operators, commands self-contained, provenance required); tenant override `prompts/evo_plan.md` if present; lock/hash-dedupe/snapshot like the advisor; `_spawn_planner` on register / `research.complete` / `evolution.merged`.
- [ ] 9. UI: planner cards replace R16 cards (R16 record becomes evidence); Explore-both flow; proposal ghosts on the canvas (collapsed).
- Verify (live, ≈ $1–2): register a problem with a deliverable that implies a viewer → planner proposes a `new_capability` viewer with the artifact route before any session; pick it → evolution runs → `proposal.status = version:<id>`; explore-both on two proposals → two children with the same `parent_version`.

### Phase 3 — Selection + backprop
- [ ] 10. Rank rule, DGM parent sampling, widening, decay; declines fed back; `Q/N` backprop on eval writes; `tests/test_planner_policy.py` (pure functions, no model).
- [ ] 11. Autonomous arm: in R15 mode the planner answers gate 1 by rank; replay stage automatic; a run log per problem.
- Verify: a scripted tree (no model spend) ranks as expected; benchmark arm runs three steps on a fixed brief under the $5 rule.

### Phase 4 — Capability discovery
- [ ] 12. Planner may call web search / R14 deep research for `capability_wishlist` items with `status: missing`; results as `candidates[]` with sources; proposals of operator `import`/`new_capability` name the wrapping path (MCP tool env or longjob) and the artifact-viewer route for visual tools.
- [ ] 13. Docs: this plan ticked, ROADMAP, CLAUDE.md pointer; note the R16 → R19 card migration.
- Verify: for the DNA-structure example brief, the wishlist lists a viewer, the planner cites at least one open-source candidate with a URL, and the resulting command is executable by the evolution agent (live, ≈ $3).

## Files touched (planned)

- `docs/plans/R19-guided-evolution-search.md` (this), `docs/research/self-improvement-archives.md`, `ROADMAP.md`, `CLAUDE.md` (pointer, later)
- `api/onboarding.py`, `api/schemas.py` — brief fields
- `api/goals.py` (new), `api/evals.py` (new), `api/planner.py` (new), `api/prompts/planner.md` (new), `api/server.py` (routes, triggers)
- `scaffold/contract.py`, `tests/fixtures/golden/**` — new durable schemas; `scaffold/archive.py` (platform copy, additive `fitness`, `parent_version`, `proposal_id`)
- `tests/test_goals.py`, `tests/test_evals.py`, `tests/test_planner.py`, `tests/test_planner_policy.py` (new)
- `ui3/src/components/{OnboardingView,EvolutionView,EvolutionGraph,HitlPanel}.tsx`, `ui3/src/lib/{api,types}.ts`, `ui3/src/state/store.tsx`
- Not touched on purpose (frozen per tenant): `research/`, `evolution/`, `roles/`, `tools/`; optional tenant-side `prompts/evo_plan.md` read by the planner if present

## Verification

Per CLAUDE.md, each phase ends with a curl-driven pass (listed above). The feature is done when, on the live container: a researcher onboards a problem whose deliverables imply a missing capability; the planner proposes it before the first session; the researcher picks it (or explores two); the evolution merges as R17 versions; a paired replay + rating yields fitness on the nodes; the next planner run ranks by that fitness and cites it; and the same loop runs unattended under `autonomous: true` for three steps. Model spend for the full pass under $10 (escalate above $5 per the policy).

## Risks / open questions

- Cost and noise of fitness (see Constraints). Mitigation: staging, caps, human rating as the cheap default.
- Proposal spam / card fatigue. Mitigation: widening, `[]` valid, decline feedback, prompt is evolvable.
- Tier-2b: UI-level features are unreachable by the tenant agent; the artifact-viewer route must be built first or the headline example fails.
- Two gates on every step may feel heavy; batch proposals per planner run and keep "Use this" one click.
- Web-sourced commands: prompt-injection surface; provenance shown, gates unchanged, planner never executes.
- The R17 backfill (pre-R17 nodes without `meta.json`/tags) should land first or the tree the planner sees is incomplete.

## Notes

- 2026-09-06 — Discussion + report. Confirmed the three references' mechanics against the papers and the local HyperAgents checkout (see the reference note). Key findings: (1) the R17 archive already *is* the DGM archive, with git materializing nodes instead of patch chains; (2) what is missing is exactly DGM's layer above the archive — fitness, selection, a tree-conditioned proposer, an outer loop — plus a goal model, which DGM gets from a fixed benchmark and we must take from onboarding; (3) MCTS-AHD's thought/code split, typed actions, path-conditioning, progressive widening and max-backup are the right bookkeeping for "propose → human picks → implement → measure"; (4) the slide's five sequential-goal items map almost 1:1 onto the O2 brief; only "human intuition" (1d) and ordered subgoals are new fields, "methods in the arsenal" (1c) becomes a derived capability wishlist. Eight open questions recorded with defaults; no code until they are answered. Branch + worktree opened.
