# Parallel implementation across worktrees

R1, R5, R2 are sequenced in the roadmap by *dependency strength*, not by *required serial execution*. They can be built concurrently in three git worktrees on three branches. This doc explains how.

## Worktrees

Created as siblings of the main checkout so they don't shadow each other and don't conflict with the evolution agent's `worktrees/` directory (reserved for self-modification sandboxes).

| Branch | Worktree path | Plan |
|---|---|---|
| `feat/R1-replay` | `../coscientist-R1/` | [R1-replay-event-log.md](R1-replay-event-log.md) |
| `feat/R5-cost` | `../coscientist-R5/` | [R5-cost-tier-routing.md](R5-cost-tier-routing.md) |
| `feat/R2-reflection` | `../coscientist-R2/` | [R2-reflection-lessons.md](R2-reflection-lessons.md) |
| `feat/R19-guided-evolution` | `../coscientist-R19/` | [R19-guided-evolution-search.md](R19-guided-evolution-search.md) — off `feat/O1-onboarding`; the ui3 `node_modules` is a symlink to the main checkout's |

The main branch (`main` at `coscientist/`) stays clean for integration.

## Day-1 setup (already done by the script)

```bash
# from coscientist/
git checkout main && git pull
git branch feat/R1-replay
git branch feat/R5-cost
git branch feat/R2-reflection
git worktree add ../coscientist-R1 feat/R1-replay
git worktree add ../coscientist-R5 feat/R5-cost
git worktree add ../coscientist-R2 feat/R2-reflection
git push -u origin feat/R1-replay feat/R5-cost feat/R2-reflection
```

## Per-agent quickstart

Each agent works in its own worktree directory. From within e.g. `../coscientist-R1/`:

```bash
# .env is gitignored; copy or symlink from the main checkout
ln -sf ../coscientist/.env .env
# (or cp, if the agent might want a different budget)

# work the plan: open docs/plans/R1-replay-event-log.md, find first `- [ ]`, do it
# commit each step as you finish — don't batch
git add -A && git commit -m "R1 step N: <what>"
git push    # branch already tracks origin
```

Each worktree is a full checkout of the repo at its branch's HEAD. `state/`, `worktrees/`, `researcher_data/`, and `.env` are gitignored, so each worktree has its own runtime state — sessions don't cross over.

## Cross-feature dependencies (and how to handle them)

The plans share two crosscut points:

### 1. `research/runtime.py` will get changes from all three branches

R1 adds `env=telemetry_env(session_id)` to `ClaudeAgentOptions`.
R5 adds `hooks={"Stop": [ledger.stop_hook()]}`.
R2 wraps the run in `try/finally` to call `Reflector(...).reflect()` at the end.

**Convention to minimize conflict:**

- R1 lands a thin helper `scaffold/telemetry.py:telemetry_env` and calls it from `runtime.py`. Single-line edit.
- R5 lands `scaffold/cost.py:CostLedger` with self-contained `observe()` + `stop_hook()`. The `runtime.py` edit is two lines: instantiate ledger, register hook.
- R2 lands `scaffold/reflector.py` and calls it from a `try/finally` around the existing client loop. Wrap-only edit.

When two of these merge first, the third rebases against `main` — three small edits on the same file resolve cleanly. If any agent finds itself doing >5 lines of `runtime.py` rework, stop and split the work into a helper module.

### 2. `evolution/runtime.py` is also touched by R1 and R5 (not R2)

Same pattern. R1 sets the env; R5 instantiates a ledger + hook. Both are 1–2-line edits.

## What each agent should stub vs. wait

R5 and R2 have *soft* deps on R1's `telemetry.py` (the `ENABLE_PROMPT_CACHING_1H=1` flag) and on R5's `settings.MODEL_DEFAULT` (R2 wants to call reflection at default tier). Recommended approach:

- **R5**: do not stub `telemetry.py`. Just set `ENABLE_PROMPT_CACHING_1H=1` directly in your `runtime.py` env edit. Remove that line in the merge PR if R1 already shipped it.
- **R2**: if `settings.MODEL_DEFAULT` doesn't exist on your branch yet, add a tiny shim in `scaffold/settings.py` (one line: `MODEL_DEFAULT = os.environ.get("COSCIENTIST_MODEL_DEFAULT", "claude-sonnet-4-6")`). If R5 lands first and the shim is already there, no change needed.
- **R2**: your "trim trace before reflection" logic is independent of the cassette format R1 produces. Don't block on R1 to define it.

## Merge order (suggested)

Land in this order to minimize conflict resolution:

1. **R1 first** — mostly new files; `runtime.py` change is one line.
2. **R5 second** — adds hook + ledger. If R1 already landed, R5's `telemetry`-related env line is a no-op.
3. **R2 third** — largest surface area; benefits from R1's cassettes existing for replay-based testing.

Each merges via PR on GitHub. Reviewer (= you, the human) runs the smoke tests in the merged worktree before merging.

## Wrapping up a worktree

When a feature's `Status: Done`:

```bash
cd /Users/yuhe/Desktop/OpenPhil/coscientist
git fetch origin
git merge --ff-only origin/feat/R1-replay        # or open a PR and merge in GitHub UI
git worktree remove ../coscientist-R1
git branch -d feat/R1-replay                     # delete local
git push origin --delete feat/R1-replay          # delete remote
```

Update [`ROADMAP.md`](../../ROADMAP.md): move the R1 row from Now → Done with the merge SHA. Tick off all the boxes in the plan file (which the implementing agent should already have done).

## Why not feature flags + single branch?

For 3 features that touch ~3 files each, the merge cost is lower than the flag cost. If we ever do >5 features in flight, revisit.
