# H1 — Hypothesis engine (Google AI co-scientist protocol)

**Status:** Planned (design + stub only; no live protocol yet)
**Owner:** unassigned
**Started:** —
**Done when:** a researcher states a goal, the system generates competing
hypotheses, ranks them by a tournament/Elo protocol, and the human selects from
a ranked, reviewed list — all driven by the backend, with the UI showing real
data (no client-side mock).

## Why

The UI3 **Hypotheses** page currently renders a client-side toy (`SEED_HYPS` in
`ui3/src/lib/mock.ts`) with fake pursue/park interactions. That misrepresents
the product: hypothesis selection is supposed to be a *real* backend loop that
generates and ranks AI hypotheses for the human to choose among, following the
**Google "AI co-scientist"** protocol (Gottweis et al., 2025): a multi-agent
system that generates, debates, ranks (via a tournament with Elo ratings), and
evolves research hypotheses, with a human in the loop selecting directions.

This plan opens the branch and pins the **contract** (API, events, data model)
so the toy can be replaced by an honest "awaiting backend" stub now, and the
engine implemented against a fixed interface later.

## The protocol (target behaviour)

A hypothesis **session** runs a loop of specialised roles over a research goal:

1. **Generation** — produce N candidate hypotheses from the goal, grounded in
   literature/tool output; encourage diversity (different mechanisms/angles).
2. **Reflection (review)** — each hypothesis is reviewed for correctness,
   novelty, testability, and safety; reviews attach to the hypothesis.
3. **Ranking (tournament)** — pairwise "which is the better hypothesis"
   comparisons (scientific-debate prompts) feed an **Elo** rating; ranking is
   the ordering signal, not a single judge.
4. **Evolution** — top hypotheses are improved: combine complementary ideas,
   sharpen testability, simplify; evolved hypotheses re-enter the tournament
   (parent links preserved for lineage).
5. **Proximity / meta-review** — cluster near-duplicates, synthesise recurring
   critique into guidance for the next generation round.
6. **Selection (HITL)** — the human is presented a **ranked, reviewed** list and
   selects one (or asks for another round). Selection steers the research
   session; the choice + rationale are recorded.

The loop repeats (bounded by round count / token budget / "no new top-1")
until the human selects.

## Contract (pin this now; implement later)

### REST (mirror the research/evolution surface in `api/server.py`)

| Method | Path | Body | Returns |
|--------|------|------|---------|
| POST | `/hypothesis/sessions` | `{ goal, session_id?, config? }` | `{ session_id, goal }` |
| GET  | `/hypothesis/{sid}/hypotheses` | — | `HypothesisView[]` (ranked, best first) |
| GET  | `/hypothesis/{sid}/hypotheses/{hid}` | — | `HypothesisView` (with reviews + lineage) |
| POST | `/hypothesis/{sid}/select` | `{ hypothesis_id, note? }` | `{ ok }` |
| POST | `/hypothesis/{sid}/round` | `{ }` | `{ ok, round }` (request another generation/rank round) |

Auth + tenancy identical to the rest of the API (Bearer `csk_…`, everything
scoped to `ctx.root`). Events stream through the existing
`GET /events/{sid}` (SSE) — no new stream transport.

### Event kinds (extend the taxonomy in the design brief §4.D)

```
hypothesis.session_started
hypothesis.generated      { hypothesis_id, statement }
hypothesis.reviewed       { hypothesis_id, verdict, scores }
hypothesis.match          { a, b, winner }          # one tournament comparison
hypothesis.ranked         { round, top: [hypothesis_id...] }
hypothesis.evolved        { hypothesis_id, parent_ids }
hypothesis.round_complete { round, count }
hypothesis.selected       { hypothesis_id }          # HITL, actor=human
```

### Data model (server-authoritative; UI mirrors it)

```
Hypothesis {
  id: string
  statement: string           # the hypothesis itself
  rationale: string
  state: "proposed" | "reviewed" | "top" | "evolved" | "parked" | "selected"
  elo: number                 # tournament rating (default 1200)
  round: number               # generation round it entered on
  parent_ids: string[]        # lineage for evolved hypotheses
  reviews: { reviewer, verdict, novelty, testability, correctness, note }[]
}
```

The UI's client-side `Hyp` type in `ui3/src/lib/types.ts` is replaced by this
server shape; `HypothesisTree` renders `parent_ids` for lineage and `elo`/rank
for ordering, and "select" calls `POST /hypothesis/{sid}/select` (no local
state mutation).

## Minimal-scaffold note

Per `CLAUDE.md`, methodology (how many rounds, which critique dimensions, the
debate prompts) is the **evolution agent's** territory, not hardcoded here. The
developer-side surface is the *plumbing*: the REST endpoints, the event kinds,
the persisted hypothesis store under the session dir, and the HITL selection
gate. Ship the mechanism + a thin default generator; leave the tournament
prompts and role hierarchy for the evolution agent to grow.

## Steps

- [ ] 1. **Contract stubs (this branch).** Add `Hypothesis*` request/response
      models to `api/schemas.py` (unused, like `StartEvolutionRequest`), and
      document the event kinds. No live endpoints.
- [ ] 2. **UI stub (this branch).** Replace the mock `HypothesisTree` toy with an
      honest "awaiting backend" panel: describe the generate→review→rank→evolve→
      select pipeline, mark it not-yet-wired, reference this plan. Drop the
      fake pursue/park interactions.
- [ ] 3. Persisted hypothesis store: `state/sessions/<sid>/hypotheses/*.json`
      (server-authoritative), plus `_list_hypotheses`/`_write_hypothesis` helpers.
- [ ] 4. `POST /hypothesis/sessions` → spawn a `hypothesis.runtime` subprocess
      (mirror `_spawn_research`), first turn generates N candidates.
- [ ] 5. Reflection + tournament: reviewer role, pairwise match prompts, Elo
      update; emit `hypothesis.reviewed` / `hypothesis.match` / `hypothesis.ranked`.
- [ ] 6. Evolution round: evolve top-K, preserve `parent_ids`, re-rank.
- [ ] 7. `GET /hypothesis/{sid}/hypotheses` (ranked) + `POST …/select` (HITL gate
      via `scaffold/hitl.py`) + `POST …/round`.
- [ ] 8. Wire the UI to the real endpoints: `HypothesisTree` renders lineage +
      Elo rank; "select" hits the backend; live updates via SSE.
- [ ] 9. Live verification: goal → ≥2 rounds → ranked list → human selects; the
      choice steers the linked research session.

## Files touched

- `docs/plans/H1-hypothesis-coscientist.md` (this file)
- `api/schemas.py` — `HypothesisConfig`, `StartHypothesisRequest/Response`,
  `HypothesisView`, `HypothesisReview`, `SelectHypothesisRequest` (stubs)
- `ui3/src/components/HypothesisView.tsx` / `HypothesisTree.tsx` — replace toy
  with awaiting-backend stub
- Later: `api/server.py` (`/hypothesis/*`), `hypothesis/runtime.py`,
  `roles/subagents/*` (generator/reflector/ranker/evolver)

## Verification

Design/stub phase (this branch): `npm run build` in `ui3/` passes; the
Hypotheses page shows the awaiting-backend stub (no mock interactions);
`python3 -c "import api.schemas"` imports the new models. Full engine
verification is step 9 above.

## References

- Google Research, "Towards an AI co-scientist" (2025) — generation → reflection
  → ranking (tournament/Elo) → evolution → meta-review, human-in-the-loop.
- Design brief: `docs/frontend-design-brief.md` (§3 screen H/I context, §4.D
  event taxonomy this extends).
