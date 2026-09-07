# coscientist — project context for Claude

## ⛔ Server operating rules (BOLD/FLAIR) — MUST FOLLOW (security-critical)

coscientist runs on the **BOLD/FLAIR** shared cluster. These rules are mandatory
for every agent and developer, in every branch, now and going forward. Breaking
them is a **cybersecurity/operational risk** and can get our jobs and account
reaped. Full detail: [docs/BOLD-server-guide.md](docs/BOLD-server-guide.md).

- **NEVER run Docker as root (Rule 1).** Every image must end on a non-root
  `USER myuser` built from `ARG UID/GID`; build with
  `--build-arg UID=$(id -u) --build-arg GID=$(id -g)`; verify with
  `docker exec <c> id` (must **not** be `uid=0`). Do **not** remove `USER myuser`
  or add a later `USER root` — the smoke gate
  (`tests/test_smoke_v0.py::test_dockerfile_runs_nonroot`) enforces this.
- **Never store data in a container — mount with `-v`.** Stopped containers /
  untagged images may be pruned at any time.
- **Attribution:** name images `${USER}_…` and containers `--name ${USER}_…`.
- **Never share a GPU (Rule 2):** pass explicit `--gpus '"device=N"'`.
- **Don't kill others' jobs (Rule 3):** out of space → ask in Slack `#compute`.
- **No misuse (Rule 4).** Use the right server tier (Rule 0; see the guide).

When editing `Dockerfile`, `docker-compose.yml`, or anything that launches
containers/jobs, re-read the guide and preserve all of the above.

## What this is

**coscientist** is the OpenPhil AI-coscientist v3. Two agents on the Python `claude-agent-sdk`:

- **Research agent** — hierarchical multi-agent system for science research. A *supervisor* agent talks to the human, plans, and dispatches subagents via the SDK Task tool.
- **Evolution agent** — modifies the system itself (including its own code) on human instruction. Every change goes through a sandboxed git worktree + human-approved merge.

Two prior systems gave us only the *concepts* — no code reuse:
- **CHICO** taught file-based IPC, atomic state writes, role templates.
- **HyperAgents** taught bash + edit primitives as the right modification surface, and worktree isolation for self-modification.

## Hard objectives (priority order)

1. **Human-in-the-loop is paramount.** The human is the principal. Every sensitive action goes through `scaffold/hitl.py`.
2. **Self-evolving.** The evolution agent reshapes the system; the human-developer should *not* be growing role hierarchies, pipelines, or agent types by hand.
3. **Open-ended and task-agnostic.** Adapts to any science research task; no domain hardcoding.

## The minimal-scaffold philosophy (READ BEFORE EDITING)

The scaffold (`scaffold/`, the two runtimes, the v0 roles, the v0 tools) is a **substrate**, not a feature set. The evolution agent is supposed to grow phases, critic roles, methodology, additional tools, memory upgrades — *not* the human developer.

**When you're tempted to add a feature, ask: should the evolution agent be doing this instead?** Usually yes. Resist the urge to:
- Add role types beyond the three v0 roles (supervisor, generalist_researcher, data_analyst).
- Add pipeline phases or workflow logic.
- Pre-design critic/synthesis layers.
- Add domain-specific tools (e.g., literature search, plotting helpers).
- Build out cost tracking, embedding memory, multi-provider critics.

The v0 plan explicitly leaves these to the evolution agent. See `../../.claude/plans/this-openphil-repo-is-whimsical-hopper.md` for the full design rationale.

Legitimate developer-side work: bug fixes in scaffold, sandbox/HITL tightening, SDK upgrades, test additions, and the future UI.

## Architecture in one screen

```
human ──HTTP──▶ api/server.py ──spawns──▶ research/runtime.py  (supervisor + Task subagents)
                       │                          │
                       ├─spawns──▶ evolution/runtime.py  (fresh subprocess per command)
                       │                          │
                       │                          ├──worktree──▶ edit/bash_sandbox/run_tests
                       │                          └──propose_merge──▶ HITL ──▶ git merge --ff-only
                       │
                       └──tails──▶ state/sessions/<sid>/events.jsonl  (SSE)
```

- Supervisor's MCP tools: `bus`, `memory`, `hitl`, `fs_read`, `fs_write_workspace`, `Task` (built-in).
- Subagents inherit those servers; each role's `tools` list in YAML gates what they can call.
- Evolution agent's tools: `edit`, `bash_ro`, `bash_sandbox`, `run_tests`, `propose_merge`, `memory`.
- **Onboarding phase (O1).** Sessions normally start from a fixed-format
  *problem brief* (`api/onboarding.py`, routes under `/onboarding/briefs/*`):
  define the problem → attach library data → run the hypothesis search →
  launch. Launch freezes the brief to `state/sessions/<sid>/brief.json`, emits
  `session.brief`, and passes the rendered brief as the supervisor's first-turn
  message. The brief is composed **API-side**, not in `research/runtime.py`,
  because per-user harness roots are frozen forks (see below). Plan:
  [docs/plans/O1-onboarding.md](docs/plans/O1-onboarding.md).
- **Onboarding system (O2).** The brief is the five-question statement
  (definition · why it matters · prior work + open gap · objective evaluation
  · exact data/task/results/permissions). Questions 1–3 register the problem on
  the **org-wide project tree** (`api/projects.py`, `state/projects/`, routes
  under `/projects/*`), a platform registry outside every tenant root with
  BM25 adjacency + dataset/method edges. A **background advisor**
  (`api/advisor.py`, prompt `api/prompts/advisor.md`, Fable → Opus via
  `api/llm.py`) reads the tree and recommends a colleague's evolved harness
  version and tools; **imports** (`api/imports.py`) fetch the donor's
  `ver/<id>` tag into the importer's repo, run the smoke + compat gate in a
  worktree, and open a HITL request in `evo-import-<id>` — the human approves
  through the normal HITL route; adopt = R17 switch to the fetched commit,
  merge/tool = fast-forward child version. Cross-tenant reads happen only in
  `api/` and surface bounded summaries; a donor repo is never written. Plan:
  [docs/plans/O2-project-tree-advisor.md](docs/plans/O2-project-tree-advisor.md).

**Per-user harness roots are frozen forks.** `api/tenancy.py:ensure_user_root`
copies `scaffold/`, `research/`, `evolution/`, `tools/`, `roles/`, `prompts/`
into `state/users/<uid>/root/` **once**, and the runtime subprocesses run from
that copy (`cwd=ctx.root`, `PYTHONPATH=root`). A later change on `main` to any
of those dirs does **not** reach existing users; only the shared `api/` is
always current. Put tenant-independent behaviour in `api/` (or add an explicit
upgrade path) — don't assume a runtime/prompt edit is live for everyone.

## Sandboxing layers — invariants future you must preserve

These are the safety guarantees. Don't break them when modifying scaffold:

1. **Docker (host vs container).** The whole system runs in Docker; host filesystem is unreachable except `state/` (rw), `researcher_data/` (ro), and the bind-mounted source dirs. See `docker-compose.yml`.
2. **Worktree path-guard for evolution edits.** `evolution/tools/edit.py` and `bash_sandbox.py` validate every path is inside the active worktree. **Enforced at the tool layer, not in the prompt.** If you change these tools, keep the guard.
3. **Per-tool ephemeral execution.** `tools/py_exec/server.py` runs in a subprocess with `setrlimit` and a minimum env. Don't loosen this.
4. **HITL is the final gate.** `scaffold/hitl.py` blocks via filesystem polling. The API answers via `state/sessions/<sid>/hitl/answered/`. Don't bypass.
5. **Long-job spooler trust boundary (R12).** For long/GPU jobs, the non-root container only writes a job *request* to the spool (`tools/longjob/`); the host-side `deploy/flair_spooler.py` (running as the user, outside Docker) is the only thing that runs `docker`, and only argv it builds from validated fields. **Never** give the container the Docker socket (root-equivalent — would undo the non-root posture). The guard lives in the spooler, not the prompt. See `docs/plans/R12-longjob-runner.md`.

**Stricter HITL for `scaffold/`, `evolution/`, `pyproject.toml`, `Dockerfile` edits**: smoke tests must pass in the worktree before the human is asked. See `evolution/tools/propose_merge.py`.

**Self-modification trick**: the evolution-agent process is short-lived per command (subprocess). Bind-mounted source means an approved merge under `evolution/` is visible to the *next* invocation without a rebuild. Don't switch to long-lived evolution processes.

## Repo map

```
scaffold/              # the substrate — tread carefully
  settings.py          # env-driven paths and model IDs
  bus.py               # file-IPC, atomic writes
  memory.py            # 3-layer JSONL memory + BM25/token-overlap recall
  hitl.py              # pending/answered router, async ask()
  config_loader.py     # YAML role loader + {{PLACEHOLDER}} rendering
  sandbox.py           # git worktree wrapper + path-guard helper
  tools_registry.py    # role.tools strings -> MCP servers + allowed_tools
  spawn.py             # builds AgentDefinition dict for ClaudeAgentOptions(agents=...)
  system_tools.py      # bus/memory/hitl MCP servers (session-scoped)
  eventlog.py          # append-only JSONL audit trail; tail() drives SSE

research/runtime.py    # supervisor entry; reads supervisor.yaml + subagents/*.yaml
research/supervisor_prompt.md

evolution/runtime.py   # evolution entry; creates worktree, runs SDK agent, exits
evolution/evolution_prompt.md
evolution/tools/       # edit, bash_ro, bash_sandbox, run_tests, propose_merge

api/server.py          # FastAPI on :8765
api/schemas.py

roles/                 # YAML role configs — editable by the evolution agent
  supervisor.yaml
  subagents/{generalist_researcher,data_analyst}.yaml
prompts/               # subagent prompts referenced by roles/

tools/                 # MCP tool packages (evolvable by the evolution agent)
  fs_read/, fs_write_workspace/, py_exec/

state/                 # gitignored runtime state, persistent across restarts
worktrees/             # ephemeral, one per evolution session
researcher_data/       # bind-mounted ro into container — researcher drops files here
tests/test_smoke_v0.py # the gating test for sensitive-path evolution merges
```

- **Guided evolution search (R19).** Self-improvement is no longer only
  human-typed commands: `api/planner.py` proposes goal-conditioned evolutions
  (from the brief's sequential goals in `api/goals.py` + the version tree + the
  decision "eval folder" in `state/evolution/`), an OpenAI judge
  (`api/judge.py`) rates every proposal and every merge request, and a
  per-tenant mode decides who answers: `manual` (human decides, judge
  recommends) or `automatic` (judge decides and the human is told why). Both
  gates remain — nothing merges unreviewed — but note the boundary: in
  automatic mode the *platform* answers the merge gate on the researcher's
  behalf, except for strict-path changes (`scaffold/`, `evolution/`,
  `pyproject.toml`, `Dockerfile`), which always go to a human. The tenant's own
  runtime still cannot answer its merge gate, and the evolution agent is
  unchanged. Plan:
  [docs/plans/R19-guided-evolution-search.md](docs/plans/R19-guided-evolution-search.md).

## Current work — start here

**[ROADMAP.md](ROADMAP.md) is the source of truth for what's being built.** Per-feature step lists live under [docs/plans/](docs/plans/) — pick the first unchecked `- [ ]` step in the active plan and do it.

Convention details: [docs/plans/README.md](docs/plans/README.md).

## v0 status

**Done and verified on host (8/8 tests pass):**
- Scaffold modules import; bus roundtrip works; memory recall works; sandbox path-guard works; role loader works.
- API surface registered (14 routes).
- `git tag v0` exists; `coscientist reset --to-v0` is wired.

**Not yet verified (needs `ANTHROPIC_API_KEY` and `docker compose up`):**
- Live supervisor + subagent dispatch via Task tool.
- Evolution agent end-to-end: command → worktree → edit → propose_merge → HITL → merge.
- Self-modification cycle (the `--dry-run` flag test in the plan).
- Sandbox-escape negative test.

The architectural plan is in `../../.claude/plans/this-openphil-repo-is-whimsical-hopper.md`. Operational feature work is in [docs/plans/](docs/plans/).

## Verification policy

After every feature or major checkpoint, run a backend verification before reporting the work as done. Drive the API directly with `curl` (and tail `state/sessions/<sid>/events.jsonl`) to confirm the data flow end-to-end — don't stop at unit tests or a syntax check.

The only reason to skip is cost: if the verification would burn more than ~$5 of API spend (long-running multi-turn agent runs, large research tasks), pause and ask the human first. A trivial `evolution.requested` round-trip or a one-shot research session is fine to run unprompted.

UI-only changes still warrant a backend pass to confirm the data the UI consumes is correctly shaped, even though the visual render still needs human eyes.

**Before pushing to remote — do the maximum amount of validation that's feasible.** Treat `git push` as the gate, not `git commit`. Before pushing any branch to origin, run all of: (1) `pytest tests/test_smoke_v0.py -q` on the host, (2) the curl-driven backend pass for the touched feature surface, (3) `python3 -c "import ast; ast.parse(...)"` on every changed `.py`, (4) any feature-specific smoke listed under "Verification" in the relevant `docs/plans/<ID>-*.md`. If a check would cost >$5, escalate; otherwise just run it. A single failing check blocks the push — fix the underlying issue, don't push with `--no-verify` or skip the check.

## Common commands

```bash
# host, with deps installed:
PYTHONPATH=. python3 -m pytest tests/test_smoke_v0.py -q

# normal use (after editing .env with ANTHROPIC_API_KEY):
docker compose up -d coscientist-api
curl -X POST http://localhost:8765/research/sessions \
  -H 'Content-Type: application/json' \
  -d '{"task":"Summarize the LTEM growth-curve CSV and propose one hypothesis."}'
# Final deliverables default to LaTeX (.tex + compiled .pdf in results/); set
# COSCIENTIST_OUTPUT_FORMAT=markdown in .env to revert to .md. The container
# image ships texlive + latexmk; the supervisor compiles via the latex_compile
# tool. See docs/plans/R13-latex-output.md.

# tail events for a session
curl http://localhost:8765/events/<sid>

# evolution
curl -X POST http://localhost:8765/evolution/commands \
  -H 'Content-Type: application/json' \
  -d '{"command":"Add a critic subagent role that reviews each generalist_researcher report."}'

# answer a pending HITL request
curl http://localhost:8765/hitl/<sid>/pending
curl -X POST http://localhost:8765/hitl/<sid>/<request_id>/answer \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve"}'

# escape hatch — restore code to the v0 tag, preserve state/
PYTHONPATH=. python3 bin/coscientist reset --to-v0
```

## Things that are explicitly out of scope for v0

Don't build these without checking with the human first:
- The unified UI (chat + dashboard with research/evolution switcher). Backend contract is stable; UI plugs into `/api` + `state/`.
- Cost tracking — eventlog has the hooks, write `cost.json` in a follow-up.
- Embeddings-based memory — BM25/token-overlap is the v0 floor; the evolution agent can upgrade.
- Multi-provider critics (Gemini/etc.) — evolution agent territory.
- Multi-user / auth / remote deployment / encrypted state at rest.

## Important context for working in this repo

- This is a **product** for actual researchers, not a research demo. Errors should be informative; failure modes should preserve work; the host machine should never be at risk.
- Prior repos `../CHICO`, `../HyperAgents`, `../Evolution-CoScientist`, `../AgentLaboratory`, `../autoresearch`, `../t3code` are reference only — don't import from them.
- The full design doc is `../../.claude/plans/this-openphil-repo-is-whimsical-hopper.md`. Read it before any architectural change.
