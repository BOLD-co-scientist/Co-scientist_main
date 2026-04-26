# coscientist

OpenPhil AI coscientist v3. Two SDK agents — a hierarchical **research agent** and an **evolution agent** — running in a sandboxed Docker container. The evolution agent reshapes the system itself (including its own code) under human approval.

## Quickstart

```
git clone …
cd coscientist
cp .env.example .env       # add ANTHROPIC_API_KEY
docker compose up
```

The API is then on `http://localhost:8765`. See [api/server.py](api/server.py) for endpoints.

## Layout

- `scaffold/` — minimal substrate (bus, memory, hitl, sandbox, spawn, eventlog). The evolution agent reshapes everything else.
- `research/` — supervisor entry, prompt.
- `evolution/` — evolution agent entry, tools, prompt.
- `roles/` — YAML role configs (supervisor + subagents). Editable by the evolution agent.
- `tools/` — MCP tool packages.
- `state/` — runtime, persistent across container restarts.
- `worktrees/` — ephemeral git worktrees for evolution sessions.
- `researcher_data/` — drop your data here; mounted read-only into the container.

See [the v0 design doc](../../.claude/plans/this-openphil-repo-is-whimsical-hopper.md) for the full architecture.
