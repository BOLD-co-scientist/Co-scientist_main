# R15 — Autonomous mode (HITL-vs-autonomous benchmark arm)

**Status:** Done
**Branch:** main (built directly on main by request)

## Why

To measure the effectiveness of the human in the loop for the paper, we need a
counterfactual arm: the same system, same prompts, same gates — but no human.
Autonomous mode auto-answers HITL gates so a research session runs end-to-end
unattended. Comparing paired runs (same task, `autonomous` on/off) isolates the
human's contribution.

## Design

- **Per-session marker** `state/sessions/<sid>/control/autonomous`, set at
  creation via a new `autonomous: bool` field on `POST /research/sessions`.
  No env var, no UI — API-only by design.
- **Auto-answer at the gate layer** (`scaffold/hitl.py::open_request`): when the
  marker is present, a pending request is resolved immediately —
  - kind `ask` (open-ended question) → decision `answer` with a fixed note
    telling the agent to proceed on its own best judgment;
  - every other kind (checkpoints, tool gates) → decision `approve`.
  Because it lives in `open_request`, both `ask()` and the
  `open_request`/`poll_answer` checkpoint wait resolve without runtime changes.
- **Never auto-answered:** `evolution_merge` (self-modification keeps the human
  as final gate) and `interrupt_feedback` (only exists because a human pressed
  Stop). Enforced in `hitl.py`, not in prompts.
- **Measurable trace:** creation logs `session.autonomous`; every auto-answer
  logs `hitl.auto_answer` with `actor: "auto"` and `"auto": true` on the
  answered record. Human-arm runs log `hitl.answer` with `actor: "human"`.
  `events.jsonl` alone distinguishes the arms.
- Prompts are untouched: the supervisor still believes a human may be present,
  so both arms see identical instructions — the mode is invisible except at the
  moment a gate resolves.

## Usage (benchmark runs)

```bash
# autonomous arm
curl -X POST http://localhost:8765/research/sessions \
  -H 'Content-Type: application/json' \
  -d '{"task":"<benchmark task>","autonomous":true}'

# human arm — same task, omit the flag (default false)
curl -X POST http://localhost:8765/research/sessions \
  -H 'Content-Type: application/json' \
  -d '{"task":"<benchmark task>"}'
```

Follow-up turns on an autonomous session stay autonomous (the marker persists
in the session dir). To flip a session back:
`python3 -c "from scaffold import hitl; hitl.set_autonomous('<sid>', False)"`.

Analysis: pull `events.jsonl` per session; count/inspect `hitl.auto_answer`
vs human `hitl.answer` events, checkpoint outcomes, wall-clock between gates,
and grade the artifacts in `results/`.

## Steps

- [x] 1. `hitl.set_autonomous`/`is_autonomous` + auto-answer in `open_request`
       (protected kinds excluded).
- [x] 2. API: `autonomous` field on `StartResearchRequest`; marker +
       `session.autonomous` event at session creation.
- [x] 3. Tests (`tests/test_autonomous.py`): flag roundtrip, instant `ask`
       resolution with judgment note, approval gates, checkpoint poll path,
       protected kinds still block, off-by-default, schema default.

## Files touched

`scaffold/hitl.py`, `api/schemas.py`, `api/server.py`,
`tests/test_autonomous.py`, `ROADMAP.md`.

## Verification

- Host: full pytest suite green.
- Backend pass: POST an autonomous session, confirm `session.autonomous` in
  events and that any gate the run hits resolves with `hitl.auto_answer`.
