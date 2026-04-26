# coscientist — project context for Claude

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

## Sandboxing layers — invariants future you must preserve

These are the safety guarantees. Don't break them when modifying scaffold:

1. **Docker (host vs container).** The whole system runs in Docker; host filesystem is unreachable except `state/` (rw), `researcher_data/` (ro), and the bind-mounted source dirs. See `docker-compose.yml`.
2. **Worktree path-guard for evolution edits.** `evolution/tools/edit.py` and `bash_sandbox.py` validate every path is inside the active worktree. **Enforced at the tool layer, not in the prompt.** If you change these tools, keep the guard.
3. **Per-tool ephemeral execution.** `tools/py_exec/server.py` runs in a subprocess with `setrlimit` and a minimum env. Don't loosen this.
4. **HITL is the final gate.** `scaffold/hitl.py` blocks via filesystem polling. The API answers via `state/sessions/<sid>/hitl/answered/`. Don't bypass.

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

The verification plan is in `../../.claude/plans/this-openphil-repo-is-whimsical-hopper.md`.

## Common commands

```bash
# host, with deps installed:
PYTHONPATH=. python3 -m pytest tests/test_smoke_v0.py -q

# normal use (after editing .env with ANTHROPIC_API_KEY):
docker compose up -d coscientist-api
curl -X POST http://localhost:8765/research/sessions \
  -H 'Content-Type: application/json' \
  -d '{"task":"Summarize the LTEM growth-curve CSV and propose one hypothesis."}'

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
