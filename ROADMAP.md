# coscientist Roadmap

Single source of truth for what's being built. Update by editing this file. Per-feature step lists live under [docs/plans/](docs/plans/).

**Convention.** Each feature has a one-liner here and a detailed plan file. To make progress, tick boxes in the plan file. The roadmap reflects feature-level state; the plan files reflect step-level state.

---

## 🟢 Now (active work, in priority order)

R1, R5, R2 are being implemented in **parallel worktrees**. See [docs/plans/PARALLEL.md](docs/plans/PARALLEL.md) for the workflow.

| ID | Feature | Status | Plan | Branch | Worktree |
|---|---|---|---|---|---|
| **O1** | Onboarding phase — fixed-format problem brief → data upload/attach → hypothesis search *before* the session → launch (supervisor gets the whole brief as its first turn) | 🟢 Built + backend-verified live (brief CRUD, live Fable generation, select→brief sync, launch, `session.brief` event). UI wizard is the default "New session"; free-form quick start kept. | [O1-onboarding.md](docs/plans/O1-onboarding.md) | `feat/O1-onboarding` | — |
| **R1** | Replayable event log (telemetry + cassette replay) | Planned | [R1-replay-event-log.md](docs/plans/R1-replay-event-log.md) | `feat/R1-replay` | `../coscientist-R1` |
| **R5** | Cost ledger + tier routing + caching | Planned | [R5-cost-tier-routing.md](docs/plans/R5-cost-tier-routing.md) | `feat/R5-cost` | `../coscientist-R5` |
| **R2** | Reflection → cross-session lessons buffer | Planned | [R2-reflection-lessons.md](docs/plans/R2-reflection-lessons.md) | `feat/R2-reflection` | `../coscientist-R2` |
| **R16** | Evolution reflection & suggestion trigger (human-gated RHI loop) — session end → reflect → propose evolutions → human picks → existing evolution | ✅ Merged (PR #8) | [R16-evo-reflection-trigger.md](docs/plans/R16-evo-reflection-trigger.md) | `feat/R16-evo-reflection` | — |
| **R17** | Evolution archive & version switching — maintain a version tree/DAG (git-tag nodes + durable archive manifest), safe version switching that preserves in-flight research (code disposable / data durable), enforced by a golden-fixture compat gate; cross-user archive-ready | In progress (core done; backfill + verification pass left) | [R17-evolution-archive.md](docs/plans/R17-evolution-archive.md) | `feat/R17-evolution-archive` | — |

## 🟡 Next (queued, plans not yet drafted)

| ID | Feature | Notes |
|---|---|---|
| **R12** | Long-job runner — async dispatch of long CPU/GPU jobs on FLAIR (host-spooler backend; py_exec auto-escalates) ([plan](docs/plans/R12-longjob-runner.md)) | 🟢 Built (core + FLAIR docker backend + auto-escalation), 33 tests green. Remaining: live FLAIR run + run the spooler on the node. R11/Isambard is the future ARM64 backend of this runner. Branch `feat/R12-longjob-runner`. |
| **R13** | LaTeX-by-default final output — deliverables become `.tex` + compiled `.pdf`; env toggle `OUTPUT_FORMAT=latex\|markdown` ([plan](docs/plans/R13-latex-output.md)) | ✅ Done (backend-verified incl. live agent run). Prompt-plumbing (`{{OUTPUT_FORMAT_GUIDANCE}}` placeholder) + new `latex_compile` tool (`results/`-only guard, `setrlimit`, shell-escape off) + texlive/latexmk in Dockerfile root stage (non-root preserved). Live run produced a real `.tex`+`.pdf`; 62 tests green. Branch `feat/R13-latex-output`. |
| **R14** | Deep research — supervisor calls OpenAI Deep Research API once per session (after scoping, before implementing) + background runs on request ([plan](docs/plans/R14-deep-research.md)) | ✅ Built + **live-verified** (host agent run: supervisor made the mandated `deep_research.run` call, auto-fell-back to `o4-mini`, delivered a cited landscape). Durable-handle tool mirroring R12; prompt mandate; enable-gated by `OPENAI_API_KEY`. Fixed a sandbox lazy-import bug + added org-verification fallback. **Action for true Deep Research:** verify the OpenAI org (platform.openai.com/settings/organization/general). Branch `feat/R14-deep-research`. |
| **R15** | Autonomous mode — per-session `autonomous: true` flag auto-answers HITL gates (except evolution merges / interrupts) for HITL-vs-autonomous benchmarking ([plan](docs/plans/R15-autonomous-benchmark.md)) | ✅ Done. API-only (no UI); auto-answers logged as `hitl.auto_answer`, `actor: "auto"` so `events.jsonl` distinguishes the arms. Built on main. |
| R6 | Verify parallel subagent dispatch + reference-passing discipline | ~½ day; prompt + test |
| R3 | Pinned plan + scratchpad as first-class context anchors | 2 days |
| R4 | Tool-grounded critique pass before user-facing emission | 2 days; opt-in flag |
| **R7** | Skill library — per-user, instruction-only skills proposed at session end + HITL approval ([plan](docs/plans/R7-skill-library.md)) | ✅ v1 done (backend + live-verified discovery). Later: admin console (view/manage/promote across users) + executable cache. |
| **B1** | Bug: container image missing pytest, so strict-path smoke gate always auto-rejects | Found during UI1 verification 2026-05-07. Dockerfile installs `.[science]` not `.[dev]`. Fix: install pytest in image (or include `.[dev]`) so `evolution/tools/propose_merge.py:_run_smoke` can succeed. |
| **B2** | Bug: `evolution.note` events truncate text to 400 chars | Found during UI2 verification 2026-05-07. `evolution/runtime.py:90` does `text=block.text[:400]`. Means long agent narrations are unreadable in the events log. Trivial fix: drop the slice or raise the cap to 8000. |

## 🖥️ UI

| ID | Feature | Status | Plan | Branch |
|---|---|---|---|---|
| **UI1** | Evolution pane in Streamlit chat | ✅ Done (backend-verified) | [UI1-evolution-pane.md](docs/plans/UI1-evolution-pane.md) | `feat/evolution_ui` |
| **UI2** | Persistent context library (upload once, use everywhere) | ✅ Done (backend-verified) | [UI2-context-library.md](docs/plans/UI2-context-library.md) | `feat/evolution_ui` |
| **UI3** | Full custom React SPA — replace Streamlit (real design freedom + SSE, no rerun jank) | 🔵 Deferred (brief ready) | [frontend-design-brief.md](docs/frontend-design-brief.md) | _(unstarted)_ |

**UI3 context (decided 2026-06-24).** The Streamlit UI hits a design ceiling (boxy layout, no glassmorphism, 2s full-page autorefresh jank). The backend is already a clean REST + SSE API, so a custom SPA (Vite + React + TS + Tailwind) is a *new client*, not a rearchitecture — spec'd in [docs/frontend-design-brief.md](docs/frontend-design-brief.md) (includes a verified API/SSE/auth appendix). The hard part is integration (Bearer auth, the SSE-can't-use-EventSource gotcha, authenticated up/downloads, CORS/same-origin), so the intended labor split is **Claude Design → visuals/mockup, Claude Code → wiring against the live backend**. Interim option when desired: beautify the existing Streamlit UI (theme + CSS) for a cheap win. Trigger to start UI3: when design/UX becomes a priority (external users or a demo).

## 🔵 Later (deferred — needs evidence first)

| ID | Feature | Trigger to revisit |
|---|---|---|
| R8 | Hybrid retrieval (BM25 + dense + reranker) upgrade | When memory layers cross ~1k entries |
| R9 | Mined-perspective question generation (STORM pattern) | After R3 lands; mostly prompt work |
| — | UI (chat + dashboard with research/evolution switcher) | When backend stabilizes |
| — | gVisor / Firecracker microVM sandbox upgrade | When researchers run untrusted external code |
| — | Embedding-only memory retrieval | Never (hybrid wins; pure-dense regresses) |

## ✅ Done

| Date | Feature | Commit |
|---|---|---|
| 2026-06-23 | **R10** — resumable multi-turn conversations (SDK `resume` + per-turn subprocess; back-and-forth like Claude Code) | _(feat/R10-resume)_ |
| 2026-04-26 | v0 backend bootstrap (scaffold, research/evolution runtimes, tools, FastAPI, sandbox) | `d6053a7` |
| 2026-04-26 | CLAUDE.md project guide | `bf44c45` |
| 2026-04-26 | Roadmap + plan-file convention | _(this commit)_ |

---

## How to use this file

- **Adding a feature:** create `docs/plans/<id>-<slug>.md` from the template, link it from the "Now" or "Next" table.
- **Working a step:** open the plan file, find the first `- [ ]`, do it, tick it. Don't batch tick-offs — tick as you finish each.
- **Finishing a feature:** flip the plan file's status to ✅ Done, move the row from "Now" to "Done" with the commit SHA.
- **Deferring:** move to "Later" with a one-line trigger condition for revisiting.

See [docs/plans/README.md](docs/plans/README.md) for plan-file template and convention details.
