# O2 — Onboarding system: five-question problem statement, project tree, background Fable advisor, harness/tool imports

**Status:** 🟢 Built (2026-09-06) — backend + UI + offline suites; live verification recorded under Notes
**Owner:** yuhe
**Started:** 2026-09-06
**Branch:** `feat/O1-onboarding` (built directly on the O1 branch, which already carries the collaborator's R16/R17 work)
**Done when:** a researcher can (1) state a problem in the fixed five-question
format and upload its data, both retrievable at any time; (2) see their problem
placed on an org-wide project tree next to adjacent problems, with the sessions,
harness versions and tools each problem used; (3) receive, from a Fable advisor
running in the background, a recommendation to start from another researcher's
evolved harness and to import specific tools, and accept it with one
human-approved click that lands as a normal R17 version node in their own repo;
(4) optionally run the hypothesis search; then launch. All of it through the
shared API and UI, so every existing tenant gets it without a runtime change.

## Why

O1 gave every session a fixed-format brief and a hypothesis search before
launch. The product pitch goes further: the problem statement should answer the
five questions we actually ask researchers, the data they upload should be a
first-class, retrievable asset, and a new researcher should not start from
scratch when a colleague has already evolved a harness for an adjacent problem.
That last point is what R17 was built for ("the archive manifest with its patch
is the cross-user seed"; "git can transport versions"). O2 connects the pieces:
a shared registry of problems, an advisor that reads it, and import operations
that reuse R17's version nodes and the existing HITL gates.

## Design constraints (inherited, load-bearing)

- **Tenant roots are frozen forks** (`api/tenancy.py`); the shared platform is
  `api/` + `ui3/`. Everything in O2 lives there. No change to `scaffold/`,
  `research/`, `evolution/`, `roles/`, `prompts/` is required.
- **Cross-tenant data is read only by the platform.** Other researchers'
  statements, archives and tool inventories are read by `api/` and surfaced as
  bounded summaries. No tenant agent, tool or worktree can reach another root.
- **Every import is human-gated.** A harness import rewrites the user's agent
  layer; a tool import is an evolution. Both go through the existing HITL file
  protocol and the existing approval UI. R15 autonomous mode never answers them.
- **Methodology stays evolvable.** What counts as adjacent and what to recommend
  live in one prompt file and one scoring function, both human-editable.
- **Minimal scaffold.** New concepts: the problem node, the recommendation
  record, the import record. Nothing else.

## The fixed format (brief v2)

The five questions become the shape of the brief. Existing O1 fields keep
their names; new fields are additive; old records load with empties.

| Q | Field(s) | Required |
|---|---|---|
| 0 Definition of the problem | `title`, `research_question`, `objectives[]`, `domain` | title + question to launch |
| 1 Why scientifically important | `significance` | to register on the tree |
| 2 What humans achieved / what remains open | `prior_work` (was `background`), `open_gap` | to register on the tree |
| 3 Objective evaluation, no hackable proxy | `evaluation_protocol`, `success_criteria` | protocol, to register |
| 4 Exact dataset, metadata, task, protocol, results, permissions | `data[]` + `data_notes`, `task_definition`, `existing_results`, `data_access` | flagged by the advisor, never blocking |
| — | `constraints`, `deliverables[]`, `keywords[]`, `visibility` (`org` \| `private`), `parent_node`, `harness` (server-set), `schema_version` = 2 | |

Rules: launching keeps O1's rule (title + question). A node is created on the
tree as soon as Q1–Q3 are filled (on leaving step 1), with the visibility the
researcher chose; sharing it with the org (`visibility: org`) is what requires
the complete statement, because that is what colleagues and the advisor read.
A private node still gets advisor recommendations; it is simply invisible to
others. `background` stays as an alias read into
`prior_work` for old records. `render_brief()` gains fixed sections in this
order: Why this matters · Prior work and what remains open · Evaluation protocol
· (under Data) Task definition / Existing results / Data access · Project tree
context (server-composed: node id, ≤5 adjacent problems with one-line outcomes,
active harness, imported-from). `brief_goal()` feeds significance, open gap and
evaluation protocol to the hypothesis search.

Files stay in the library (`state/library/`), attached by path; `GET
/library/files/download?name=` makes every library file retrievable at any time
(today only session outputs have a download route).

## The project tree

A platform-owned registry, outside every tenant root:

```
state/projects/
  nodes/<pid>.json          # one per registered problem (statement projection + links)
  edges.jsonl               # append-only; current edge = last record per (src, dst, type)
  recommendations/<pid>/    # advisor records (<rec_id>.json, latest.json, running.lock, advisor.log)
  imports/<import_id>.json  # cross-tenant import ledger
  events.jsonl              # node.registered/updated, edge.*, advisor.*, import.*
  index.json                # cached summaries for the tree view
```

Node: `{id, owner{user_id, display_name}, visibility, status (registered|active|solved|abandoned|superseded), statement{five questions + keywords}, statement_sha256, statement_revisions[], source{user_id, brief_id}, parent_node, links{sessions[], harness_versions[], tools[], datasets[], outcomes[]}}`.
Edge: `{src, dst, type (adjacent|subproblem|shares_dataset|shares_method|supersedes), weight, source (keyword|advisor|human|import), status (proposed|confirmed|rejected), rationale, rec_id, ts}`.

Links are attached deterministically by `api/`: at launch (session id, active
harness version from `archive.list_versions`, tool inventory from the tenant's
`roles/` + `tools/`, dataset fingerprints); at session completion (outcome =
last `research.complete` summary, results files, version changed during the
session); at import (provenance on both nodes).

Adjacency: a deterministic BM25 pass over statements (`rank_bm25` is already a
dependency; the `scaffold/memory.py` idiom, run in `api/`) writes `adjacent`
edges (top 8, normalised score ≥ 0.25); equal dataset fingerprints →
`shares_dataset`; shared non-base tools → `shares_method`. The advisor refines
these into typed, rationalised edges; either node's owner can confirm or reject.
Embeddings are deliberately not built (R8 territory; `ADJACENCY_BACKEND` is the
seam).

Visibility: any authenticated user sees every `org` node's statement, owner
name, status, edges and link summaries (session ids + outcome one-liners,
version ids + summaries, tool names). Nobody sees another tenant's files,
events, memory, hypotheses or HITL records. Private nodes are owner-only.
A harness version is discoverable (and importable) only if it is linked to a
non-private launched problem, or its owner shared it explicitly
(`POST /versions/{id}/share`, an additive `shared` key on `meta.json`), so a
researcher's whole evolution history is not exposed because one problem is.

## The background advisor

`api/advisor.py`, run as `python -m api.advisor --node <pid> --user <uid>
--trigger <t>` — the R16 runner pattern (`research/reflect.py`) moved into the
platform: spawned detached by `api/server.py` like `_spawn_reflection`,
`cwd=/app`, log to `state/projects/recommendations/<pid>/advisor.log`, failures
swallowed so no request is ever blocked. The model call goes through
`api/llm.py::complete()` — the Fable → Opus fallback lifted from
`api/hypothesis.py` (which is refactored to use it). Prompt:
`api/prompts/advisor.md`, `{{PLACEHOLDER}}`-rendered, human-editable.

Triggers: leaving step 1 with Q1–Q3 filled (the node is created then, with the
chosen visibility, so recommendations have somewhere to live and the pill can
start early); statement changed (new sha); manual. Never on every autosave
(hash dedupe), never on session completion, never from tenant code. One run at a
time per node (`running.lock` with pid; stale locks cleaned like
`propose_merge._pid_alive`). The exact inputs the model saw are kept as
`snapshot.json` next to the record for audit.

Inputs (bounded): the node; the owner's current harness inventory (so it never
recommends what they have); the top-8 candidate neighbours with statements,
outcomes, versions (summary, rationale ≤600 chars, diffstat) and tools; an
importable catalogue (≤20 shared versions, ≤30 tools). Every id the model
returns is validated against the registry and dropped if unknown.

Record (`recommendations/<pid>/<rec_id>.json`): `status`, `served_by`, `usage`,
`summary`, `related_problems[] {node_id, relation, confidence, rationale,
edge_id}`, `harness_import {recommended, owner, version_id, sha, from_node,
summary, rationale, confidence, mode_hint (adopt|merge), risks[], alternatives[]}
| null`, `tool_imports[] {name, owner, version_id, description, rationale,
confidence, files[], role_wiring[]}`, `statement_feedback {missing[], notes[],
suggested_keywords[]}`, `hypothesis_seed {suggested, why}`. Events:
`advisor.started/ready/failed/skipped` in `state/projects/events.jsonl`; the
wizard polls `GET /projects/{pid}/recommendations` every 3 s while running.

Failure: refusal/error → Opus; both fail → `failed` with a Retry button and the
keyword edges still shown; empty tree → `skipped`, no model call. Unknown ids
returned by the model are dropped and listed in `validation_notes`. Cost ≈
$0.10–0.30 per run, recorded as `usage` (input/output tokens) on the record so
R5's ledger can sum it; daily caps per user and global (counted from the
records, env-configurable) are the spend backstop; manual re-runs are
human-initiated. Stale pre-gate worktrees (`worktrees/import-*` left by an API
crash) are pruned on the next import.

## Imports (on top of R17)

Transport: `git -C <user_root> fetch --no-tags <owner_root>
refs/tags/ver/<vid>:refs/imports/<owner>/<vid>` (all tenant repos share one
filesystem; verified on a scratch pair 2026-09-06). The fetched sha is checked
against the owner's `meta.json`. The archive's `diff.patch` is the fallback
transport (a future remote) and the input for merge mode.

Harness import, two modes (advisor gives `mode_hint`, human chooses):
- **adopt** (fresh root): tag the fetched commit `ver/<import_id>`, write
  `state/archive/evolutions/<import_id>/{meta.json (+imported_from, import_id,
  rollback_to), diff.patch, rationale.md}`, then the R17 switch
  (`archive.activate_version` under the switch lock and busy guard). `state/`
  untouched; own bootstrap and versions remain switchable; rollback = `POST
  /versions/{rollback_to}/activate`. Skills are gitignored assets (R17 decision) and do not travel with the
  commit; the approval card lists the donor's skills and `include_skills[]`
  copies the chosen ones into the importer's `.claude/skills/` (name
  collisions refused).
- **merge** (user has own versions): worktree from HEAD, `git apply --3way` of
  the donor delta, smoke + compat tests (imports are always strict), HITL,
  `--ff-only` merge, `record_merged_version(imported_from=…)`. On apply
  conflicts the import is delegated to the evolution agent with a
  self-contained command (the patch sits inside the user's own root).

Gate: the API opens the request itself in a dedicated `evo-import-<id>` session
using the `scaffold/hitl.py` file protocol (`kind: harness_import`, payload:
source, mode, summary, rationale, smoke result, diffstat, bounded diff preview,
`risks[]` computed deterministically by the API (tools and roles present in the
importer's tree but absent at the donor sha, e.g. `latex_compile` on a pre-R13
donor), donor skills with `include_skills`, what happens, rollback target), so it appears in the existing approval panel and
is answered through `POST /hitl/{sid}/{rid}/answer`; that route gains one hook
that applies or rejects. A pre-gate smoke in a temporary worktree runs *before*
the human is asked (the R17 rule). Autonomous mode cannot answer it (no runtime
owns that session; the kinds are excluded).

Tool import: deterministic and API-side (no agent run, no model spend). The
API creates a worktree off the importer's HEAD, runs `git checkout <sha> --
tools/<name>` from the fetched ref, inserts `- <name>` into the chosen roles'
YAML `tools:` lists, commits, runs the smoke + compat tests in the worktree,
opens the HITL request (`kind: tool_import`, same file protocol), and on
approve fast-forwards and records a child version via
`record_merged_version(imported_from=…)`. Verified during design: a subset
checkout plus `--ff-only` merge works on a detached HEAD. Rollback = switch to
the parent. Only when the smoke fails (the tool needs scaffold deltas from the
donor) does the API fall back to an authored evolution command through the
existing evolution runtime and `evolution_merge` gate.

## The onboarding page

Five steps; the advisor status pill sits in the header from the moment a
question is saved and never blocks:

1. **Problem statement** — five numbered blocks with completeness dots; keyword
   chips (advisor-proposed, editable); "Share on the project tree" toggle with
   the exact list of what is shared; "derives from" chip when started from a
   node.
2. **Data** — upload files/folders → library, attach with descriptions;
   permanent "Open library" link; note that files also appear in the session's
   Files panel; every library file downloadable.
3. **Project tree & recommendations** — Register (requires Q1–Q3). Left: the
   near-tree (me in the centre, neighbours by relation, owner names; click →
   statement, outcomes, versions, tools; "Declare relation"; "Open full tree").
   Right: advisor cards — statement review (gaps by question, jump links),
   related problems, harness recommendation (Import & switch / Merge into my
   harness → approval card → active version shown, Switch back), tool imports
   (checkboxes → editable evolution command → Run evolution → approval), footer
   with served_by, cost, Re-run, and the "start from scratch" rationale when
   nothing is recommended. Everything optional.
4. **Hypotheses (optional)** — unchanged, seeded by the richer brief.
5. **Review & launch** — the exact opening message (now with Project tree
   context and the Harness line); warns if an import approval is pending.

A **Projects** entry in the left rail opens the full org tree (clustered by
domain, owner-coloured, status greys like R17's rejected nodes) with node
detail, "Start a subproblem here" (`POST /projects/{pid}/spawn-brief`), and
"Confirm / reject relation".

## REST (all Bearer-authed; visibility filtered server-side)

| Method | Path | Body → returns |
|---|---|---|
| POST/PUT | `/onboarding/briefs[/{bid}]` | brief v2 fields (additive) |
| GET | `/library/files/download?name=` | file bytes (traversal-guarded) |
| POST | `/onboarding/briefs/{bid}/register` | `{visibility?}` → node (422 if Q1–Q3 empty) |
| GET | `/projects/tree` | `{nodes[], edges[]}` (summaries) |
| GET | `/projects/{pid}` | node + links + neighbours |
| GET | `/projects/{pid}/near` | `{self, neighbours[]}` for the wizard |
| PATCH | `/projects/{pid}` | status / visibility / keywords (owner) |
| POST | `/projects/{pid}/edges/{eid}/confirm|reject` | (either owner) |
| POST | `/projects/{pid}/spawn-brief` | new draft with `parent_node` |
| POST | `/projects/{pid}/advise` | `{ok, rec_id, status}` (manual run) |
| GET | `/projects/{pid}/recommendations` | latest record (+ `running`) |
| POST | `/projects/{pid}/imports` | `{kind: harness\|tool, owner, version_id, mode?, tools?, roles?}` → import record (harness: pending HITL in `evo-import-*`; tool: evolution session) |
| GET | `/projects/imports/{iid}` | ledger record |
| POST | `/hitl/{sid}/{rid}/answer` | unchanged shape; hook applies `harness_import` on approve |
| GET | `/versions` | unchanged; imported nodes carry `status: imported`, `imported_from` |
| POST | `/onboarding/briefs/{bid}/launch` | unchanged; stamps harness + tree context, links the session |

## Events (additive)

`node.registered/updated/status`, `edge.proposed/confirmed/rejected`,
`advisor.started/ready/failed/skipped`, `import.requested/approved/rejected/applied/failed/rolled_back`
in `state/projects/events.jsonl`; `session.brief` gains `harness` and
`tree_context`; `advisor.ready` is mirrored into the owner's session stream when
a session exists (a slim card, like R16's nudge).

## Steps

### Phase 1 — Brief v2 + library download
- [x] 1. `api/onboarding.py`: new fields, `_upgrade()` on load (background→prior_work alias), fixed sections, `brief_goal` context; `api/schemas.py` widened; `missing_for_registration()`.
- [x] 2. `GET /library/files/download` (reuse the library path guards).
- [x] 3. Tests: fixed-order golden updated; O1-era record loads/renders/launches; download traversal/404/200.
- [x] 4. UI: five-question DefineStep with completeness dots, keywords, share toggle; FilesPanel library download.
- Verify: `pytest tests/test_onboarding.py -q`; curl create → preview shows the new sections in order; an existing `brief.json` still renders; library download returns the bytes; `npm run build`.

### Phase 2 — Registry + tree
- [x] 5. `api/projects.py`: node/edge store (atomic writes), `register()`, `recompute_edges()` (BM25 + dataset/method edges), `link_session()`, `sync_session()`, visibility helpers, cache.
- [x] 6. Routes: register, tree, node, near, patch, edges confirm/reject, spawn-brief; launch links the session; `_monitor_runtime` syncs outcomes.
- [x] 7. `tests/test_projects.py`: two tenants; org vs private visibility; BM25 neighbour on overlapping statements; confirm/reject; spawn-brief sets parent; pre-R17 archive node tolerated.
- [x] 8. UI: `ProjectTreeView` (full tree; reuse the R17 canvas idioms), `NearTree` panel, Projects nav.
- Verify: two users via curl; A registers, B sees A's node, not A's private draft; B's near-tree lists A with a weight; spawn-brief → draft with parent.

### Phase 3 — Advisor
- [x] 9. `api/llm.py` (Fable→Opus `complete()` + tolerant JSON), `api/hypothesis.py` refactored onto it.
- [x] 10. `api/prompts/advisor.md` (hard rules: JSON only, cite evidence, never recommend what exists, `harness_import: null` is valid, ≤5 tools, name gaps by question number).
- [x] 11. `api/advisor.py` runner: candidates, catalogue, budget, one call, validation, record + edges + events; `_spawn_advisor`; lock/stale handling; skip on empty tree.
- [x] 12. Routes advise / recommendations; `tests/test_advisor.py` with a fake client (refusal → Opus; unknown ids dropped; unchanged hash → no run; empty tree → no call).
- [x] 13. UI: `AdvisorCards` with polling, gaps, related, harness, tools, footer.
- Verify (live, ≈$1): tenant A has a tagged version (seeded like the R17 stress test); B registers an adjacent statement → record `ready` within ~30 s, `served_by` recorded, harness points at A's version, tools exclude base tools; re-register unchanged → skipped.

### Phase 4 — Harness import
- [x] 14. `api/imports.py`: `fetch_ref()`, pre-gate smoke in a temp worktree, HITL request writer for `evo-import-*`, `apply_adopt()` (tag, meta, activate under lock), `apply_merge()` (worktree, 3-way, smoke, ff-merge, delegate on conflict), rollback target, ledger.
- [x] 15. `POST /hitl/{sid}/{rid}/answer` hook; `POST /projects/{pid}/imports`; `GET /projects/imports/{iid}`; `scaffold/archive.py` (platform copy) additive `record_imported_version` / `imported_from` passthrough; `HitlPanel` renderer for `harness_import`.
- [x] 16. `tests/test_imports.py`: two temp tenants; fetch; smoke; pending record shape; approve → tag + meta + detached HEAD, `state/` sha256 identical; reject prunes the ref; busy/lock 409; self-import 400; unknown 404.
- Verify: curl, no model spend: B imports A's version → pending in `/hitl/evo-import-…/pending` with smoke ok → approve → `/versions` (B) active = imported node; `git log --all` shows two roots; switch back restores HEAD.

### Phase 5 — Tool import
- [x] 17. `api/imports.py::import_tools()`: source verification (`git ls-tree`), worktree off HEAD, subset checkout, role-YAML insert, commit, smoke + compat, HITL request (`kind: tool_import`), approve → ff-merge + `record_merged_version(imported_from=…)`; evolution-command fallback when the smoke fails.
- [x] 18. UI: checkboxes → approval card (diffstat limited to `tools/<name>/` + role YAMLs, smoke result) → "Imported tools" list.
- Verify (no model spend): B imports one of A's tools → pending request has smoke ok and a diff touching only `tools/<name>/` and one role YAML → approve → child version; `GET /roles` lists the tool; the fallback path is exercised once live (≈$1–3) with a tool that needs a scaffold delta.

### Phase 6 — Page assembly, docs, end-to-end
- [x] 19. Five-step wizard wired; Review shows harness + tree context; launch gated while an import approval is pending.
- [x] 20. Docs: this plan ticked, ROADMAP, CLAUDE.md pointer; ui3/README.
- [x] 21. End-to-end walkthrough on the live container (see Notes, 2026-09-06 delivery); `pytest tests/ -q` (132 passed); `npm run build`; push gate.

## Files touched

- `docs/plans/O2-project-tree-advisor.md` (this), `ROADMAP.md`, `CLAUDE.md` (pointer)
- `api/onboarding.py`, `api/schemas.py` — brief v2, registration validation
- `api/projects.py` (new) — registry, edges, BM25 adjacency, links, visibility
- `api/advisor.py` (new), `api/llm.py` (new), `api/prompts/advisor.md` (new); `api/hypothesis.py` refactored onto `api/llm.py`
- `api/imports.py` (new) — fetch, pre-gate smoke, HITL request writer, adopt/merge, ledger
- `api/server.py` — routes above; launch links the session; `_monitor_runtime` sync hook; `hitl_answer` import hook
- `scaffold/archive.py` (platform copy, additive) — `record_imported_version`, `imported_from` passthrough
- `tests/test_onboarding.py`, `tests/test_projects.py` (new), `tests/test_advisor.py` (new), `tests/test_imports.py` (new), `tests/fixtures/onboarding_brief_v1.json` (new)
- `ui3/src/components/OnboardingView.tsx`, `AdvisorCards.tsx` (new), `NearTree.tsx` (new), `ProjectTreeView.tsx` (new), `ImportApprovalCard` inside `HitlPanel.tsx`, `EvolutionGraph.tsx` (forest + imported label), `FilesPanel.tsx` (library download), `SessionRail.tsx`, `Workbench.tsx`
- `ui3/src/lib/{api,types,mock,eventVM}.ts`, `ui3/src/state/store.tsx`
- Not touched on purpose (frozen per tenant): `research/`, `evolution/`, `roles/`, `prompts/`, `tools/`, `Dockerfile`, compose files

## Verification

Per CLAUDE.md, every phase ends with a curl-driven pass (listed under each
phase). The whole feature is done when the Phase 6 walkthrough holds on the live
container: A evolves (or a seeded `ver/` tag) and registers; B onboards with
the five questions, uploads and downloads data, sees A on the near-tree,
receives the advisor's harness and tool recommendations, approves the harness
import (B's active version becomes the imported node; `state/` byte-identical),
approves the tool import merge, optionally runs hypotheses, launches; B's
`events.jsonl` shows `session.brief` with `harness` and `tree_context`, and the
supervisor's first reply restates the question and the harness it runs on.
Model spend for the full pass stays under $5.

## Notes

- 2026-09-06 — Design pass: three independent designs (minimal plumbing;
  tree-first; advisor-first) were produced and scored by three independent
  judges (totals 101 / 81 / 94). The minimal-plumbing design won on codebase
  fit and simplicity; grafts taken
  from the others: a real HITL pending record in an API-created `evo-import-*`
  session (not a parallel decide route), a pre-R17-tolerant archive reader,
  the advisor kicked on leaving step 1 with a status pill on every step,
  statement review keyed to the five question numbers plus `snapshot.json`,
  `include_skills` on harness import, the completion sync hook, a
  deterministic API-side tool import with the evolution command as fallback,
  the version discoverability rule, deterministic `risks[]`, daily advisor caps
  with `usage` on the record, `validation_notes`, and the correction that the
  lineage canvas already lays out multiple roots. Rejected as too
  heavy for now: a durable advisor job store with dead-PID resume and daily
  caps (the lock + hash dedupe cover today's scale), a `harness_merge` patch
  mode as the default (merge is offered, adopt is the default for fresh roots).
- Verified today: a `git fetch` of a `ver/*` tag between two sibling repos on
  one filesystem works with unrelated histories (scratch pair); no tenant has
  a `ver/*` tag yet (R17 backfill pending); one tenant has a pre-R17 archive
  node with `decision.json` + `rationale.md` only.

## Delivery facts (2026-09-06)

- **Built on `feat/O1-onboarding`**, not a separate branch. Everything in
  `api/` + `ui3/`; `scaffold/archive.py` gained two additive helpers
  (`record_imported_version`, `annotate_version`) and `api/tenancy.py` now
  writes `.git/info/exclude` for the tenant marker (an adopt checkout was
  refused by "untracked file would be overwritten" before that).
- **Adjacency scorer is hand-rolled.** `rank_bm25` returns negative scores
  on tiny corpora, so `api/projects.py::bm25_scores` is BM25 with the Lucene
  IDF normalised by the statement's own score (threshold 0.12, ≥3 shared
  informative terms). Real adjacent statements score ~0.15–0.25; unrelated
  ~0.01. `index.json` was not needed (nodes are read directly).
- **Versions used vs inventory.** `links.harness_versions` holds only the
  versions a problem RAN on (active at register/launch/completion); the
  owner's full inventory is owner-only (`harness_inventory`). Discoverability
  and the advisor catalogue read the former.
- **Advisor runner** is `python -m api.advisor` with cwd = the platform root
  (never a tenant's frozen fork); the server holds `running.lock` on the
  child's behalf from the spawn; a statement change during a run writes
  `rerun.flag` and the pass goes again. Daily caps: 40/user, 400 global.
- **Imports.** Ledger records exist from the first moment (`preparing`) so
  concurrent requests cannot prune each other's smoke worktree; approvals
  refuse (409, request kept pending) while a turn runs or a switch is in
  flight, then apply under the switch lock for all modes; the apply re-checks
  HEAD against `rollback_to.sha`; deleting the `evo-import-*` session
  withdraws the import; a `pending` record whose request file vanished
  self-heals to `failed`. The pre-gate smoke runs with a minimal env (no
  inherited secrets), a throwaway TMPDIR and CPU/memory/file rlimits.
  Merge mode is decomposed (new tool packages → subset checkout; role wiring
  → textual insert; the rest → `git apply --3way`) so the common case never
  conflicts.
- **Live walkthrough** (local container, two fresh accounts): Quantum lab
  evolved `tools/decay_fit/` through the real evolution agent (approved
  merge → `ver/…`), registered + launched its charge-noise problem; BOLD demo
  registered three problems (two-tone transmon, LTEM Biolog with the 21-file
  data folder uploaded, cartilage scaffolds). The advisor (served by
  `claude-fable-5`, no refusal on the biology statement) recommended adopting
  the Quantum lab harness for the two-tone problem (confidence 0.85, no
  risks) and nothing for the other two ("start from scratch" with question-
  keyed gaps); the adopt import fetched, passed the 16-test smoke gate in a
  worktree, was approved through `POST /hitl/evo-import-…/…/answer`, and left
  the importer on an `imported` version with two roots and a clean tree; all
  four sessions launched, their supervisors restated the brief and the
  harness (`decay_fit`, version 647f1199) and paused at the first checkpoint.
  Model spend for the whole pass ≈ $6 (evolution run ≈ $3, four first turns,
  four advisor calls ≈ $0.15 each).

## Risks

- Cross-tenant visibility is a product decision: the tree exposes titles,
  questions, significance, outcome one-liners and harness summaries to every
  authenticated user (opt-out via `private`). Confirm before real users.
- An imported harness may be older than the platform expects (a pre-R13 tenant
  lacks `latex_compile`); the smoke + compat gate proves it can read old state,
  not feature parity. The advisor's `risks[]` and the approval card list what
  the version lacks (diff of `tools/` and `roles/` against the importer).
- Two roots in the importer's git DAG after adopt. The lineage canvas already
  lays out multiple roots; only an "imported from <owner>" root label and node
  badge are needed.
- Skills are gitignored durable assets, so an adopted harness arrives without
  the donor's skills (R17's parked concern); surfaced in the card.
- BM25 over a handful of short statements is noisy; the advisor and human
  confirmation carry the weight until the tree is larger.
- Fable refuses much life-science text; the Opus fallback is on the critical
  path and `served_by` must stay visible.
- The R17 backfill (manifest + tags for pre-R17 archive nodes) should land
  first, or older tenants' evolutions are invisible to the advisor.

## References

- O1 (`docs/plans/O1-onboarding.md`), R16 (`R16-evo-reflection-trigger.md`),
  R17 (`R17-evolution-archive.md`, Version model, cross-user seed).
- Design pass 2026-09-06: three independent designs (minimal plumbing;
  tree-first; advisor-first) converged on the platform-side registry, the
  R16-shaped background runner, and git-native transport between sibling tenant
  repos; this plan is the synthesis.
