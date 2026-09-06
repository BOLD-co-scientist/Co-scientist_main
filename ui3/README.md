# coscientist — UI3

A Vite + React + TypeScript + Tailwind rewrite of the coscientist workbench,
replacing the Streamlit `ui/app.py`. Dark‑first, calm scientific‑instrument feel;
the live event stream is the centerpiece, HITL approvals pull focus, and the
Evolution view is a **hypothesis explorer** the human steers.

> Design source of truth: `../../CoScientist.dc.html` (the approved interactive
> mockup). This app is a faithful port of it. The visual tokens in
> `src/index.css` are lifted verbatim from that file.

## Quickstart

```bash
cd ui3
npm install

# 1) Standalone demo on bundled mock data — no backend needed:
cp .env.example .env.local        # VITE_MOCK=1 is set by default
npm run dev                        # http://localhost:5173  (any csk_ key logs in)

# 2) Against a live coscientist API:
#    edit .env.local → set VITE_MOCK=0 and VITE_API_TARGET to your server
npm run dev
```

`npm run build` type‑checks (`tsc -b`) and produces a static bundle in `dist/`.
`npm run typecheck` runs the type checker only.

## How it's wired

- **Single origin / proxy (§7.4).** `vite.config.ts` proxies every API prefix
  (`/auth`, `/sessions`, `/events`, `/hitl`, `/library`, `/git`, …) to
  `VITE_API_TARGET`, so the browser talks to one origin and the Bearer flow is
  uniform with no CORS dance. In production, serve `dist/` behind the same
  reverse proxy that fronts the API.
- **Auth.** The `csk_…` key is held in memory + `localStorage`, sent as
  `Authorization: Bearer …` on every request. Any `401` clears it and bounces to
  the login gate (§7.5).
- **Event stream (§6, §7.1).** `src/lib/sse.ts` is a **fetch‑based** SSE reader
  (`fetch` + `ReadableStream` + a `data:` line parser) because the native
  `EventSource` cannot set an `Authorization` header. On selecting a session we
  `GET /sessions/{sid}/events?limit=200` for backfill, then open the SSE with
  `?since=<last id>`.
- **Authenticated download (§7.2)** and **multipart upload with progress + 413
  handling (§7.3)** live in `src/lib/api.ts`.
- **409 on `/messages` (§7.6)** surfaces as a non‑destructive inline notice and
  keeps the composer text.
- **Timestamps (§7.7)** are parsed as server‑local strings, never UTC.

## Mock mode

`VITE_MOCK=1` swaps the real client for `src/lib/mock.ts`, an in‑memory backend
that mirrors the approved mockup: a kinase‑selectivity session with a pending
GPU‑docking approval that, once granted, streams the follow‑up events in real
time. Use it for design review and offline demos. Everything the UI calls goes
through the same `Api` interface, so mock and live are interchangeable.

## Project structure

```
src/
  main.tsx, App.tsx            entry + auth gate
  index.css                    design tokens (CSS vars: dark + light) + keyframes
  lib/
    types.ts                   API + domain types (brief §§1–4)
    api.ts                     Api interface + real fetch client (§§5–7)
    sse.ts                     fetch-based SSE reader (§7.1)
    mock.ts                    in-memory backend + seed data (mock mode)
    format.ts                  sizes, ext colors, actor identity, ts parsing
    eventVM.ts                 event record → timeline view model (per-kind)
  state/
    store.tsx                  app context: auth, sessions, streaming, HITL, files
  components/
    TopBar, SessionRail, Workbench
    EventStream, EventItem, Composer      ← the signature timeline
    RightRail, HitlPanel, FilesPanel, ContextPanel
    OnboardingView                        ← the onboarding system (O1+O2): five-question statement → data → project tree & advisor → hypotheses → launch
    AdvisorCards, NearTree                advisor recommendations + one-click HITL-gated imports; the near-tree around a problem
    ProjectTreeView                       the org-wide project tree (Projects page)
    HypothesisView, HypothesisCards       parallel-set hypothesis explorer (cards shared with onboarding)
    EvolutionView, EvolutionGraph, ActivityLog      evolution: capability lineage + log
```

## Onboarding system (O1 + O2)

"New session" opens `OnboardingView`, a five-step wizard backed by
`/onboarding/briefs/*` and `/projects/*` (plans: `docs/plans/O1-onboarding.md`,
`docs/plans/O2-project-tree-advisor.md`). The steps adapt to what has been
answered:

1. **Problem statement** — the five questions (definition · why it matters ·
   prior work + open gap · objective evaluation · data/task/results/permissions)
   with a completeness dot per question, keywords, and the "Share on the
   project tree" toggle. Title + question unlock the later steps; questions
   1–3 register the problem on the org tree when you continue (and start the
   background advisor — the pill in the header tracks it on every step).
2. **Data** — upload files/folders to the library, attach them with a
   description, answer question 4. Every library file has a `get` download.
3. **Project tree & advisor** — the near-tree (neighbours by relation, confirm /
   reject / declare relations) and the advisor's cards: statement review
   (gaps by question with jump links, suggested keywords), related problems,
   the harness recommendation (Import & switch / Merge into mine → approval
   card → active version / Switch back), tool imports (checkboxes, role wiring
   → approval card), the evolution-agent fallback, served-by / cost / re-run.
4. **Hypotheses** (optional) — the parallel-set search seeded from the whole
   statement, with the advisor's hint.
5. **Review & launch** — the exact opening message (now with the harness and
   the project-tree context), gaps by question, and a warning when an import
   approval is still pending.

**Projects** in the left rail opens `ProjectTreeView`: every shared problem
clustered by domain, owner-coloured, status-shaped; a node's statement,
sessions/outcomes, harness and tools; "Start a subproblem here"; relation
confirm/reject. Mock mode (`VITE_MOCK=1`) simulates the registry, a canned
advisor run (~2 s) and the import approval flow.

## Hypotheses & Evolution

Two distinct left-rail sections:

- **Hypotheses** — free-form parallel-set exploration backed by the real
  generator (`POST /hypothesis/sessions`, `…/refine`, `…/select`; Fable with an
  Opus fallback). State a goal, get a few competing hypotheses, select one or
  "explore from" one to get a fresh set. The onboarding phase runs the same
  search seeded from the full problem brief; this page remains for scratch
  exploration outside a session.
- **Evolution** — two tabs. **Graph**: the capability lineage — skills the
  system has taught itself, rooted at the harness core and branching by
  direction, each node merged / in-flight / proposed; a proposed skill can
  **start evolution** (spins up a worktree), in-flight skills route to the HITL
  gate. **Activity log**: the `evolution.*` lifecycle timeline + the system-repo
  git graph (`GET /git/history`, public).

The evolution **command** composer is intentionally **stubbed** until
`POST /evolution/commands` lands on the backend (brief handoff checklist).

## Notes for wiring against the real backend

`SessionSummary` / `HitlPending` field names in `src/lib/types.ts` are the
client's expectation — reconcile them with `api/schemas.py` and adjust the thin
mapping in `src/lib/api.ts` if the server uses different keys. Everything else
(auth, SSE, HITL, files, git) matches the API reference in the design brief.
