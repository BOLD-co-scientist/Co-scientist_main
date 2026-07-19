# coscientist — Frontend Design Brief (UI3)

> **What this file is.** The single source of truth for the UI3 rewrite that replaces
> the Streamlit app (`ui/app.py`) with a React SPA. It is written to serve **two
> consumers**:
>
> 1. **Claude Design (claude.ai/design)** — reads the *screen inventory*, *per-screen
>    data & states*, and *visual direction* to produce a component library / mockups.
>    **Paste sections 1–4 and 8 into Claude Design.** Do **not** paste the whole repo —
>    the scaffold, Docker rules, and security guards are noise to a design tool.
> 2. **Claude Code (here)** — reads the *API reference* and *integration appendix*
>    (sections 5–7) to assemble the synced components into React and wire them to the
>    live backend.
>
> **Labor split:** Claude Design does the *look*; Claude Code does the *behavior*
> (auth, SSE, uploads, routing). The design tool hands back a component library via
> the `/design-sync` skill, **not** a running app.
>
> **Verification status of this brief:** routes, schemas, event kinds, and auth flow
> below were read directly from `api/server.py`, `api/schemas.py`, and `api/auth.py`
> on 2026-07-09. Items marked **⚠ not-yet-wired** have no backend endpoint yet.

---

## 1. Product in one line

A workbench for AI-assisted science. A human principal talks to a **research
supervisor agent** (which dispatches subagents), watches a **live event stream** of
what the agents are doing, approves or rejects **sensitive actions (HITL)**, manages a
**file library**, and (later) issues **evolution commands** that let the system modify
its own code. Human-in-the-loop is the paramount design constraint: approval and
visibility are first-class, not buried.

## 2. Tech stack (decided)

- **Vite + React + TypeScript.**
- **Styling: Tailwind + CSS variables** for theming (dark-first, light supported).
  *Not* runtime CSS-in-JS/JSS — this is a streaming, data-dense dashboard and runtime
  CSS-in-JS adds re-render cost on the event stream.
- **State/data:** lightweight fetch layer + a small store (Zustand or React Query).
  SSE drives live session state; REST for everything else.
- **Single origin behind one proxy** so the SPA and the API share an origin (avoids
  CORS and lets Bearer auth flow cleanly). See §7.

## 3. Screen inventory

The current Streamlit app has these surfaces (from `ui/app.py`): **Chat**, **Library**,
**Session History**, **Human-in-the-Loop**, an **Agent API key** panel, and a **git
graph**. UI3 keeps these and adds first-class **Live Event Stream** and **Evolution**
surfaces. Target layout: a persistent left rail (sessions + nav), a main column
(chat + stream), and a right rail (HITL + files + context).

| # | Screen | Purpose | Primary data source |
|---|--------|---------|---------------------|
| A | **Login / auth gate** | Enter Bearer API key (`csk_…`), validate via `/auth/me`, persist locally | `GET /auth/me` |
| B | **Session list (left rail)** | All sessions, newest first, running/idle badge, last activity | `GET /sessions` |
| C | **Research chat + composer** | Start a session; multi-turn conversation with the supervisor; interject during a HITL wait | `POST /research/sessions`, `POST /research/sessions/{sid}/messages`, `POST /research/sessions/{sid}/interject` |
| D | **Live event stream** | Real-time feed of agent activity for the open session (tool calls, subagent dispatch, reports, checkpoints) | `GET /events/{sid}` (SSE) + `GET /sessions/{sid}/events` (backfill) |
| E | **HITL approval panel** | Show pending approval requests; approve/reject with an optional note | `GET /hitl/{sid}/pending`, `POST /hitl/{sid}/{request_id}/answer` |
| F | **Session files** | List + download agent-generated outputs (`results/`, `scratch/`) | `GET /sessions/{sid}/files`, `GET /sessions/{sid}/files/download` |
| G | **File library** | Upload / list / delete shared input files; storage health | `GET/POST /library/files`, `DELETE /library/files/{name}`, `GET /library/health` |
| H | **Roles inspector** | Read-only view of the agent roles (supervisor + subagents), their tools, model, spawn ability | `GET /roles` |
| I | **Memory search** | Query global + project memory layers | `GET /memory/{layer}` |
| J | **Evolution console** | Issue self-modification commands; watch worktree/edit/merge lifecycle; approve merges (HITL reuse) | ⚠ **not-yet-wired** — no `/evolution/*` route exists; `evolution.*` events already flow through the same event stream |
| K | **Git history / evolution graph** | Branch graph of the system repo (what the evolution agent changed) | `GET /git/history` (**public, no auth**) |
| L | **Agent key settings** | Set/clear a per-user Anthropic key used for agent runs | `GET/PUT/DELETE /auth/agent-key` |

## 4. Per-screen data shapes & states

Design every screen for **all five states**: `loading`, `empty`, `streaming/active`,
`error`, `success/idle`. The states below are the ones that actually occur.

### A. Login
- Input: single password-style field (API key `csk_u_…`). Validate against `GET /auth/me`.
- States: idle → validating → invalid-key (401) → unreachable-API → authenticated.
- On success show `display_name`. Persist key client-side (localStorage). 401 anywhere → bounce back here.

### B. Session list
- Item = `SessionSummary`: `{ session_id, task?, mtime, last_ts?, last_kind?, running }`.
- `running: true` → live badge/pulse. `last_kind` drives a status chip (e.g.
  `research.complete` = done, `session.crashed` = error, `hitl.pending` = needs you).
- Empty state: "No sessions yet — start one below."

### C. Research chat
- Compose task → `POST /research/sessions {task}` → `{session_id, task}`.
- Follow-up in an idle session → `POST …/messages {text}`. **409** if a turn is still
  running (show "agent is working — stop it or wait") or if there's no resumable turn yet.
- Interject while blocked on HITL → `POST …/interject {text}` (works even mid-run).
- Chat bubbles are *derived from the event stream* (see D), not a separate message list.
  Human turns: `research.requested`, `message.received`. Agent turns: `report`,
  `research.complete`, `turn.start`/`session.turn_result`.
- Stop button → `POST /sessions/{sid}/stop` (rejects all pending HITL too).

### D. Live event stream — **the centerpiece**
- Each event: `{ id, ts, session, actor, kind, …fields }`.
  - `actor` ∈ `human` | `system` | `supervisor` | a subagent role name.
  - `ts` is `"YYYY-MM-DD HH:MM:SS"` (server local time, **not** ISO/UTC).
  - `id` is a 12-hex string; use it as the SSE cursor (`?since=<id>`) and React key.
- **Full event `kind` taxonomy** (verified from source — design an icon/treatment per group):

  | Group | Kinds | Suggested treatment |
  |-------|-------|--------------------|
  | Session lifecycle | `session.start` `turn.start` `session.turn_result` `session.idle` `session.end` `session.interrupted` `session.crashed` `research.requested` `research.complete` `research.crashed` | Timeline spine / status changes |
  | Human ↔ agent | `human_directive` `message.received` `interrupt_feedback` `report` | Chat bubbles |
  | Tool activity | `tool.use` `tool.missing` | Collapsible "agent used X" rows |
  | Subagent bus | `bus.send` `bus.drain` | Dispatch/handoff indicators |
  | HITL | `hitl.pending` `hitl.ask.resolved` `hitl.answer` `ask` `checkpoint.triggered` `checkpoint` `checkpoint.resolved` | **Prominent** — routes to panel E |
  | Evolution | `evolution.start` `evolution.proposal` `evolution.tool` `evolution.note` `evolution.merged` `evolution.rejected` `evolution.auto_reject` `evolution.error` `evolution.end` `evolution_live_check` `evolution_merge` | Console J + graph K |
  | Skills | `skill.proposed` `skill.approved` `skill.rejected` `skill_proposal` `skill.reflection_error` | Evolution-adjacent cards |
  | Long jobs (GPU/CPU) | `longjob.proposed` `longjob.submitted` `longjob.cancelled` `longjob.rejected` `longjob_submit` | Job-status chips w/ progress |
- States: connecting → live (autoscroll w/ "jump to latest") → reconnecting → ended.
  Group consecutive same-actor events. Backfill via `GET /sessions/{sid}/events?limit=`
  (1–1000) before opening the SSE, then stream from the last id.

### E. HITL approval panel
- `GET /hitl/{sid}/pending` → array of request records (free-form JSON; always has an
  `id`; typically a prompt/summary + the action being gated). Treat unknown fields as
  displayable detail.
- Answer: `POST /hitl/{sid}/{request_id}/answer {decision: "approve"|"reject", note?}`.
- This is the **most safety-critical UI**: make the gated action legible (what will
  happen if approved), make approve/reject unmissable, support an optional note.
- Empty state is the common one: "No approvals pending."

### F. Session files
- `SessionFile`: `{ path, size, mtime }` — `path` is relative (e.g. `results/summary.md`).
- Download: `GET /sessions/{sid}/files/download?path=<path>` — **authenticated blob**
  (see §7; can't be a plain `<a href>`). Render markdown/CSV/images inline where possible.

### G. Library
- `LibraryFile`: `{ name, size, mtime }`. Upload = multipart `POST /library/files`
  (field name `file`), returns `{ name, size, overwrote }`.
- `LibraryHealth`: `{ file_count, total_bytes, disk_free_bytes, staging_files, broken_symlinks[] }`
  → a small storage-health strip; warn on broken symlinks / low disk.

### H. Roles inspector
- `RoleSummary`: `{ name, description, tools[], can_spawn, model }`. Read-only cards.

### I. Memory search
- `GET /memory/{layer}` where `layer` ∈ `global` | `project` (project needs `?sid=`),
  plus `?q=<query>&k=5`. Returns `{ layer, hits[] }` (hits are free-form JSONL records).

### J. Evolution console — ⚠ design now, wire later
- No HTTP endpoint yet (`StartEvolutionRequest/Response` schemas exist but are unused;
  evolution currently launches via CLI/subprocess). Design the console against the
  `evolution.*` event lifecycle (they already stream through `/events/{sid}`):
  `start → proposal → tool… → live_check → merged | rejected | auto_reject | error → end`.
- Merges are gated by the same HITL panel (E). When the backend adds
  `POST /evolution/commands`, wiring is a drop-in.

### K. Git / evolution graph
- `GitHistory`: `{ commits: GitCommit[], head }`, `GitCommit`:
  `{ sha, parents[], author, ts, refs[], subject }`. **Public endpoint (no auth).**
  Render a branch graph; highlight `head` and evolution-merge commits.

### L. Agent key settings
- `AgentKeyStatus`: `{ has_custom_key, default_available }`. PUT `{api_key}` to set,
  DELETE to clear. Show whether runs use the user key or the server default.

## 5. API reference (verified 2026-07-09)

Base URL: env-configurable, default `http://127.0.0.1:8765`. **All routes require
`Authorization: Bearer <csk_…>` except** `GET /health` and `GET /git/history`.

| Method | Path | Body | Returns |
|--------|------|------|---------|
| GET | `/health` | — | `{ ok, root }` (public) |
| GET | `/auth/me` | — | `AuthMe { user_id, display_name, root }` |
| GET | `/auth/agent-key` | — | `AgentKeyStatus` |
| PUT | `/auth/agent-key` | `{ api_key }` | `AgentKeyStatus` |
| DELETE | `/auth/agent-key` | — | `AgentKeyStatus` |
| POST | `/research/sessions` | `{ task, session_id? }` | `{ session_id, task }` |
| POST | `/research/sessions/{sid}/messages` | `{ text }` | `{ ok, session_id, resumed }` · 404/409 |
| POST | `/research/sessions/{sid}/interject` | `{ text }` | `{ ok, queued }` · 404 |
| POST | `/sessions/{sid}/stop` | — | `{ ok }` · 404 if not running |
| GET | `/sessions` | — | `SessionSummary[]` |
| GET | `/sessions/{sid}/events?limit=100` | — | `event[]` (limit 1–1000) |
| DELETE | `/sessions/{sid}` | — | `{ ok, session_id }` · 409 if running |
| GET | `/sessions/{sid}/files` | — | `SessionFile[]` |
| GET | `/sessions/{sid}/files/download?path=` | — | file blob |
| POST | `/library/files` | multipart `file` | `LibraryUploadResponse` |
| GET | `/library/files` | — | `LibraryFile[]` |
| DELETE | `/library/files/{name}` | — | `{ ok, name }` |
| GET | `/library/health` | — | `LibraryHealth` |
| GET | `/hitl/{sid}/pending` | — | pending record[] |
| POST | `/hitl/{sid}/{request_id}/answer` | `{ decision, note? }` | `{ ok }` |
| GET | `/events/{sid}?since=<id>` | — | **SSE** `text/event-stream` |
| GET | `/roles` | — | `RoleSummary[]` |
| GET | `/memory/{layer}?q=&k=5&sid=` | — | `MemorySearchResult` |
| GET | `/sessions/{sid}/inbox/{agent}` | — | message record[] |
| GET | `/git/history?limit=200` | — | `GitHistory` (public) |
| POST | `/evolution/commands` | `{ command, session_id? }` | ⚠ **not implemented yet** |

## 6. Event stream (SSE) contract

- `GET /events/{sid}` streams `text/event-stream`. Each message is
  `data: <json>\n\n` where `<json>` is one event record. The server long-polls its
  event log every ~0.5 s and flushes new records; the connection stays open.
- Resume/cursor: pass `?since=<last-event-id>` to only get events after that id.
- **Pattern:** on opening a session, `GET /sessions/{sid}/events?limit=200` for
  backfill/history, render it, then open the SSE with `?since=<last id you rendered>`.
- The stream never sends a terminal event by itself — infer "done" from
  `research.complete` / `session.end` / `session.crashed` kinds and close the reader.

## 7. Integration appendix (the risky part — Claude Code owns this)

These are the things a design tool cannot do blind; budget real effort here.

1. **Bearer auth on SSE.** The native `EventSource` API **cannot set headers**, so it
   cannot send `Authorization: Bearer`. Use a **fetch-based SSE reader**
   (`fetch(url, { headers })` + `response.body.getReader()` + a line parser) instead of
   `new EventSource(...)`. This is non-negotiable given the auth model.
2. **Authenticated file download.** `/sessions/{sid}/files/download` needs the Bearer
   header, so a plain `<a href>` won't work. `fetch` → `blob()` → object URL →
   programmatic download (mirror the current Streamlit `_fetch_session_file`).
3. **Authenticated upload.** `POST /library/files` is multipart with field name `file`;
   send the Bearer header, show progress, handle `413` (exceeds `LIBRARY_MAX_BYTES`).
4. **Single origin / proxy.** Serve the SPA and proxy `/…` API calls through one origin
   (Vite dev proxy in dev; the same reverse proxy in the container) so there's no CORS
   dance and the Bearer flow is uniform.
5. **401 handling.** Any 401 → clear stored key + user + open session, bounce to login.
6. **409 handling on `/messages`.** Session busy or no resumable turn — surface as a
   non-destructive inline notice, keep the composer's text.
7. **Timestamps are server-local strings** (`"YYYY-MM-DD HH:MM:SS"`), not ISO. Parse as
   local; don't assume UTC or a `Z` suffix.
8. **Tenancy is implicit.** Everything is scoped to the authenticated user server-side;
   the client never sends a user id — just the Bearer key.

## 8. Visual direction

- **Feel:** a calm, precise scientific instrument — not a chat toy, not an enterprise
  dashboard. Dense but legible. The human is in command; the UI should make agent
  activity *observable* and approvals *deliberate*.
- **Dark-first**, with a real light theme via CSS variables. Avoid Streamlit's boxy,
  full-width-card look and its 2 s full-page refresh jank — the whole point of UI3.
- **Hierarchy:** left rail (sessions/nav) · main column (chat + live stream) · right
  rail (HITL + files + context). HITL must be able to pull focus (e.g. a pinned banner)
  when something is pending.
- **The event stream is the signature component** — invest here. A readable timeline
  with per-`kind` iconography, actor color-coding, collapsible tool calls, grouped runs,
  and clear "live vs. historical" affordance.
- **Motion:** subtle streaming affordances (typing/working pulse, new-event fade-in).
  Nothing that fights a fast-moving log.
- **Accessibility:** legible at density, keyboard-navigable approval actions, color
  never the only signal for status (running/error/pending also get an icon/label).

---

### Handoff checklist
- [ ] Paste §§1–4, §8 into **Claude Design**; generate the component library / mockups.
- [ ] Pull the library back with **`/design-sync`** (incremental, one component at a time).
- [ ] Scaffold Vite+React+TS+Tailwind; assemble components into screens A–L.
- [ ] Wire against the live backend using §§5–7; verify with the curl + drive-the-SPA pass.
- [ ] Leave Evolution console (J) as a designed-but-stubbed surface until
      `POST /evolution/commands` lands.
