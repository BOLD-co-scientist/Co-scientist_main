# R10 — Resumable multi-turn conversations

**Status:** Done
**Owner:** yhg01
**Started:** 2026-06-23
**Completed:** 2026-06-23
**Done when:** A human can send a message to an existing research session and get a reply that remembers the entire prior conversation — repeatedly, across subprocess exits and even across a `docker compose down/up` — the same back-and-forth flow as Claude Code.

## Why

Today every interaction is effectively single-turn. `POST /research/sessions` spawns a per-task subprocess that opens one `ClaudeSDKClient`, runs to a natural stop, logs `session.end`, and **exits** (`research/runtime.py:439`) — the conversation lives only in that process's memory, so it dies with the process. The only human-input channels are HITL pause-points (checkpoint notes, interrupt feedback, evolution prompt); the one chat-shaped endpoint, `POST /research/sessions/{sid}/messages`, writes to a bus inbox the runtime **never drains** (dead code, despite `runtime.py:3` and `supervisor_prompt.md:13` claiming otherwise). For real scientific discovery the human needs to discuss back and forth with full context, like Claude Code.

The fix is native: `claude-agent-sdk` 0.1.68 exposes `resume`/`session_id`/`fork_session`/`session_store` on `ClaudeAgentOptions`, and every run surfaces a CLI session UUID on `ResultMessage.session_id`. Passing `resume=<uuid>` into a fresh client replays the full prior conversation — no custom transcript replay needed.

**Design decisions (confirmed with human 2026-06-23):**
1. **Turn-based resume**, not a long-lived chat process. Each human message spawns a fresh subprocess that resumes context, does its agentic work, and exits. Mirrors Claude Code; smallest change; crash-resilient; preserves the short-lived-subprocess self-modification invariant from `CLAUDE.md`.
2. **Idle after each turn** — no automatic evolution-prompt tail. The session goes idle (subprocess exits) and waits for the next message. Evolution becomes a separately-triggered action.
3. **Durability via `CLAUDE_CONFIG_DIR` under `state/`** — point the SDK transcript dir at the persistent mount so resume survives `docker compose down/up`. (`SessionStore` adapter deferred.)

## Architecture

Decouple **the conversation** (durable, keyed by our `sid`) from **a runtime process** (ephemeral, one turn then exits):

```
POST /research/sessions {task}            → new sid, spawn fresh runtime, run turn 1,
                                            persist SDK uuid, exit → idle
POST /research/sessions/{sid}/messages    → spawn runtime with resume=<uuid>, query(text),
       {text}                               run turn N with full prior context, exit → idle
GET  /events/{sid}                         → unchanged; events.jsonl already persists full history
```

The existing checkpoint/interrupt/HITL machinery runs unchanged *inside* each turn.

## Steps

### Durability + session-id plumbing (scaffold)

- [x] 1. **Persist transcripts on the state mount.** In `scaffold/settings.py` add `CLAUDE_CONFIG_DIR = STATE / ".claude"` and export it into the process env (set `os.environ["CLAUDE_CONFIG_DIR"]` at import, and add it to `api/server.py:_runtime_env()` so subprocesses inherit it). Create the dir in `ensure_runtime_dirs()`. Verify the SDK writes `projects/.../<uuid>.jsonl` there.
- [x] 2. **SDK-session file helper.** In `settings.py` add `sdk_session_file(sid) -> Path` = `session_dir(sid)/"sdk_session.json"`. Schema: `{"sdk_session_id": <uuid>, "updated": <ts>, "turns": <int>}`. Add a small `read_sdk_session(sid)` / `write_sdk_session(sid, uuid, turns)` pair (in `settings.py` or a tiny new helper — keep it scaffold-thin).

### Runtime (research)

- [x] 3. **Capture the SDK session UUID.** In `research/runtime.py:_process_message_stream`, when a `ResultMessage` arrives, read `message.session_id` and stash it on `event_state["sdk_session_id"]`. Also handle the init `SystemMessage` (subtype `init`) as a fallback source.
- [x] 4. **Resume-aware `run_session`.** Change signature to `run_session(session_id, message, resume_uuid=None)`. When `resume_uuid` is set, pass `resume=resume_uuid` in `_build_options`'s `ClaudeAgentOptions`, and make the first `client.query(...)` send `message` (the new human turn) instead of re-sending the original task. The library-listing prepend stays only on the very first turn (no `resume_uuid`).
- [x] 5. **Persist uuid at end of turn.** After the turn's loop exits (before `return`), call `write_sdk_session(session_id, event_state["sdk_session_id"], turns+1)`. Emit a `session.idle` event (new kind) instead of always falling through to the evolution loop.
- [x] 6. **Drop the auto evolution tail.** In `follow_session` / `_follow_session_inner`, remove the automatic `evolution_prompt` loop after research completes. A turn now ends at `session.idle`. (Keep `follow_session` as the crash-logging wrapper.) Evolution stays reachable via its own `/evolution/commands` endpoint.
- [x] 7. **CLI args.** `research/runtime.main` gains `--message` (defaults to `--task` for the first turn) and `--resume <uuid>`. `main` calls `run_session` directly (no evolution tail).

### API

- [x] 8. **Shared spawn helper.** Extract `_spawn_research(sid, message, resume_uuid=None)` in `api/server.py` from the body of `start_research`; both start and follow-up use it. Pass `--resume`/`--message` through.
- [x] 9. **Repurpose `/messages` into the resume endpoint.** `post_human_directive(sid, body)` becomes: 404 if no session dir; **409 if a turn is already running** (`sid in _RUNNING`) — surface "session busy; stop it or wait"; else read `read_sdk_session(sid)` for the uuid and `_spawn_research(sid, body.text, resume_uuid)`. Make it `async`. Drop the dead `bus.send` path.
- [x] 10. **Clean up the dead inbox claims.** Fix the now-inaccurate docstring in `research/runtime.py:3` and the "Drain your inbox with `bus.drain`" line in `research/supervisor_prompt.md:13` (the supervisor never had a drained inbox). Keep `bus` for agent→human reports.

### UI

- [x] 11. **Chat input resumes.** In `ui/app.py`, when a research `session_id` already exists, the bottom `st.chat_input` POSTs to `/research/sessions/{sid}/messages` (not a new session). On 409, show "still working — Stop first or wait". Only the welcome screen (no session) POSTs to `/research/sessions`.

### Verification + tests

- [x] 12. **Resume smoke test.** `tests/test_resume.py`: start a session with a task that states a fact ("My sample is labeled XJ-7"), wait for idle, capture the persisted uuid, then send a follow-up ("What did I call my sample?") and assert the reply references "XJ-7". Guarded to skip without `ANTHROPIC_API_KEY` (it costs real tokens). Add a cheap unit test that `read/write_sdk_session` round-trips and that `/messages` returns 409 when `_RUNNING` contains the sid.
- [x] 13. **Backend verification (per CLAUDE.md policy).** Drive it with curl: start session, tail events to `session.idle`, `GET` nothing-running, POST a follow-up to `/messages`, confirm a contextual reply in events.jsonl, then `docker compose restart coscientist-api` and confirm a *further* follow-up still resumes (durability across restart). Document the event trace in **Notes**.
- [x] 14. **Flip status + ROADMAP.**

## Files touched

- `scaffold/settings.py` — `CLAUDE_CONFIG_DIR`, `sdk_session_file`, `read/write_sdk_session`, dir creation.
- `research/runtime.py` — resume-aware `run_session`, uuid capture/persist, drop evolution tail, CLI args, docstring fix.
- `research/supervisor_prompt.md` — remove the dead `bus.drain` inbox instruction.
- `api/server.py` — `_spawn_research`, resume `/messages` endpoint (409-on-busy), `_runtime_env` gets `CLAUDE_CONFIG_DIR`.
- `ui/app.py` — chat input resumes an existing session.
- `tests/test_resume.py` — new.
- `ROADMAP.md`, this plan.

## Verification

```bash
# host
PYTHONPATH=. python3 -m pytest tests/test_resume.py -q          # unit parts run; live part skips w/o key

# end-to-end (needs ANTHROPIC_API_KEY)
SID=$(curl -s -X POST localhost:8765/research/sessions -H 'Content-Type: application/json' \
  -d '{"task":"Remember: my sample is XJ-7. Acknowledge."}' | jq -r .session_id)
# ...wait for session.idle in:
curl -N localhost:8765/events/$SID
# follow-up resumes context:
curl -X POST localhost:8765/research/sessions/$SID/messages -H 'Content-Type: application/json' \
  -d '{"text":"What is my sample called?"}'        # reply must say XJ-7
docker compose restart coscientist-api              # durability check
curl -X POST localhost:8765/research/sessions/$SID/messages -H 'Content-Type: application/json' \
  -d '{"text":"And again, what was it?"}'           # still XJ-7 after restart
```

## Risks / open questions

- **Concurrent message while a turn runs.** v1 = 409 (busy). A nicer model — queue the message to the bus inbox and have the runtime drain it at end of turn before exiting — finally gives the dead inbox a purpose; deferred to a follow-up, noted so we don't forget.
- **`CLAUDE_CONFIG_DIR` collisions.** The API server itself is a Claude Code-spawned process in dev; make sure pointing subprocess config at `state/.claude` doesn't clobber anything the host cares about. It's inside the container under `/app/state` at runtime, so fine; only matters for host-side `pytest` runs (use a temp dir there).
- **Transcript growth.** Long conversations grow the JSONL under `state/.claude`. The SDK's `cleanupPeriodDays` sweeps local transcripts; confirm that doesn't silently evict a session the user still wants. Surface retention as a later concern.
- **Evolution sessions.** Same resume mechanism should apply to `evolution/runtime.py`, but evolution turns mutate a worktree — resuming must re-attach or recreate the worktree. Out of scope here; research-only for R10. File a follow-up if evolution multi-turn is wanted.
- **max_turns per turn.** `MAX_TURNS=80` now bounds a *single* turn's agentic work; with resume each human turn gets its own budget. Probably fine; revisit if a turn hits the cap mid-thought.

## Notes

(Append discoveries here as you work the steps. Don't rewrite earlier entries.)

- 2026-06-23: Plan drafted after tracing the single-turn root cause. Confirmed SDK 0.1.68 has `resume`/`session_id`/`fork_session`/`session_store`; `ResultMessage.session_id` carries the CLI uuid; transcripts default to `~/.claude/projects` which is NOT on the `./state` persistent mount (only `./state:/app/state` is mounted per `docker-compose.yml`) — hence step 1.
- 2026-06-23: Implemented. Refactored the turn loop out of `run_session` into `_turn_loop`; `run_session(session_id, message, resume_uuid)` now persists the SDK uuid in a `finally`, and a thin `run_turn` wrapper logs crashes and always emits `session.idle` last. The `--message` arg ended up named `--task` (kept the existing flag name; it now carries the per-turn message). Live test uses the real repo root, not a tmp reparent — the agent needs `roles/`/`prompts/`, so reparenting `COSCIENTIST_ROOT` crashes it (caught during testing).
- 2026-06-23: **Verified.** `pytest tests/test_smoke_v0.py tests/test_resume.py` → 13 passed with key (1 skips without). Live in-process two-turn resume: turn 1 "Acknowledged — your sample is labeled XJ-7" (uuid `fd2854f6…`), turn 2 resumed SAME uuid → "You said your sample is labeled XJ-7." Transcript confirmed under `state/.claude/projects/.../<uuid>.jsonl` (persistent mount). HTTP e2e via uvicorn: `POST /research/sessions` (BLUE-42) → `session.idle`; `POST /messages` → event trace `message.received → human_directive → turn.start(resumed) → bus.send "The experiment code is BLUE-42." → session.idle`. **Durability across restart**: killed + restarted the API process (empty `_RUNNING`), a 3rd `/messages` still resumed and recalled BLUE-42 (uuid read from disk). Error paths over HTTP: 404 unknown, 409 no-prior-turn, 409 busy (unit). Used uvicorn locally rather than `docker compose restart` since the image still has bug B1 (no pytest); the process-restart test exercises the same durability guarantee.
