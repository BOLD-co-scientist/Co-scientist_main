# coscientist Roadmap

Single source of truth for what's being built. Update by editing this file. Per-feature step lists live under [docs/plans/](docs/plans/).

**Convention.** Each feature has a one-liner here and a detailed plan file. To make progress, tick boxes in the plan file. The roadmap reflects feature-level state; the plan files reflect step-level state.

---

## 🟢 Now (active work, in priority order)

| ID | Feature | Status | Plan |
|---|---|---|---|
| **R1** | Replayable event log (telemetry + cassette replay) | Planned | [R1-replay-event-log.md](docs/plans/R1-replay-event-log.md) |
| **R5** | Cost ledger + tier routing + caching | Planned | [R5-cost-tier-routing.md](docs/plans/R5-cost-tier-routing.md) |
| **R2** | Reflection → cross-session lessons buffer | Planned | [R2-reflection-lessons.md](docs/plans/R2-reflection-lessons.md) |

## 🟡 Next (queued, plans not yet drafted)

| ID | Feature | Notes |
|---|---|---|
| R6 | Verify parallel subagent dispatch + reference-passing discipline | ~½ day; prompt + test |
| R3 | Pinned plan + scratchpad as first-class context anchors | 2 days |
| R4 | Tool-grounded critique pass before user-facing emission | 2 days; opt-in flag |
| R7 | Skill library substrate (Voyager-style executable cache) | 2 days; evolution agent populates |

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
