# coscientist Roadmap

Single source of truth for what's being built. Update by editing this file. Per-feature step lists live under [docs/plans/](docs/plans/).

**Convention.** Each feature has a one-liner here and a detailed plan file. To make progress, tick boxes in the plan file. The roadmap reflects feature-level state; the plan files reflect step-level state.

---

## 🟢 Now (active work, in priority order)

R1, R5, R2 are being implemented in **parallel worktrees**. See [docs/plans/PARALLEL.md](docs/plans/PARALLEL.md) for the workflow.

| ID | Feature | Status | Plan | Branch | Worktree |
|---|---|---|---|---|---|
| **R1** | Replayable event log (telemetry + cassette replay) | Planned | [R1-replay-event-log.md](docs/plans/R1-replay-event-log.md) | `feat/R1-replay` | `../coscientist-R1` |
| **R5** | Cost ledger + tier routing + caching | Planned | [R5-cost-tier-routing.md](docs/plans/R5-cost-tier-routing.md) | `feat/R5-cost` | `../coscientist-R5` |
| **R2** | Reflection → cross-session lessons buffer | Planned | [R2-reflection-lessons.md](docs/plans/R2-reflection-lessons.md) | `feat/R2-reflection` | `../coscientist-R2` |

## 🟡 Next (queued, plans not yet drafted)

| ID | Feature | Notes |
|---|---|---|
| R6 | Verify parallel subagent dispatch + reference-passing discipline | ~½ day; prompt + test |
| R3 | Pinned plan + scratchpad as first-class context anchors | 2 days |
| R4 | Tool-grounded critique pass before user-facing emission | 2 days; opt-in flag |
| R7 | Skill library substrate (Voyager-style executable cache) | 2 days; evolution agent populates |
| **B1** | Bug: container image missing pytest, so strict-path smoke gate always auto-rejects | Found during UI1 verification 2026-05-07. Dockerfile installs `.[science]` not `.[dev]`. Fix: install pytest in image (or include `.[dev]`) so `evolution/tools/propose_merge.py:_run_smoke` can succeed. |

## 🖥️ UI

| ID | Feature | Status | Plan | Branch |
|---|---|---|---|---|
| **UI1** | Evolution pane in Streamlit chat | ✅ Done (backend-verified) | [UI1-evolution-pane.md](docs/plans/UI1-evolution-pane.md) | `feat/evolution_ui` |

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
