# R1 — Replayable event log

**Status:** Planned
**Owner:** unassigned
**Started:** —
**Done when:** Every research/evolution session writes raw Anthropic API request/response cassettes to `state/sessions/<sid>/cassettes/`, AND a session can be re-run against those cassettes (no live API calls) and produce identical assistant turns.

## Why

Substrate for everything downstream. R2 (reflection) needs traces in the right shape; R5 (cost) rides on the same telemetry; debugging long sessions without replay is a nightmare. The headline finding from research: `claude-agent-sdk` already supports `OTEL_LOG_RAW_API_BODIES=file:<dir>` which dumps untruncated request/response JSON — so we don't need an instrumentation library, only a small replay server.

Sources: [Claude Agent SDK observability](https://code.claude.com/docs/en/agent-sdk/observability), [Claude Code Monitoring](https://code.claude.com/docs/en/monitoring-usage), [Langfuse Claude Agent SDK integration](https://langfuse.com/integrations/frameworks/claude-agent-sdk).

## Steps

### Pre-work — verify environment behavior (~30 min total)

- [ ] 1. **V1 — Confirm `ANTHROPIC_BASE_URL` is honored by the bundled `claude` CLI.** Start a netcat listener on 127.0.0.1:8787 (`nc -l 8787`); set `ANTHROPIC_BASE_URL=http://127.0.0.1:8787` in `ClaudeAgentOptions(env=...)`; fire one tiny `query()`. The listener should see an HTTP request. If yes → proceed. If no → switch to the fallback in step 8 (`pre_tool_use` hook interception).
- [ ] 2. **V2 — Confirm `OTEL_LOG_RAW_API_BODIES=file:<dir>` writes the data we expect.** Set the env on `ClaudeAgentOptions(env=...)`, run one supervisor turn, verify `<dir>/*.request.json` and `<dir>/*.response.json` appear. Inspect schema. Note any quirks under **Notes**.

### Telemetry wiring

- [ ] 3. Create [`scaffold/telemetry.py`](../../scaffold/telemetry.py) exposing `telemetry_env(session_id) -> dict[str, str]` that returns:
  - `CLAUDE_CODE_ENABLE_TELEMETRY=1`
  - `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`
  - `OTEL_LOG_RAW_API_BODIES=file:<state/sessions/<sid>/cassettes>`
  - `ENABLE_PROMPT_CACHING_1H=1`
  - If `LANGFUSE_HOST` env is set: also OTLP exporter vars pointing at it.
- [ ] 4. Wire `telemetry_env` into [`research/runtime.py`](../../research/runtime.py): `ClaudeAgentOptions(..., env=telemetry_env(session_id))`. Make sure `state/sessions/<sid>/cassettes/` is created in `settings.ensure_session_dirs()`.
- [ ] 5. Wire same into [`evolution/runtime.py`](../../evolution/runtime.py).
- [ ] 6. Add [`tests/test_telemetry.py`](../../tests/test_telemetry.py): assert `telemetry_env` returns the expected keys; assert `cassettes/` is created on session start.

### Replay server

- [ ] 7. Create [`scaffold/replay.py`](../../scaffold/replay.py) — a FastAPI app with a single route `POST /v1/messages`. On each request:
  1. Compute `key = sha256(model + canonical_json(messages) + canonical_json(system) + canonical_json(tools))[:16]`.
  2. Look up `cassettes/<key>.response.json`.
  3. Return verbatim. If not found → 404 with the missing key for debugging.
  Helper: `index_cassettes(session_dir) -> dict[key, response_path]`.
- [ ] 8. **If V1 failed**, instead of an HTTP server, implement a `pre_tool_use` hook that intercepts LLM calls inside `claude-agent-sdk` and returns cached responses. Document the chosen path under **Notes**.
- [ ] 9. Create [`bin/coscientist-replay`](../../bin/coscientist-replay): boots the replay server on a free port, sets `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>` in env, then re-invokes `coscientist research --session <sid> --task <original_task>`.
- [ ] 10. Add [`tests/test_replay.py`](../../tests/test_replay.py): record a tiny session; replay; assert no new cassette files appear; assert assistant text is identical.

### Documentation

- [ ] 11. Add a "Replay" section to [`CLAUDE.md`](../../CLAUDE.md) with the one-line command for replaying a session.
- [ ] 12. Mark **Status: Done**, move the row in [`ROADMAP.md`](../../ROADMAP.md), commit.

## Files touched

- New: `scaffold/telemetry.py`, `scaffold/replay.py`, `bin/coscientist-replay`, `tests/test_telemetry.py`, `tests/test_replay.py`
- Modified: `scaffold/settings.py` (add `cassettes/` to `ensure_session_dirs`), `research/runtime.py`, `evolution/runtime.py`, `CLAUDE.md`

## Verification

After step 12, this sequence works end-to-end:

```bash
# 1. Run a tiny research session (records cassettes)
curl -X POST http://localhost:8765/research/sessions \
  -H 'Content-Type: application/json' \
  -d '{"task":"Print the names of files under researcher_data/"}'
# capture <sid> from response

# 2. List the cassettes that were written
ls coscientist/state/sessions/<sid>/cassettes/

# 3. Replay — no live API calls, no charges
coscientist-replay --session <sid>
```

The replayed run's `events.jsonl` should mirror the original's in tool calls and assistant text.

## Risks / open questions

- **`ANTHROPIC_BASE_URL` honoring** — open until V1 (step 1).
- **Tool-side determinism** — replayed model responses don't make tool side effects deterministic. A `py_exec.run` that wrote to `state/sessions/<sid>/scratch/` once still re-executes on replay. v0.5 scope: replay reproduces *next assistant message* fidelity, not full session state. Document as a known limitation.
- **Cassette PII** — extended-thinking content is redacted by Anthropic, but cassettes still contain the full conversation. `state/` is gitignored already; ensure no cassette path leaks into eventlog summaries.
- **Cassette key collisions** under prompt caching: cache reads vs writes have different request payloads. Verify the hash includes whatever the SDK varies between cache states; if not, key off the request body verbatim.
- **`sqlite-vec` and other Stage-3 deps don't matter here** — keep this stage dep-free beyond what's already in `pyproject.toml`.

## Notes

(Append-only running notes — discoveries, decisions, dead ends, links.)

- 2026-04-26 — Plan drafted from research/synthesis. Key reference: [Laminar's writeup on instrumenting claude-agent-sdk](https://laminar.sh/blog/2025-12-03-claude-agent-sdk-instrumentation) explains why subprocess-based instrumentation is tricky and why the SDK's own OTel is the right hook.
