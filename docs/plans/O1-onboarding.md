# O1 — Onboarding phase (problem brief → data → hypothesis search → launch)

**Status:** In progress
**Owner:** yuhe
**Started:** 2026-09-06
**Branch:** `feat/O1-onboarding`
**Done when:** every user starts a research session through a fixed-format
onboarding phase: they define the problem in a structured brief, upload/attach
the data, run the hypothesis search *before* the session exists, pick a working
hypothesis, and launch — and the supervisor's first turn receives the whole
brief verbatim. Free-form "quick start" stays available as a secondary path.

## Why

Today a session starts from one free-text line in the composer. The problem
statement, the data, and the working hypothesis all arrive ad hoc: data is
dropped in the library and the agent is told about it (or not); the
hypothesis tool lives on a separate page and is bolted onto the task string by
the client (`withHyp` in `ui3/src/state/store.tsx`). The agent therefore
starts most sessions under-specified, and the human has no fixed place to say
what "done" looks like.

The onboarding phase makes the *problem definition* a first-class,
server-authoritative object with a fixed shape, attaches data to it, and moves
the hypothesis search to the front of the process where it belongs.

## Design constraints (from CLAUDE.md)

- **Minimal scaffold.** This is plumbing: a persisted brief, a fixed rendering,
  REST routes, and a UI wizard. No new roles, phases, or domain logic. The
  hypothesis *methodology* stays the evolution agent's (H1) — we reuse the
  interim generator in `api/hypothesis.py` and only seed it from the brief.
- **Per-user harness roots are frozen forks** (`api/tenancy.py:ensure_user_root`
  copies `research/`, `scaffold/`, … once). A change to `research/runtime.py`
  would not reach existing users. So the brief is composed **API-side** into the
  first-turn message (`--task`) — no runtime change is required for the feature
  to work for every existing tenant.
- **HITL untouched.** Launch is a human action; checkpoints/HITL gates inside
  the session are unchanged. `autonomous` (R15) is passed through.

## Contract

### Fixed brief format (`api/onboarding.py`, `schemas.ProblemBrief`)

| Field | Type | Required | Notes |
|---|---|---|---|
| `title` | str | ✔ | becomes the session title (`research.requested.task`) |
| `domain` | str | | field / discipline |
| `background` | str | | context, motivation, what is already known |
| `research_question` | str | ✔ | the one question the session must answer |
| `objectives` | str[] | | concrete aims |
| `data` | `{path, description}[]` | | library-relative paths, validated at launch |
| `data_notes` | str | | provenance, format, caveats |
| `constraints` | str | | scope, time, compute, ethics |
| `success_criteria` | str | | what a good answer looks like |
| `deliverables` | str[] | | expected outputs |
| `hypothesis` | `{id, statement, rationale}` \| null | | copied server-side from the linked hypothesis session's `selected_id` |

Rendered by `render_brief()` in a canonical section order; `opening_message()`
wraps it for the supervisor's first turn with the exact `fs_read`/`py_exec`
paths for each attached file.

### REST (all Bearer-authed, tenant-scoped)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/onboarding/briefs` | `ProblemBrief` (partial ok) | `BriefRecord` |
| GET | `/onboarding/briefs` | — | `BriefSummary[]` (newest first) |
| GET | `/onboarding/briefs/{bid}` | — | `BriefRecord` |
| PUT | `/onboarding/briefs/{bid}` | `BriefPatch` (partial merge) | `BriefRecord` · 409 if launched |
| DELETE | `/onboarding/briefs/{bid}` | — | `{ok}` |
| POST | `/onboarding/briefs/{bid}/hypotheses` | `{n?}` | hypothesis session (same shape as `/hypothesis/*`), linked via `hypothesis_session_id` |
| POST | `/onboarding/briefs/{bid}/launch` | `{autonomous?}` | `{session_id, task, brief_id}` · 422 with `missing[]` / bad data paths · 409 if already launched |
| GET | `/onboarding/briefs/{bid}/preview` | — | `{text}` the exact opening message |
| GET | `/sessions/{sid}/brief` | — | the brief stored with the session · 404 |

`/hypothesis/{hid}/select` on a brief-linked hypothesis session also updates
the brief's `hypothesis` (server-authoritative; the UI never mutates it).
`SessionSummary` gains `has_brief`, `brief_title`, `hypothesis`.

### Delivery facts (verified, not obvious from the code)

- **What the supervisor really sees on turn 1.** The runtime (unchanged on
  every tenant) renders the opening message into the system prompt as
  `{{TASK}}` *and* sends it as the first user turn, prefixed by its own
  library listing. So the brief appears twice. Pre-existing behaviour; harmless.
- **argv ceiling.** The message is one `--task` argv element. Linux caps a
  single argv string at 128 KiB (E2BIG). `OPENING_MESSAGE_MAX_BYTES = 100_000`
  is enforced in `launch` (422 before any state is touched) and reported by
  `preview` (`size_bytes`, `max_bytes`, `too_large`) so the UI disables Launch.
- **Spawn failure is atomic.** If `create_subprocess_exec` raises, the
  half-created session dir is removed and the brief stays a draft (500 with
  the reason). `_spawn_research` also closes its log handle on failure.
- **Folders.** An attached folder renders with a trailing `/`, its file count
  and total size, and a bounded inner listing (20 entries), plus the note that
  `fs_read.read` works on files only. Large (>50 MB) and binary-looking files
  get an explicit "don't read it whole / use py_exec or ocr" warning; the data
  section also says what to do if `import pandas` fails inside `py_exec`
  (the sandbox-strips-deps question is still unverified on FLAIR).
- **Absolute paths are container paths** (`/app/state/users/<uid>/root/state/library/…`).
  Correct inside Docker; they would be wrong if the API were ever run on the host.
- The old tenant (`u_3d9695…`, 485-line runtime, pre-R13) lacks
  `latex_compile`/`deep_research`; a brief asking for a PDF gets markdown there.

### On disk

```
state/onboarding/<bid>.json          # draft / launched brief records (per user)
state/sessions/<sid>/brief.json      # frozen copy written at launch
```

### Events

```
research.requested   { task: <title>, brief_id }
session.brief        { brief_id, title, research_question, hypothesis?, data: [paths], text: <rendered brief> }
```

## Steps

- [x] 1. Plan file (this).
- [x] 2. `api/onboarding.py`: brief store helpers, `render_brief`, `opening_message`, `brief_goal`, `missing_required`.
- [x] 3. `api/schemas.py`: `BriefDataItem`, `ProblemBrief`, `BriefPatch`, `BriefRecord`, `BriefSummary`, `BriefHypothesesRequest`, `LaunchBriefRequest/Response`, `BriefPreview`; `SessionSummary.has_brief/brief_title/hypothesis`.
- [x] 4. `api/hypothesis.py`: optional `context` block so generation is seeded by the brief, not just a one-liner.
- [x] 5. `api/server.py`: onboarding routes; `_create_hyp_session` factored out; `select_hypothesis` syncs the brief; `GET /sessions/{sid}/brief`; session listing fields.
- [x] 6. `tests/test_onboarding.py` (11 tests): CRUD, tenancy isolation, rendering golden, data path validation (traversal + missing), preview == launch message, launch writes `brief.json` + events, hypothesis link/select sync, stale-search guard, autonomous flag, free-form path untouched.
- [x] 7. UI: `OnboardingView.tsx` wizard (Define → Data → Hypothesis search → Review & launch), `api.ts`/`mock.ts`/`types.ts`/`store.tsx` wiring, "New session" opens the wizard (free-form quick start kept, first-time users land on the wizard), brief bar + "view brief" on the session, `session.brief` renders in the timeline, hypothesis cards factored into `HypothesisCards.tsx` shared with the Hypotheses page.
- [x] 8. `npm run build` (ui3) + `pytest tests/ -q` green (84 passed, 1 skipped).
- [x] 9. Backend verification (curl, 2026-09-06, localhost container): user → upload file + folder file → brief create/list/patch → preview (missing=[], problems=[]) → hypotheses (live, `served_by claude-fable-5`, `brief_id` linked) → select → brief synced → launch → `events.jsonl` = `research.requested`(title) · `session.brief` · `session.start`(full brief) → supervisor's first reply restated the question, both data paths, and the working hypothesis, and honoured the "no deep research" constraint. PUT after launch → 409.
- [x] 10. ROADMAP + CLAUDE.md pointers (incl. the frozen-per-user-root note).
- [ ] 11. Later (evolution-agent territory, per H1): replace the interim generator with the tournament protocol; the brief seeding (`brief_goal`) is the stable hook.

## Verification

```bash
PYTHONPATH=. python3 -m pytest tests/test_onboarding.py tests/test_smoke_v0.py -q
(cd ui3 && npm run build)
# live: see step 9 — drive /onboarding/* with curl against the running API
```
