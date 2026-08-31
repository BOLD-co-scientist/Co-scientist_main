# R17 — Evolution archive & version switching

**Status:** Planned
**Owner:** unassigned
**Started:** 2026-08-30
**Done when:** A merged evolution becomes a named, switchable version node; the human can activate any archived version and *continue* an existing research session on it; switching a version out and back — with no manual edits in between — leaves all prior session/memory/library data byte-identical; a version whose code can no longer read prior on-disk state is auto-rejected at the merge gate before the human is asked.

## Why

Today a single evolution runs (worktree → edit → `propose_merge` → HITL → `git merge --ff-only` into the tenant's `main`), but nothing *after* one evolution is maintained: the `evo/*` branch is deleted, `main` is forced linear, there is no stable version identity, no way to switch the running system back to an earlier version, and no guarantee that switching preserves in-flight research work. R17 turns the accumulated evolutions into a maintained **archive (tree/DAG)** with **first-class version switching**, designed so that switching versions is a *safe* operation a user can perform freely, and so the archive can later extend across users.

This plan is design-first. It records the boundary decisions reached in the R17 design discussion (2026-08-30) before any code. Read the **Design** sections top-to-bottom; they are the load-bearing part.

---

## Design goal (first principle)

**The system must be able to rewrite any of its own code between versions, while never breaking or losing the work and data accumulated under earlier versions.**

From this one goal comes the whole architecture, as a single binary split:

> **Code is per-version and disposable. Data is cross-version and durable.**

- A **version** = everything under a tenant's harness root *except* `state/` — i.e. the committed git tree (`scaffold/`, `research/`, `evolution/`, `tools/`, `roles/`, `prompts/`, `api/`, `ui3/`, …). Switching versions swaps this whole tree atomically.
- **Data** = everything under `state/` — sessions, memory, skills, library, hypotheses, the evolution archive itself. It is written by whatever version was live at the time and must remain interpretable by every later version.

### Why this is already true in the codebase

- **Multi-tenant = per-user git repo.** `api/tenancy.py:92 ensure_user_root` gives each user their own harness root at `state/users/<uid>/root/` (a copy of the code dirs) with its own `.git`. Evolution worktrees branch off *this* repo and merge into *this* `main`; `scaffold/sandbox.py:111 history()` already reconstructs its commit DAG per-user.
- **`state/` is gitignored** (`.gitignore`: `/state/`, `worktrees/`). So a `git checkout` of a different version **cannot touch `state/`** — the "switch out and back = unchanged" guarantee is provided by git's own semantics, not by discipline.
- **The body is rebuilt fresh every turn.** `research/runtime.py:80 _build_options` re-reads roles/prompts/tools/skills/model from current disk on *every* research turn (each turn is a fresh subprocess). So role/prompt/tool evolutions are already picked up on the next turn with no migration — most evolutions are switch-safe by construction.

R17 is therefore largely: **formalize + name + surface** the per-user git DAG, add a switch operation, add the durable archive layer, and add the one enforcement gate that keeps new code able to read old data.

---

## Editing-freedom spec (three tiers)

*This is the project's first formalization of what the system is free to rewrite about itself and what it is not.* Everything the system contains falls into exactly one of three tiers.

### Tier 1 — Frozen invariants (the floor; evolution cannot touch these)
Safety/security + durable-data integrity. **Enforced at the tool layer and the merge gate, not in prompts** — an evolution that would break one is auto-rejected before the human is asked.
- **HITL gates every sensitive action** — above all the evolution merge itself (`scaffold/hitl.py`, `propose_merge.py`). Evolution may reshape the HITL *UX*, never remove the *gate*.
- **Worktree path-guard + sandbox skeleton** (`sandbox.in_worktree`, `evolution/tools/edit.py`, `bash_sandbox.py`).
- **Non-root Docker; no docker socket in the container** (BOLD Rule 1; the longjob host-spooler trust split).
- **`state/` durability + the anchors below.**
- **The merge smoke/compat gate itself** (a version cannot weaken its own gate).

### Tier 2 — Evolvable harness (capabilities). Splits in two, by what the tenant repo actually contains (`api/tenancy.py` `_COPY_DIRS`):
- **Tier 2a — per-tenant agent layer** (IN the tenant repo; evolution *can* edit; **swapped by a version switch**; read fresh per turn): `scaffold/` logic, `research/`+`evolution/` runtimes, `tools/`, `roles/`, `prompts/`, methodology, critic/synthesis layers, models/tiers. **This is the entire editing surface of the evolution agent** — its worktree branches from the tenant repo and the path-guard confines it there.
- **Tier 2b — shared platform** (NOT in the tenant repo; single shared instance from `/app`; **human-evolved only, not touchable by the tenant agent**; NOT per-tenant-versioned, so a switch never swaps it): `api/`, `ui3/`, `bin/`, `coscientist_cli/`, `deploy/`, old `ui/`.
- **Decision (R17) — keep the split (option b).** Do NOT bring `ui3`/`api` into per-tenant evolvability now. They are genuinely single-instance shared infra (one API process, one served UI for all tenants); making them per-tenant-evolvable = per-tenant servers/frontends = a different architecture, and it conflicts with the two-agent + minimal-scaffold philosophy (evolution grows the *agent*; the human-facing platform is human territory). The design is *preserved, not foreclosed* — extension path if ever wanted: (i) add them to `_COPY_DIRS`, (ii) build the platform blue-green/reload machinery (the deferred reload OPEN), (iii) per-tenant instances or a version-routing layer. All future/out-of-scope.
- **Correction:** an earlier draft of this spec wrongly listed `api`/`ui3` as agent-evolvable Tier-2. They are **Tier-2b (human-only)**; the evolution agent cannot see or edit them (not in its repo).

### Tier 3 — Durable data (accumulated work — a switch never touches it)
Cross-version, lives under gitignored `state/`: research sessions, memory (incl. R2 global lessons), library, hypotheses, and **the archive itself**. Not "frozen" but **additive-only + migratable**: a version may change a Tier-3 schema only additively (new optional fields / appended lines), or by shipping a migrator + bumping `schema_version`.

**Where evolution-flow and HITL sit (they straddle tiers, deliberately):**
- Evolution flow: its **sandbox skeleton + merge gate = Tier 1 (fixed)**; the agent's **methodology / what it edits / how it explores = Tier 2 (free)**. Evolution can rewrite its own method but not its own safety gate.
- HITL: its **guarantee (a human gates sensitive actions) = Tier 1 (fixed)**; its **rendering / interaction flow = Tier 2 (free)**.

**Where skills sit — Tier 2 (harness), like prompts.** A skill is a *modular instruction module* — effectively a special, reusable prompt the agent can invoke. Prompts are Tier-2 harness, so a skill is the same kind of thing and is version-bound too. The right distinguishing axis is **not** "text vs code" (a prompt is also pure text, yet harness) but **authored capability (how the agent works) vs accumulated knowledge (what it learned)**: tools, roles, prompts, workflows, skills all define *how the agent works* → Tier 2; sessions, memory/lessons, library, hypotheses record *what accumulated* → Tier 3. Consequences: switching versions correctly changes the skill set (exactly as it changes prompts/roles); a skill authored under a node lives on that node and its descendants. Cross-user: each user's repo carries its own skills, base skills seeded at tenant bootstrap, cross-user sharing = future promotion. (Soft edge: a skill may reference a tool a given version lacks → not fully applicable, not a hard break.)

### Frozen anchors (Tier-1 roots you cannot migrate around — you need them to *find* the data)
1. **state root location + session directory layout** (`settings.STATE`, `session_dir(sid)` = `state/sessions/<sid>/`).
2. **`CLAUDE_CONFIG_DIR` → `state/.claude`** (where the SDK stores the resume transcript; move it and `resume=` breaks).
3. **the evolution-archive reader** must always read older archive formats (the archive is the map used to navigate/switch versions).
4. **the longjob spool schema** — a cross-boundary contract with the host-side `deploy/flair_spooler.py` (different release cadences); more like an external API.

### Freedom boundary — concrete examples
**CAN (Tier 2a, agent):** add a `critic` subagent that reviews each researcher report; add a plotting/literature-search MCP tool; change the checkpoint methodology; add a synthesis phase; swap the memory retrieval algorithm (BM25 → hybrid) keeping the JSONL schema; rewrite the supervisor prompt; enrich event logging additively. **(Restyling the UI / changing the API is Tier-2b — human-only, not the agent.)**
**CANNOT (Tier 1, auto-rejected at the gate):** remove the HITL gate on merges; make the evolution agent run Docker as root or hand the container the Docker socket; relocate `state/` or the session layout; ship code that can no longer read an existing session's `events.jsonl`; weaken the smoke/compat gate; write outside its worktree.

---

## Durable state inventory (the contract surface)

Everything below is written under `state/` and must survive version switches. `scratch/` is the only explicitly-transient exception.

### Per-session `state/sessions/<sid>/`
| Artifact | Schema (writer) | Role |
|---|---|---|
| `events.jsonl` | `{id, ts, session, actor, kind, **fields}`; ~60 `kind` values (`scaffold/eventlog.py`) | **Audit + what the UI renders.** Viewing any historical session depends only on this. |
| `sdk_session.json` | `{sdk_session_id, turns, updated}` (`settings.py:154`) | Resume pointer (UUID handoff). |
| `memory/agent/*.jsonl`, `memory/project/shared.jsonl` | `{id, ts, agent, text, tags}` (`scaffold/memory.py`) | Session memory. |
| `hitl/pending/*.json`, `hitl/answered/*.json`, `control/chat/*.json` | see `scaffold/hitl.py`; decisions ∈ {approve, reject, answer, edit, interrupted} | Pending human gates. |
| `control/stop`, `control/autonomous`, `control/research_active/<sid>` | marker files (`api/server.py`, `scaffold/hitl.py`) | Runtime signals (research_active holds PID). |
| `results/` | deliverables (.tex/.pdf/ocr output) | User output. |
| `reflection.json` | `{reflection, proposals[], toolcalls_at_reflection}` (`research/reflect.py`) | R2/R16 reflection. |
| `deep_research/*.json`, `jobs/*.json` | durable handles (`tools/deep_research/store.py`, `tools/longjob/job.py`) | In-flight async work — must stay readable so a switch doesn't orphan a running job. |
| `scratch/` | py_exec / latex work dirs | **Transient — NOT in the contract.** |

### Cross-session / global (per tenant)
| Artifact | Role |
|---|---|
| `state/archive/evolutions/<node>/` (`diff.patch`, `rationale.md`, `decision.json`, `revert.patch`, `smoke.log`) | **The version tree meta-state. Most critical: must be forward-readable (anchor 3).** |
| `state/memory/global.jsonl` | Cross-session lessons (R2). |
| ~~skills~~ | **Tier-2 harness, not durable data** (a modular prompt; see Editing-freedom spec). Lives on the code side, version-bound. Bug is that authoring doesn't commit — see Bugs to sweep. |
| `state/library/`, `state/library_events.jsonl` | Uploaded files (UI2). |
| `state/users/`, `state/auth/users.db` | Tenancy + auth. |
| `state/jobspool/` | Longjob spool (trust boundary with host spooler). |
| `state/.claude/` (`CLAUDE_CONFIG_DIR`) | **SDK-owned** conversation transcript that `resume=` replays. External dependency; see SDK-version note. |
| `state/hypotheses/*.json` | Hypothesis-engine sessions (H1). |

**Two representations of a session — keep them distinct:**
- **`events.jsonl` (ours)** → drives **viewing/rendering**. Always works across versions as long as the schema stays readable.
- **SDK transcript (`state/.claude`, SDK-owned)** → only used to **continue** the conversation (`resume=`). Only continuation depends on it.

---

## Version model — tag as truth, a tree/DAG (not linear)

**Decided:** the active version is a **node in a tree/DAG**, identified by an immutable `ver/<id>` annotated tag — *not* the tip of a linear `main`. At any moment the user is simply *sitting on some node*; every version looks the same to them (a node in the archive), whether they got there by switching or by growing a new one. Retire the `--ff-only`, keep-`main`-linear model (`sandbox.py:88`); the tag is the source of truth, `main` at most becomes a convenience pointer to one recent tip.

- **A version = a git commit made switchable by `ver/<id>`.** `id` = timestamp+slug (or a monotonic counter). "Switch version" = `git checkout <ver-tag>` in the one active working tree; `state/` (gitignored) is untouched.
- **A durable archive manifest on top of git.** Formalize `state/archive/evolutions/` into an indexed DAG: per-node `meta.json` + top-level `index.json`. Each node: `id`, `git_sha`, `tag`, `parent_ids[]`, `summary`, `rationale`, `direction`, `owner`, `schema_version`, `smoke`, `status` (proposed | merged | rejected | superseded | active), timestamps — and keeps the existing `diff.patch`.
- **Why a manifest when git already has the DAG:** (1) durable data under `state/` (survives switches, unlike deletable branches); (2) node metadata git doesn't hold; (3) it stores the patch, so a node is **portable to another user's repo by applying the patch** → the **cross-user seed**: git stays per-user, the archive is the shareable layer. A future shared archive imports nodes by `owner` + patch, no shared git required.

## Evolution & version-switch mechanics

- **One active working tree.** The tenant root (`state/users/<uid>/root/`) is the single tree the running system executes from. **Switching a version = `git checkout <ver-tag>` in this tree** (detached HEAD at the tag). The user's leaning is adopted: *load a version out via git* — which means the same mechanism extends to a **remote** later (push tags/commits to a shared remote → cross-machine / cross-user version transport), with the archive's `diff.patch` as a git-independent fallback path.
- **Evolution runs in separate worktrees.** An evolution branches off the chosen node's commit into an isolated `root/worktrees/<name>` on an `evo/*` branch, edits + tests there, then proposes. Because the active tree checks out **tags** (detached) and worktrees check out **branches**, they never collide (git only forbids two worktrees on the *same branch*).
- **Untracked / uncommitted handling.** A worktree is a clean checkout of the base commit, so anything untracked/uncommitted in the active tree does **not** leak into the evolution — evolution is isolated by construction. Corollary: the active *code* tree must hold **no** durable untracked files (all durable data is in gitignored `state/`); the current skills-in-`.claude` case is the lone violator → Step 2.
- **Worktree lifecycle.** Worktrees are scaffolding for *building* a version. Once merged and tagged, the version lives permanently in git and any worktree can be recreated from the tag → discard the worktree after merge (a rejected worktree is retained for revise-and-re-propose; already the behavior).
- **Growing = same DAG.** A new evolution off the active node adds a **child** node; switching moves the active pointer to any node. Growth and switching are the same tree operation from different directions.
- **Operational note (to resolve in impl):** research/evolution turns are per-turn subprocesses that read the freshly-checked-out code, so they pick up a switch on the next turn automatically; the long-lived API-server process, being Tier-2 code too, stays on its old code until restarted. Decide whether a switch signals an API reload or is defined as affecting subprocess-spawned work only.

---

## Version switching + session continuity

Switching is `git checkout <ver-tag>` of the code tree; `state/` stays put. The switch action itself **only checks out — it never runs migrations and never writes `state/`** (this is what preserves the safety guarantee).

- **Viewing** any session after a switch: always works (reads `events.jsonl`).
- **Continuing** a session after a switch: the next turn rebuilds the body from the new version's code and resumes via `sdk_session.json` + the SDK transcript. Because the body is rebuilt fresh, additive evolutions continue seamlessly.
- **When the target version's code cannot interpret a session's on-disk schema:** the session goes **read-only**; the user may **fork** it (copy → continue on the current version), leaving the original untouched. This is "non-destructive migration": migration is read-time / copy-on-write, never an in-place rewrite of old bytes (an in-place rewrite would mutate `state/` on mere viewing and break the safety guarantee).
- **Guard on timing:** reuse the existing idle guard (`propose_merge.py:73 _wait_for_sessions_idle`) so the code tree is never swapped under a running turn.

---

## Evolution UX model (spec for the R17 UI; approved 2026-08-31)

The Evolution experience is **one zoomable version tree + one on-demand conversation surface** — no sub-tabs, no permanent sidebar (the current Conversation|Lineage toggle and the "(no task)" research-session jump are retired).

- **The tree is the whole canvas.** A zoomable / pannable graph (à la the gemma-lineage-plot), growing **vertically upward**. Structurally a **tree today** (each evolution branches from one `base`; ff merges add no 2nd parent) but the renderer is **DAG-capable** (git can make merge nodes; future version-joins). Data = `/git/history` (all commits + in-flight `evo/*` branches) **joined by sha** with `/versions` (which commits are `ver/` version nodes + metadata + which is `active`).
- **Node types:** version node (`ver/<id>`), root (`tenant bootstrap` = v0), in-flight evolution (running/pending; a marker commit), rejected/abandoned (**greyed, retained on the tree**, not deleted). Exactly one node is **active** (== HEAD), always visibly marked; also shown in the Evolution header ("Active: `<id>`").
- **Click a node → a popover/modal anchored at the node:** compact meta (summary, rationale, diff stat, smoke, status) + actions **[Activate this version] · [Evolve from here] · [View conversation]**. May expand-in-place to reveal normally-hidden commits.
- **The conversation is a slide-in, collapsible drawer** over the tree — the single conversation surface for both a live/in-flight evolution (events stream + merge-approval gate) and a node's **birth-story** (how that version was made). Never routed into the research session list.
- **Three verbs → backends:** Inspect = `/versions` + `/git/commit/{sha}` (exist). Evolve-from = `/evolution/commands` with `base` (exists), opens the drawer in place. **Activate/Switch = `POST /versions/{id}/activate` (P2 — the one new backend).**
- **Switch UX:** idle-guarded (never mid-turn; same wait/stop pattern as merges); rollback to an ancestor allowed → sessions on a newer schema go read-only + fork (P3); zero reload today (Tier-2a only).

**Evolution conversations are Tier-3 durable data, keyed to their node.** They already persist as session-shaped `events.jsonl` under `state/`; R17 links each to the version node it produced (its provenance) and surfaces it in the drawer. Because they live in gitignored `state/`, they are **preserved across version switches** by construction — you can read how *any* node was made from *any* version you're standing on. They are NOT merged into research sessions (research = doing science; evolution = changing the system — two distinct agents).

**Build split:** R17 (this track) owns the R17 backend **and** this UI together (they are tightly coupled) on `feat/R17-evolution-archive`; the parallel `fix/ui3-bugs` track owns only the standalone UI bug-fixes (duplicate bubble, KaTeX math, stale-evo-state on account switch). Coordinate by merging `fix/ui3-bugs → feat/R17` as fixes land; the two touch mostly different `ui3` files.

## Hard-constraint implementation (how the boundary is *enforced*, not just promised)

Two properties, two mechanisms:

1. **"Switch out and back = unchanged"** → **enforced by git**: `state/` is gitignored, so `checkout` provably cannot touch it. Requires the discipline that *all durable data lives under `state/`* (hence the skills-location bug must be fixed). A test can assert activating then deactivating a version leaves `state/` byte-identical.

2. **"New code can still read old data"** → **an explicit contract registry + a golden-fixture backward-compat test wired into the merge gate:**
   - `scaffold/contract.py` — the single explicit declaration of the frozen surface: current `SCHEMA_VERSION`, the enumerated durable schemas + their versions, and the anchors. Independently editable.
   - `tests/fixtures/golden/` — captured on-disk `state/` samples written by older versions (a session, a memory file, an archive node, …).
   - `tests/test_contract_compat.py` — asserts the current code can **read / render / fork-continue** every golden fixture. **This test is the hard constraint.**
   - Wired into `evolution/tools/propose_merge.py`'s smoke gate: a proposal that would break reading old state **fails in the worktree → `auto_rejected` → the human is never asked**; the worktree is preserved so the evolution agent revises and re-proposes (same flow the strict-path smoke gate already uses at `propose_merge.py:131`).
   - **Maintaining future frozen interfaces:** when a new thing must be frozen, (a) add it to `scaffold/contract.py`, (b) drop a golden fixture capturing its current shape, (c) the compat test enforces it thereafter. One place, independently maintainable.
   - **Runtime backstop:** a validating loader stamps/reads `schema_version`; data it is too old to understand is opened **read-only** rather than mutated.

**SDK-version note:** the resume transcript in `state/.claude` is SDK-owned; we can't migrate it. So the SDK version is *part of the durable contract*. An evolution that bumps the SDK is **controlled** — gated behind an explicit deliberate change plus the compat test plus a live-resume smoke (resume a golden session must still succeed); it is not a silent side-effect. If a transcript truly cannot resume, the session degrades to read-only + fork. No data loss.

---

## Resolved design questions (from the 2026-08-30 discussion)

1. **Rollback direction.** Additive-only schema is the default discipline + per-session `schema_version` stamp. A version too old to understand a session may **view** it but not mutate it; the user forks to continue. Accepted trade-off: "roll back → newer sessions are read-only on the older version."
2. **SDK upgrades.** Controlled + gated (above), graceful degradation to read-only+fork; not "ignored."
3. **Tool renames/removals inside a persisted transcript.** Handled by **alias/shim** (register the old tool name as an alias to the new impl) so old transcript references don't dangle; or accept as a soft edge (history is not re-executed). Discipline: tool names only-add; rename ⇒ ship an alias.
4. **Migrator engine.** **Deferred** — do not build now (premature; minimal-scaffold philosophy). Add only the *convention + enforcement* now (`schema_version` stamps, additive rule, the compat gate, a stub registration point). Build the actual migrator when the first non-additive change forces it; at that point the migrator registry becomes a small fixed part of the substrate.

---

## Bugs to sweep (opportunistic, while touching these surfaces)

- **Skills are written but never committed → limbo.** Skills are Tier-2 harness (version-bound), correctly on the code side, but R7 `skills.save_skill` writes `root/.claude/skills/*` at runtime **without committing** — so an authored skill is uncommitted working-tree state that a `git checkout` would clobber. Fix: **authoring a skill commits it into the active version** (a lightweight evolution; HITL-gated already), and base skills are tracked + seeded at tenant bootstrap (`_COPY_DIRS` currently omits `.claude`). Then skills travel with the version exactly like prompts. **Prerequisite for correct version switching.**
- **Duplicate-message rendering** (from the now-void PR #7): `message.received` + `human_directive` both render as chat bubbles. Sweep if we touch the event/render surface.
- **B2:** `evolution/runtime.py` truncates `evolution.note` text (historically `[:400]`) — raise/drop the cap so long agent narrations are readable.

---

## Steps

- [x] 1. Land this design doc + add R17 to [ROADMAP.md](../../ROADMAP.md) (🟢 Now). Commit.
- [x] 2. **Skills = durable Tier-3 asset (NOT version-bound) for R17** — decision reversed (pass 6, see Open questions). Skill authoring keeps the R7 flow (save on HITL approval, audit under `state/archive/skills`); `.claude/skills/` is **gitignored** so a checkout never swaps it (satisfies "switch out and back = unchanged" trivially, exactly like `state/`). No commit, no `ver/` tag, no lineage node. Base-skill seed at bootstrap retained (into the gitignored dir). Two open concerns (cross-user completeness, accumulation) parked for post-R17.
- [x] 3. **Contract registry**: `scaffold/contract.py` declares `SCHEMA_VERSION`, the durable-schema list, and the anchors.
- [x] 4. **Version identity**: on merge, tag each merged node `ver/<id>` (archive.record_merged_version). `main`/`master` is just a pointer; active = HEAD.
- [~] 5. **Archive manifest**: per-node `meta.json` + `index.json` under `state/archive/evolutions/` ✅; `/versions` read API ✅; provenance `origin_session` ✅. **Remaining: backfill** manifest+tag for pre-R17 merged evolutions (old tenants with archive folders but no meta/tag).
- [x] 6. **Switch operation**: `POST /versions/{id}/activate` (detached checkout), idle-guarded + switch-lock; no `state/` writes.
- [x] 7. **Session continuity across switch**: `schema_version` stamp on new sessions; too-new sessions open read-only (runtime guard); **fork-to-continue** endpoint + UI banner/button. Verified end-to-end.
- [x] 8. **Golden-fixture compat gate**: `tests/fixtures/golden_v1.json` + `tests/test_contract_compat.py`, wired into `propose_merge`'s smoke gate.
- [x] 9. **UI**: zoomable lineage canvas, node modal, Activate/Evolve-from (gated to versions), active-version marker, provenance "View conversation", read-only banner + fork button. (D2 in-flight→ended node = follow-up todo.)
- [x] 10. Sweep bugs: duplicate-message render (delegated to `fix/ui3-bugs`); B2 note truncation — **not reproducing** (evolution.note renders full in a wrapping spine, no backend/frontend cap).
- [ ] 11. Backend verification pass (curl-driven) + a switch-and-back byte-identical check on real `state/`; then the PR.

## Files touched

- `docs/plans/R17-evolution-archive.md` (this file), `ROADMAP.md`
- `scaffold/contract.py` (new), `scaffold/sandbox.py`, `scaffold/settings.py` (skills path), `scaffold/skills.py`
- `evolution/tools/propose_merge.py`, `evolution/runtime.py`
- `api/server.py`, `api/schemas.py`, `api/tenancy.py` (skills dir), `.gitignore`
- `tests/fixtures/golden/**` (new), `tests/test_contract_compat.py` (new), `tests/test_smoke_v0.py`
- `ui3/src/**` (version graph actions, active-version + read-only/fork UI)

## Verification

### Scenarios (edge-touching but realistic — use to *feel* the freedom + switch UX)
1. **Time-travel A/B.** User evolves in a `critic` subagent (Tier 2), runs a research task, dislikes the output; switches back to the parent node and re-runs the *same* session to compare — **while a longjob dispatched under the first version is still running** (its handle in `state/` must stay pollable across the switch).
2. **Branch-and-compare.** From one node the user grows two children — one adds deep-research-heavy methodology, one adds three more subagents — runs the same task on each, and compares. The archive shows a real fork, not a line.
3. **Rollback-during-session (the safety headline).** User is mid research session, evolves the system, the new version has a bug; user switches back — the in-flight session **continues intact** on the next turn, and `state/` is byte-identical to before the failed hop.
4. **Skill authored, then time-travel.** A session crystallizes a skill → it is committed into the active version (a child node). Switching to an *older* node: skill absent (expected — Tier-2, authored later, just like a prompt). Switching forward: skill present. Confirms skills travel with the version like prompts.
5. **Un-switchable session → read-only + fork.** An old session used a tool a much newer active version renamed; continuing it triggers **read-only + fork-to-continue**, original preserved.
6. **Cross-user promote (future-facing, don't build now).** User A's evolution (a great new tool) is imported into User B's archive by applying its `diff.patch` — validates that the node is portable without shared git.
7. **Gate catches a foot-gun.** An evolution accidentally changes the `events.jsonl` shape non-additively; the golden-fixture compat test fails in the worktree; the merge is `auto_rejected` before the human is even asked.

### Checks
- **Safety guarantee:** create a session under version A; switch to B and back to A with no edits; assert `state/` is byte-identical (diff/hash the tree).
- **Continuity:** continue an existing session after an additive-evolution switch; the turn resumes with full prior context (SDK resume works).
- **Read-only + fork:** simulate a session whose `schema_version` the active version can't handle; assert it opens read-only and fork-to-continue produces a working new session, original untouched.
- **Merge gate:** a proposal that breaks a golden fixture is `auto_rejected` before HITL; worktree preserved.
- Curl-driven backend pass over the new `/versions` + switch endpoints; `pytest tests/test_smoke_v0.py -q` + the new compat test green.

## Risks / open questions

- **~~`main`-linear vs real DAG~~ — DECIDED:** tag-as-truth DAG; retire `--ff-only`-linear `main`. See Version model.
- **Switch vs long-lived processes — mostly MOOT for the current architecture; the hard part deferred.**
  - *Why mostly moot:* a tenant version = the tenant repo (Tier-2a only). The shared platform (API server, `ui3`) is **Tier-2b — not in the tenant repo and not per-tenant-versioned**, so a tenant switch **never swaps it** → the long-lived API server is never on stale code because of a switch. A switch only swaps Tier-2a, which the per-turn subprocesses read fresh → **zero reload, ever, today.**
  - *When the hard part returns (deferred OPEN):* only if we ever make the platform itself per-version (the Tier-2b extension path). Then "how to reload an expensive-to-restart long-lived process" matters — e.g. the API server holding many live SSE streams. Options for that day: (a) in-place graceful restart (cheap iff ~stateless); (b) **blue-green via a temporary worktree** — boot the target's process from a parallel checkout, drain + hand off, retire old → zero-downtime; (c) multiple versions side-by-side + routing. Not now.
- **~~Skill visibility across the DAG~~ — RE-OPENED, then DEFERRED (2026-08-31, pass 6):** the pass-3 decision "skills are version-bound Tier-2, authoring commits into the version (a node)" was **reversed for R17**. It caused two problems the user surfaced: (i) minting a *code-version node* from a *research* session is a category error (research produces assets, evolution edits code), and (ii) it creates a confusing "two evolution granularities" model. Better axis for the *durability* question: **authored-by-editing-code (evolution → version-bound) vs produced-by-the-running-system (research artifact → durable Tier-3 asset, like memory/results/hypotheses).** A skill is *produced* by a running session, so for R17 it stays a **durable, cross-version asset**: saved on approval (HITL, unchanged R7 flow), audited under `state/archive/skills`, **not committed, not a lineage node**; `.claude/skills/` is gitignored so a checkout never swaps it. **Two genuinely-open concerns to revisit (post-R17), both premised on "skill = harness":** (1) **cross-user completeness** — evolving/switching *from someone else's version* would not carry that version's skills, so their harness would be incomplete; (2) **accumulation** — skills pile up across evolutions/rollbacks and may dilute retrieval hit-rate. If either bites, revisit version-binding (or a hybrid: version-scoped skill *sets* over a durable skill *pool*). The lineage tree therefore shows **evolution versions only**.
- **SDK transcript portability** across container rebuilds and SDK bumps — the one durable dependency we don't own. Needs a live-resume smoke in the gate.
- **Cross-user archive** — out of scope to *build* now; only the manifest shape (owner + portable patch) must not foreclose it.
- **Longjob spool schema** — a cross-boundary contract with the host spooler; freeze carefully if touched.

## Notes

- 2026-08-30 (pass 1) — Converged on "code disposable / data durable," grounded in the per-tenant git model (`state/` gitignored ⇒ switch safety is a git property). Boundary shrank from an earlier "5 frozen interfaces" to "state/ is a migratable data store + 4 anchors"; UI and API confirmed **free** (co-shipped per version). Migrator engine deferred; enforcement = explicit `contract.py` + golden-fixture compat test in the merge gate. Skills-location identified as a real bug — promoted to a prerequisite step.
- 2026-08-30 (pass 2) — Formalized the **three-tier editing-freedom spec** (the project's first): Tier 1 frozen invariants / Tier 2 evolvable harness / Tier 3 durable data. Classified evolution-flow (skeleton+gate = T1, methodology = T2) and HITL (gate = T1, UX = T2). Version model decided: **tag-as-truth tree/DAG**, retire `--ff-only`-linear `main`. Mechanics settled: one active working tree (switch = `git checkout <ver-tag>`), evolution in separate `evo/*` worktrees (no collision: tags vs branches), worktrees discarded after merge (recreatable from tag), git as the version transport → remote-extensible. Parallel `fix/ui3-bugs` worktree created under `../coscientist-worktrees/` for message-dup/UI/B2 (+ the parked R12 longjob HITL renderer).
- 2026-08-31 (pass 7) — **Provenance + lineage polish + a cross-worktree infra bug.** (a) **Provenance shipped**: `record_merged_version` stores `origin_session` (the evolution session that produced the version); node modal gets **View conversation** → a read-only "birth story" drawer loading that session's events (durable under `state/`, readable from any version). Verified round-trip + smoke. (b) Lineage polish: v0 root styled as a version node (switchable baseline), commit dots smaller/hollow vs version dots, version/active/root/evo labels brightened & unified, `Ctrl+E` hint removed, Activate/Evolve-from gated to version/root only. (c) **D2 recorded as a todo (not a design)**: an `evo/*` branch whose evolution ended WITHOUT merging (agent asked clarification / user abandoned) currently stays purple "in-flight" forever. Target design: it becomes an **"ended" node (grey)** that is *version-level* — you can **Evolve from it** (branch a new evolution) — but is **not switchable / not directly usable**. Crucially, an evolution that ends this way must clean up **exactly like a merged one** (worktree removed, no lingering branch/lock/process, evolution-module + git state identical) — the ONLY difference is the node's status. Needs evo-branch↔session-running state (UI cross-refs `/sessions`, or backend marks branch state on end). (d) **Infra bug (fixed)**: the two worktrees share Docker `container_name`; a `compose up` from `fix/ui3-bugs` had remounted `/app/api` from that worktree → broke R17 `/versions` (404) + killed a running session. Recreated from main; documented the collision. (e) Batch of session-UX issues parked for a unified pass — see the session-UX memory + the summary handed to `fix/ui3-bugs`.
- 2026-08-31 (pass 6) — **Skill decision reversed + lineage UI redesign + P3 verified.** (a) **Skills → durable Tier-3 asset, not version-bound** for R17 (see Open questions; reverts the pass-3/pass-4 "skill = node" build): skill authoring stays the R7 save-on-approval flow, `.claude/skills/` gitignored, no node/tag. Two open concerns parked (cross-user harness completeness; skill accumulation vs hit-rate). (b) **Read-only guard + fork verified end-to-end** on a fresh post-R17 tenant (inject `schema.json` v2 → directive returns `session.readonly`+`idle`, zero cost, data untouched → fork drops the stamp → the fork runs and re-stamps v1). Surfaced that runtime-side R17 code only reaches a tenant via bootstrap-of-a-new-tenant or an evolution merge (per-turn subprocess runs the *tenant* code copy), not existing pre-R17 tenants — by design. (c) **Deep-research "unavailable"** root-caused to a placeholder `OPENAI_API_KEY=sk-...` in `.env` (graceful degradation worked as designed). (d) **Lineage UI redesigned** to the approved model: single zoomable/pannable canvas, **default collapsed to switchable version nodes only** (root + `ver/` + in-flight `evo/*` + active), `e`/button to expand every folded commit (edges bridged across hidden commits), node labels decluttered (no per-node sha; badge + summary, ellipsis), **click-anchored fixed popover clamped into the viewport + scrollable** (fixes off-screen/truncation), slide-in conversation drawer, "Evolve from here" streams into the drawer (fixes the "(no task)" duplicate-surface). Not committed yet — per-feature commits pending user review.
- 2026-08-31 (pass 5) — **Architecture correction + evolution-UX model.** Caught that `ui3`/`api` are NOT in the tenant repo (`_COPY_DIRS`), so the evolution agent literally cannot edit them — Tier 2 splits into **2a per-tenant agent layer** (what evolution edits, what a switch swaps) and **2b shared platform** (`api`/`ui3`/`bin`/`deploy`, human-only). **Decision (b): keep the split**, don't make the platform per-tenant-evolvable now (extension path noted). This also makes switch-reload **moot today** (a switch swaps only Tier-2a, read per-turn). Approved the evolution-UX model: one zoomable DAG-capable tree canvas + node popover + conversation drawer, no sub-tabs/sidebar; evolution conversations = Tier-3 provenance keyed to their node, cross-version-preserved, not in the research session list. Build split: R17 owns R17 backend + its UI together; `fix/ui3-bugs` owns standalone UI bugs. P1 backend (archive.py + tag-on-merge + `/versions`) built and self-verified (container smoke 13/13); awaiting the user's live evolution test.
- 2026-08-30 (pass 4) — First-principles refinements: (a) **a skill is the smallest-granularity, purely-additive, instruction-only evolution** — same *act* as an evolution (modifying Tier-2 harness), differing only in blast radius, so gate ceremony should scale with blast radius; propose_skill and evolution being separate mechanisms is an implementation convenience, not a principled split (possible future unification). (b) A skill travels with the version because *a version is a coherent, reproducible whole*; genuinely-cross-version task knowledge belongs in **memory (R2)**, not skills — keep the two mechanisms pure. (c) Reload: reload-need is *computed* from the switch diff (not a stored meta field or a frozen layer taxonomy) — this part is settled; but **how** to reload an expensive-to-restart long-lived process (in-place vs blue-green temp-worktree vs multi-version routing) is left **OPEN** for a dedicated discussion with concrete cases (see Risks). (d) `fix/ui3-bugs`: duplicate-human-bubble fixed (`dedupeHumanEcho`, verified typecheck+build+6/6 assertions) + R12 longjob HITL renderer committed.
- 2026-08-30 (pass 3) — **Corrected skills to Tier 2 (harness), not Tier 3.** The pass-2 "instruction-only ⇒ data" reasoning was wrong: a prompt is also instruction-only yet is harness. The real axis is *authored capability vs accumulated knowledge* — a skill is a modular special prompt, so version-bound like prompts/roles. Reframes the skill bug from "wrong location" to "authored-but-never-committed → a checkout clobbers it"; fix = authoring commits into the version + seed base skills at bootstrap.
