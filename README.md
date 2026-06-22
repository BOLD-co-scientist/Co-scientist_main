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

Create a user before logging into the UI:

```
docker compose exec coscientist-api coscientist users create alice
```

The command prints a one-time API key. Paste it into the Streamlit login screen.
Logging out only clears the browser session; the user's sessions and private
agent harness remain on disk until explicitly deleted.

The `ANTHROPIC_API_KEY` in `.env` is the server default for agent runs. After
logging in, a user can open **Agent API key** in the UI sidebar, paste their own
Anthropic key, and save it. New sessions for that user will use the saved key;
clearing it returns the user to the server default.

## User and auth database management

User records and API-key hashes live in `state/auth/users.db`. Per-user agent
harnesses, sessions, libraries, memories, worktrees, and evolution history live
under `state/users/<user_id>/root/`.

User-specific agent API-key overrides are stored under the user's private root:

```
state/users/<user_id>/root/state/secrets/anthropic_api_key
```

Create a user and print a one-time API key:

```
docker compose exec coscientist-api coscientist users create alice
```

List users:

```
docker compose exec coscientist-api coscientist users list
```

Disable or re-enable a user's API key while preserving all state:

```
docker compose exec coscientist-api coscientist users disable <user_id>
docker compose exec coscientist-api coscientist users enable <user_id>
```

Delete a user, their auth DB entry, and all per-user state:

```
docker compose exec coscientist-api coscientist users delete <user_id> --yes
```

Deletion is destructive. Use `disable` when you only want to revoke access while
keeping the user's sessions and evolved harness available for later inspection.

## Layout

- `scaffold/` — minimal substrate (bus, memory, hitl, sandbox, spawn, eventlog). The evolution agent reshapes everything else.
- `research/` — supervisor entry, prompt.
- `evolution/` — evolution agent entry, tools, prompt.
- `roles/` — YAML role configs (supervisor + subagents). Editable by the evolution agent.
- `tools/` — MCP tool packages.
- `state/` — runtime, persistent across container restarts.
- `state/users/<user_id>/root/` — per-user mutable agent harness roots. Evolution
  merges land here, not in the shared API/UI.
- `worktrees/` — ephemeral git worktrees for evolution sessions.
- `researcher_data/` — drop your data here; mounted read-only into the container.

See [the v0 design doc](../../.claude/plans/this-openphil-repo-is-whimsical-hopper.md) for the full architecture.
