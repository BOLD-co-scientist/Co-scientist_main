# R19 — Guided evolution search (proactive, goal-conditioned, judge + human gated)

**Status:** 🟢 Built and live-verified end to end (2026-09-07). Backend + ui3 Plan panel + OpenAI judge at both gates; 139 offline tests; one full live loop on a real brief. Remaining: platform-scope staging/cutover (Phase 4, flag off by design)
**Owner:** yuhe
**Started:** 2026-09-06
**Branch:** `feat/R19-guided-evolution` (off `feat/O1-onboarding` @ `9dda59c`) — worktree `../coscientist-R19`
**Reference note:** [docs/research/self-improvement-archives.md](../research/self-improvement-archives.md) (how DGM / HyperAgents / MCTS-AHD store and search their trees; what we already had)
**Done when:** without a typed command, the system proposes goal-relevant evolutions (tools, roles, workflows, UI features, imports) ahead of the researcher's next subgoal; every proposal is judged by an independent LLM on another provider and decided by the human (manual mode) or by the judge with the human told why (automatic mode); chosen proposals flow through the unchanged evolution agent and merge gate, where the judge again reviews (recommendation or answer); outcomes, adoption and decisions feed the next planner run; the researcher's sequential goals come from onboarding and the planner asks when the plan drifts; the agent may also evolve the shared UI/API behind a staging build and a tested cutover with a fallback copy.

## Why

Self-improvement was R7 skill discovery, R16 single-session reflection cards and human-typed evolution commands: direction 100 % human or 100 % one session, nothing measured, nothing conditioned on the tree, nothing planned ahead. The intent (the "Heuristic Search for human-guided RSI" slide, transcribed in the reference note) is an agent that **proactively proposes** system changes appropriate to the task and a **search over the evolution tree** in the manner of HyperAgents, with the human as selector and approver. The archive half already existed (R17). R19 is the layer HyperAgents builds *on top of* an archive — an evaluation folder, a selector, a meta-planner and an outer loop — with an LLM judge standing in for the benchmark.

## Decisions (the human's answers, 2026-09-06)

1. **Fitness = judge + human.** An LLM judge on a *different provider* (OpenAI; configured model `COSCIENTIST_JUDGE_MODEL`, default `gpt-6-extra`, resolved against the account's model list with a preference fallback) rates every proposed change; the human gives a yes/no on the same proposal. The judge learns the researcher's taste **in context** from the decision history, and judge/human agreement is measured (`GET /evolution/judge`).
2. **Autonomy.** The planner never launches an evolution without approval from the human or the judge. Per-tenant **mode**: `manual` (judge gives a concise recommendation; the human decides both gates) or `automatic` (the judge decides gate 1 *and* gate 2 and tells the human why; a judge-declined merge sends the agent back with the reasons, at most `JUDGE_MAX_AUTO_REJECTS` times, then the human decides). Automatic mode cannot be turned on without a working judge.
3. **UI features are in scope.** The evolution agent may edit `ui3/` and `api/` (`scope: "platform"`), with a safe fallback: existing processes keep running on the current checkout while the agent works in a worktree; the change must build (`npm run build`), boot (the API from the worktree answers `/health`) and pass the smoke gate; only then is it merged and cut over, with the previous commit tagged as the fallback. (Flag `COSCIENTIST_PLATFORM_EVOLUTION`; off until the cutover script lands — Phase 4.)
4. **Scope.** Goals and proposals per problem (keyed by brief id; O2's problem node is linked when present); versions per tenant; other tenants' versions only through the O2 import operator.
5. **Planner location.** Platform side (`api/`), in-process background tasks (the R16/O2 runner pattern without a subprocess), with the prompt overridable per tenant (`<root>/prompts/evo_plan.md`) so evolution can rewrite how it plans. Gates, judge, triggers and records stay platform-owned (the parts HyperAgents also keeps fixed).
6. **No explicit budget.** MCTS-AHD is only a source of vocabulary (typed proposals); no UCT, no backprop, no widening. HyperAgents is the model: archive + eval folder + meta-planner + selector.
7. **Sequential goals.** The researcher orders subgoals at onboarding; the planner infers the current phase from the sessions and, on a drastic misalignment, asks "has the plan changed?" (a drift question the human answers; a "no" is remembered).
8. **R16 and O2.** One card surface: R16 reflections become evidence the planner reads; the O2 advisor is the `import` operator.

## Design (as built)

### Components

| Module | Role |
|---|---|
| `api/evo_store.py` | durable store under `<tenant>/state/evolution/`: `settings.json` (mode), `goals/<bid>.json`, `proposals/<pid>.json`, `decisions.jsonl` (the eval folder), `adoption.json`, `runs/<run_id>.json`, `events.jsonl` |
| `api/goals.py` + `api/prompts/goals.md` | brief → ordered subgoals with acceptance + capability wishlist (present/missing vs the harness inventory); researcher patch/reorder/current; phase inference + drift question |
| `api/planner.py` + `api/prompts/planner.md` | the meta step: mission (goal ledger) + status (version tree, adoption, recent sessions, R16 reflections) + eval folder (decisions, open proposals) + inventories → one Claude call → validated, deduped proposals → one judge call each → records + events |
| `api/judge.py` + `api/prompts/judge.md` | OpenAI judge for gate 1 (proposal) and gate 2 (merge request); taste history block; calibration |
| `api/server.py` (R19 section) | routes; triggers (launch, clean research completion after `EVO_PLAN_DELAY_S`, evolution merged, manual); the single evolution launcher; the approved queue (one evolution per tenant at a time); the gate-2 watcher; adoption recording; proposal ↔ session ↔ version linking |
| `api/llm.py` | Fable→Opus platform call (copied verbatim from the O2 work so the branches merge cleanly) |

### The loop

1. **Goals.** `POST /evolution/goals/{bid}/derive` (or the first planner run) derives the ledger from the brief; the researcher edits/reorders/marks current with `PUT /evolution/goals/{bid}`; `approach_hints` carries the researcher's intuition (slide item 1d).
2. **Plan.** Triggers: brief launch (before the session does any work), clean `research.complete` (after the R16 reflection has had `EVO_PLAN_DELAY_S`), `evolution.merged`, manual `POST /evolution/plan`. One run per brief at a time (lock).
3. **Judge gate 1.** Every new proposal gets a verdict, score, why, risks, recommendation; recorded in the eval folder.
4. **Decide.** Manual: `POST /evolution/proposals/{pid}/decide {pick | both | decline}` ("both" = explore two branches: the first launches, the second queues and launches when the slot frees; children of the same parent become **sibling versions** — see propose_merge). Automatic: judge-approved proposals queue and launch in score order; `proposal.auto_approved` + `evolution.judge_approved` carry the why.
5. **Implement.** The unchanged evolution agent; `POST /evolution/commands` also accepts `proposal_id` (edited command → recorded as pick/edited) and `scope`.
6. **Judge gate 2.** `_watch_merge_gate` reviews each `evolution_merge` request: manual → the review is attached to the pending card (`judge` field on `GET /hitl/{sid}/pending`, `judge.review` event); automatic → the judge answers (`hitl.auto_answer`, actor `judge`), rejecting with reasons up to `JUDGE_MAX_AUTO_REJECTS` times then deferring to the human. Human answers on merge gates are recorded too.
7. **Outcome.** On evolution exit the proposal becomes `merged` (+`version_id`), `rejected` or `ended`; a merge re-plans. On research completion, tool-use counts are recorded per active version (adoption) for the next plan.

### Records

Proposal: `{id, brief_id, run_id, parent_version, title, scope (harness|platform), direction (agents|workflow|tools|methodology|memory|ui|import), goal_ids[], rationale, command, expected_gain, cost, why_now, provenance[], fingerprint, status (proposed|declined|queued|implementing|merged|rejected|ended), judge{verdict, score, why, risks, recommendation, served_by, at}, human{decision, note, at}, session_id, version_id}`.
Goal ledger: `{brief_id, title, research_question, approach_hints, subgoals[{id, order, text, acceptance[], capabilities_needed[], status}], capability_wishlist[{name, why, candidates[], status}], current, inferred_current, phase{…}, drift{question, inferred_current, declared_current, answered}, drift_history[], researcher_ordered, derived{served_by, usage, error}}`.
Decision: `{ts, proposal_id, actor (human|judge), stage (proposal|merge), decision, note, score?, served_by?}`.

### REST (all Bearer-authed; additive)

| Method | Path | Purpose |
|---|---|---|
| GET / PUT | `/evolution/mode` | `{mode, judge{available, model}}`; PUT `{mode}` (409 without a judge) |
| GET | `/evolution/judge` | judge status + calibration (judge vs human, both gates) |
| GET | `/evolution/goals` · `/evolution/goals/{bid}` | ledger summaries / one ledger (empty scaffold if not derived) |
| POST | `/evolution/goals/{bid}/derive` | `{approach_hints?, keep_subgoals?}` → derived ledger |
| PUT | `/evolution/goals/{bid}` | `{approach_hints?, subgoals?, order?, current?}` |
| POST | `/evolution/goals/{bid}/drift/answer` | `{changed, note?}` |
| POST | `/evolution/plan` | `{brief_id, trigger?}` → background run (409 while running) |
| GET | `/evolution/proposals?brief_id=` | proposals + latest run + running/mode flags |
| POST | `/evolution/proposals/{pid}/decide` | `{decision: pick\|both\|decline, note?, with_id?}` |
| GET | `/evolution/events?limit=` | the planner/judge/proposal event log |
| POST | `/evolution/commands` | unchanged + `proposal_id`, `scope` |
| GET | `/hitl/{sid}/pending` | unchanged + `judge` review on evolution_merge requests |

### Platform scope (answer 3) — design, Phase 4

`scope: "platform"` evolutions run the platform's own evolution runtime with `COSCIENTIST_REPO=<platform checkout>` (worktree + merge target) and `COSCIENTIST_ROOT=<tenant root>` (events/state), so the agent edits `ui3/` and `api/` in an isolated worktree while the running system stays on the current checkout. Gate additions in `propose_merge` for this scope: `npm run build` in the worktree's `ui3/` (when touched), boot `uvicorn api.server:app` from the worktree on a free port and require `/health` (when `api/`/`scaffold/` touched), plus the usual smoke/compat. On approval: ff-merge into the platform checkout, tag the previous commit as the fallback, write a cutover request under `<platform state>/platform/cutover/`; the host-side `deploy/platform_cutover.py` (the R12 spooler trust pattern — the container never runs docker) rebuilds `ui3/dist`, restarts the API service, health-checks, and rolls back to the fallback tag on failure. Enabled by `COSCIENTIST_PLATFORM_EVOLUTION=1`; until then platform-scope launches return 501 and queued platform proposals end with that error.

### What the human sees (UI — Phase 3)

Evolution tab: a **Plan** panel (goal ledger with order/current/hints/wishlist and the drift question; planner cards with scope/direction/goal/gain/cost, the judge's verdict + one-line recommendation, *Run* / *Explore both* / *Decline (why?)* / *Edit* (prefills the composer with the command and `proposal_id`); mode toggle Manual/Automatic with judge status; judge-approved launches show "Judge approved: why". Approval cards show the judge's gate-2 review. Research conversation: the R16 nudge stays; planner events mirror as slim notices.

## Steps

### Phase 1 — Goal ledger + store ✅
- [x] 1. `api/evo_store.py` (settings/mode, goals, proposals, decisions, adoption, runs, events).
- [x] 2. `api/goals.py` + `api/prompts/goals.md`: derive, patch/order/current, phase inference, drift question/answer, prompt rendering.
- [x] 3. Routes: mode, judge, goals (list/get/derive/put/drift).

### Phase 2 — Planner + judge + gates ✅
- [x] 4. `api/judge.py` + `api/prompts/judge.md`: OpenAI call (model resolution, JSON mode, graceful `unavailable`), taste history, calibration.
- [x] 5. `api/planner.py` + `api/prompts/planner.md`: composition, one model call, validation/dedupe, judge pass, records/events; tenant prompt override.
- [x] 6. `api/server.py`: launcher, decide (pick/both/decline), queue, automatic mode, gate-2 watcher (manual recommendation / automatic answer with reject cap), outcome linking, adoption, triggers (launch / research.complete / evolution.merged / manual), `proposal_id` + `scope` on commands, `judge` on pending.
- [x] 7. `tests/test_evo_planner.py` — 14 offline tests over the real routes (fake Claude + fake judge + fake evolution subprocess). `tests/test_judge_live.py` — live judge suite, skipped without a valid key.
- [x] 8. `evolution/tools/propose_merge.py`: sibling-version fallback so "explore both" children of one parent both become version nodes.

### Phase 3 — UI (ui3) ✅
- [x] 9. `types.ts` / `api.ts` / `mock.ts`: goal ledger, proposals, judge, mode, platform capability.
- [x] 10. `store.tsx`: mode, goals for the active brief, proposals, actions; refresh on evolution **and judge** events; launch errors surfaced instead of swallowed.
- [x] 11. `PlannerPanel.tsx` (mode toggle, goals + drift question, proposal cards with judge recommendation, Run/Queue/Explore both/Decline/Edit) mounted in `EvolutionView`; `HitlPanel` shows the judge's gate-2 review; `eventVM` labels for `judge.*` and judge-launched commands.
- [x] 12. `npm run build` clean. (Mounting the goals panel in the O2 wizard waits for O2 to land — its `OnboardingView` rewrite is uncommitted in the main checkout, so touching it here would collide.)

### Phase 3.5 — adversarial review pass ✅
- [x] A. Eight-lens review of the whole diff produced 110 findings; verification was cut short by a credit exhaustion, so every finding was triaged by hand against the code.
- [x] B. 26 real defects fixed (commit `4211415` + follow-ups), each with a regression test. The worst: the sibling-version fallback decided by substring-matching a `GitError` whose message always embeds the argv `--ff-only`, so **every** merge failure — dirty tree, conflict, anything — was silently recorded as a merged sibling version. Now decided by `git merge-base` (`sandbox.can_fast_forward`).
- [x] C. `tests/test_evo_fixes.py` — 20 regressions incl. a real-git test that pins the ff-only bug, judge verdict/redaction/model-resolution, wishlist matching, drift rules.

### Phase 4 — Platform scope (UI/API evolution with fallback)
- [ ] 13. `scaffold/sandbox.py` + `evolution/tools/bash_ro.py`: honour `settings.REPO`; `evolution/runtime.py --scope`; `propose_merge`: platform gate (ui3 build + API boot), fallback tag, cutover request.
- [ ] 14. `deploy/platform_cutover.py` (host-side; `--once` / `--watch`; rebuild, restart, health, rollback) + `deploy/README-platform-cutover.md`.
- [ ] 15. Tests + a live platform-scope evolution on a scratch clone of the platform (host-run API on a spare port).

### Phase 5 — Live verification ✅ (2026-09-07)
- [x] 16. Full loop on a host-run API (`deploy/dev_api.sh 8823`, scratch tenant) against a real structural-biology brief. See **Live run** below.
- [x] 17. Docs: ROADMAP, CLAUDE.md pointer (with the automatic-mode gate boundary stated precisely), `scaffold/hitl.py` comment, `.env.example` for every new knob, `docs/plans/PARALLEL.md` worktree row.

## Live run — 2026-09-07 (the evidence)

Brief: *"Structure of the LTEM stress-response protein and its ATP pocket"* — deliverables a PDF report with structure figures, a PDB model and a pocket residue table.

1. **Goals derived** (`claude-fable-5`): six ordered subgoals with verifiable acceptance taken from the brief's success criteria, and a seven-item capability wishlist — every item `missing`, including **"3D structure viewer / molecular renderer"**.
2. **Planner ran before any research session** and proposed four changes, each citing a subgoal and a wishlist gap. One was *"Add self-contained 3D structure viewer tool"*, correctly scoped `harness` (platform evolution is off, so the planner is told to route visual capabilities through a tool that writes a self-contained artifact into `results/`).
3. **Judge (gate 1)** — `gpt-5.5`, resolved down from the configured `gpt-6-extra`, which the account does not have. Four approvals with real reasoning and real risks (external API flakiness, sequence privacy, CDN reproducibility).
4. **Human picked** the viewer proposal → the unchanged evolution agent built `tools/struct_view/` (3Dmol.js HTML + PyMOL/matplotlib PNG fallback + bundled test PDB), registered it, and ran the smoke suite (12 passed).
5. **Judge (gate 2)** reviewed the merge request: `approve 0.74`, *"Approve if smoke/compat is run and passes"* — and correctly refused to assume the smoke result, because this tenant's frozen fork runs the pre-R19 merge tool that sends no `smoke` key. That gap is now closed API-side by `_backfill_merge_payload`, which reads `smoke.log` from the tenant's own archive.
6. **Human approved** → fast-forward merge → version node `ver/20260907-223732__…struct-view…` tagged and active; the proposal linked to it (`status: merged`, `version_id`).
7. **The merge re-planned automatically**; the wishlist now reads `present:struct_view` for the viewer, and the planner moved on to the next gaps (structure prediction, pocket finder, docking, homology search).
8. **Calibration**: gate 1 and gate 2 both 1 pair, 100 % judge/human agreement.

Live judge suite (`COSCIENTIST_LIVE_JUDGE=1 pytest tests/test_judge_live.py`) — 5/5 against the real judge: approves a goal-relevant tool; declines skipping the smoke gate; declines a duplicate of `latex_compile`; **follows the researcher's taste**, declining a critic subagent because the history showed two prior declines of reviewer roles; and at gate 2 catches a real path-safety flaw in a diff rather than rubber-stamping it.

**Two findings the live run produced, both fixed:** the frozen-fork smoke gap above, and a wishlist that still read `missing` for a capability the system had just built (tool names are slugs, wishlist entries are prose — matching is now prefix-aware, and statuses are recomputed on read rather than only when the planner runs).

## Files touched

- `docs/plans/R19-guided-evolution-search.md`, `docs/research/self-improvement-archives.md`, `ROADMAP.md`
- `api/evo_store.py`, `api/goals.py`, `api/judge.py`, `api/planner.py`, `api/llm.py` (copied verbatim from O2), `api/prompts/{goals,judge,planner}.md` (new)
- `api/server.py` (R19 section + monitor/pending/answer/commands/launch hooks), `api/schemas.py`, `scaffold/settings.py`
- `evolution/tools/propose_merge.py`, `scaffold/sandbox.py`, `scaffold/archive.py` (sibling versions)
- `tests/test_evo_planner.py`, `tests/test_judge_live.py` (new)
- Phase 3: `ui3/src/lib/{types,api,mock,eventVM}.ts`, `ui3/src/state/store.tsx`, `ui3/src/components/{PlannerPanel,EvolutionView,HitlPanel}.tsx`
- Phase 4: `scaffold/sandbox.py`, `evolution/tools/bash_ro.py`, `evolution/runtime.py`, `deploy/platform_cutover.py`

## Verification

- Offline: `PYTHONPATH=. python3 -m pytest tests/test_evo_planner.py tests/test_smoke_v0.py tests/test_contract_compat.py tests/test_onboarding.py tests/test_autonomous.py tests/test_auth_tenancy.py -q` — **59 passed, 1 skipped (2026-09-07)**.
- Live (Phase 5): the curl walkthrough above on a host-run API; judge spend a few cents per verdict; one real evolution run under the $5 rule.

## Risks / open questions

- **RESOLVED — the judge key works.** The 401s on 2026-09-06 (and the Anthropic 403s) were the account's credit exhaustion, not a bad key. The judge now runs live. **Rotate the key** that was pasted into the chat transcript.
- **Model id.** "GPT-6 Extra" is not on this account; the preference list resolves to `gpt-5.5` and `served_by` records it on every verdict. A guess made while model listing fails is no longer cached.
- **Frozen forks are the main deployment trap.** Everything R19 adds lives in `api/` + `state/`, but two tenant-side files changed (`propose_merge.py`, `sandbox.py`), and those reach only tenants created afterwards. The live run hit exactly this. Where it matters the API now compensates (`_backfill_merge_payload`); "Explore both" on an old tenant still cannot produce sibling versions, because its fork lacks the fallback.
- **Judge gaming.** The evolution agent could write acceptance tests it then passes; the judge reviews the diff at gate 2 and the human reads the why. Not solved, only surfaced.
- **Card fatigue.** Dedupe by fingerprint, declines remembered, `[]` is a valid planner output, one evolution at a time.
- **Frozen forks.** Everything R19 adds lives in `api/` + `state/`; the only tenant-side change (propose_merge sibling fallback) reaches existing tenants via bootstrap-of-new-tenants or an evolution merge, as R17 documents.
- **O2 not yet committed** in the main checkout; R19 keys goals by brief id and tolerates a missing project registry. When O2 lands: mount the goals panel in the wizard, link `problem_node`.

## Notes

- 2026-09-06 — Discussion + reference note; eight open questions with defaults; branch + worktree opened.
- 2026-09-06 — Answers received (see Decisions). MCTS-AHD dropped as a mechanism; HyperAgents is the model.
- 2026-09-07 — Backend built: store, goals (derive/patch/drift), judge (OpenAI, taste history, calibration), planner (HyperAgents-style composition, dedupe, judge pass, tenant prompt override), server wiring (launcher, decide, queue, automatic mode, gate-2 watcher, adoption, triggers), schemas, settings. 14 new offline tests + 45 existing green. Judge key rejected by OpenAI (401) — live judge run pending a valid key. Platform scope designed, flag off pending the cutover script.
