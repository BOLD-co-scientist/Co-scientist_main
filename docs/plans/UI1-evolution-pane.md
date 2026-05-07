# UI1 — Evolution pane in Streamlit chat

**Status:** Done
**Owner:** yhg01
**Started:** 2026-05-07
**Completed:** 2026-05-07
**Done when:** A user can pick "Evolution" mode from the sidebar, type a meta-prompt, watch the evolution agent's events stream, and approve/reject worktree-merge HITL requests, all from the browser. Research mode behavior is unchanged.

## Why

The `antonio` branch shipped a research-side Streamlit chat (`ui/app.py`) but left the evolution agent reachable only via `curl`. The backend already exposes everything needed (`POST /evolution/commands`, shared `events.jsonl` per session, shared HITL endpoints) — this is purely a UI-side addition. With it, the human principal can drive the meta-agent through the same interface used for research, satisfying objective #1 (HITL is paramount) and #2 (self-evolving) from `CLAUDE.md`.

## Steps

- [x] 1. **Mode toggle in sidebar.** Add `st.radio("Mode", ["Research", "Evolution"])` at the top of the sidebar in `ui/app.py`, persisting to `st.session_state.mode`. Default to "Research". Switching mode clears `st.session_state.session_id`.
- [x] 2. **Filter session history by mode.** Evolution sessions have `evo-` prefix (set in `api/server.py:74`). In Research mode hide `evo-*` from the history list; in Evolution mode show only `evo-*`.
- [x] 3. **Evolution welcome screen.** When `mode=="Evolution"` and `session_id is None`, replace the research welcome with "What should the meta-agent change?" — a `st.chat_input` that POSTs `{"command": <text>}` to `/evolution/commands`, then sets `session_id` from the response and reruns.
- [x] 4. **Reuse events viewer with new branches.** Added render branches for `evolution.requested`, `evolution.proposal`, `evolution.merged`, `evolution.rejected`, `evolution.auto_reject`, `evolution.crashed`. Unknown kinds still fall through to `⚙️ System Event`. (Note: actual emitted kinds per `propose_merge.py` are `evolution.proposal`/`merged`/`rejected`/`auto_reject` — not `merge_proposed` as I'd guessed in the draft.)
- [x] 5. **HITL panel renders diffs.** Confirmed payload shape from `evolution/tools/propose_merge.py:74-85`: carries `archive`, `branch`, `diff_preview` (8000 chars), `strict`, `rationale`. Smoke-test stdout is *not* in payload (it lives in `archive_dir/smoke.log` on host fs); skipped surfacing it for v1. UI now branches on `req["kind"] == "evolution_merge"` and renders branch label, strict warning, rationale (collapsed), and diff_preview (expanded by default).
- [x] 6. **Per-command form in Evolution mode.** In Evolution mode, replaced bottom `st.chat_input` with a `st.form` ("Queue another command on this session") that POSTs to `/evolution/commands` reusing the existing `session_id` (schema confirms `session_id` is optional in `StartEvolutionRequest`).
- [x] 7. **Backend verification on host.** Done 2026-05-07. Submitted a non-strict-path command (`Add a YAML comment to roles/supervisor.yaml`) on session `evo-20260507-202833-2335f8`. Confirmed event sequence: `evolution.requested → evolution.start → evolution.tool* → evolution.proposal → hitl.pending → hitl.answer → evolution.merged → evolution.end`. HITL payload `kind=evolution_merge` carried `branch`, `strict`, `rationale`, `diff_preview` — all five fields the UI render path consumes. Approving via `POST /hitl/<sid>/<rid>/answer` with `decision=approve` landed the merge on the worktree (file modified on host fs). Visual render still needs human eyes.
- [x] 8. **Flip Status to Done in ROADMAP.md.**

## Files touched

- `ui/app.py` — only file. Adds a mode toggle, evolution welcome, evolution event-kind branches, evolution HITL diff rendering, evolution per-command form. Estimate ~80–120 added lines.
- `docs/plans/UI1-evolution-pane.md` — this plan.
- `ROADMAP.md` — one-line entry under a new "UI" section.

## Verification

```bash
# from the worktree
cd ../coscientist-evolution_ui
docker compose up -d coscientist-api coscientist-ui
```

1. Open http://127.0.0.1:8501. Mode = "Research". Start a research session, verify nothing regressed.
2. Switch Mode → "Evolution". Welcome prompt appears. Submit `Add a one-line comment at the top of scaffold/settings.py`.
3. Watch events stream (worktree create, edit, smoke tests, merge proposal).
4. When the merge HITL appears in the sidebar, the diff should render. Approve.
5. On the host, `git -C ../coscientist-evolution_ui log -1 --stat scaffold/settings.py` should show the merge.
6. Switch back to Research. Session list shows only research sessions; evolution sessions hidden.

## Risks / open questions

- **Step 5 prerequisite:** does the current evolution HITL payload already include `diff` and smoke-test stdout? If not, that's a small `evolution/tools/propose_merge.py` change to embed those — still a thin scaffold edit, but breaks the "UI-only" framing of this plan. Resolve in step 5 by reading the file before coding.
- **Auto-rerun cadence.** Streamlit's 2s `st.rerun()` loop is fine for research streams but may feel laggy when the merge HITL first arrives. Acceptable for v1; revisit if it bothers users.
- **Single-process evolution UX.** Because evolution runs are short-lived, the user can't "talk to" a running evolution agent — they can only queue another command. If this confuses users, surface a banner explaining the model.

## Notes

(Append discoveries here as you work the steps. Don't rewrite earlier entries.)
