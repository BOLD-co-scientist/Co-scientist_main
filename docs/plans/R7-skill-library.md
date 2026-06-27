# R7 — Skill library (per-user, instruction-only, approval-gated)

**Status:** Done (v1) — admin console + executable cache remain as later tracks
**Owner:** unassigned
**Started:** 2026-06-27
**Done when:** a research session can, on natural finish, propose a reusable `SKILL.md`; the human approves it via HITL; the skill is written to that user's `.claude/skills/`; and a later session of the same user discovers and can invoke it.

## Why
The evolution loop is purely human-commanded today. We want it *guided*: when a research session follows a coherent, reusable workflow, the system should distil it into a reusable **skill** and ask the human to keep it — a self-improving capability cache with a human gate. The Claude Agent SDK already supports skills natively (`ClaudeAgentOptions.skills`), and the HITL approval gate already exists, so this is mostly wiring, not new machinery. Skills are **per-user** (each user's instance accrues its own); an admin console to view/manage/promote across users is a later track.

## Design (decided 2026-06-27)
- **Scope:** research-agent workflows → skills the research agent reuses. (Not evolution recipes; not executable code — those are later.)
- **Form:** instruction-only `SKILL.md` (Claude Code skill format: YAML frontmatter `name` + `description`, body = the procedure). No executable scripts in v1.
- **Storage:** per-user, at `<user root>/.claude/skills/<name>/SKILL.md` (under the rw `state/` mount; self-contained per tenant). No shared/global store in v1.
- **Discovery (verified against SDK 0.1.68):** `skills="all"` auto-injects the `Skill` tool and defaults `setting_sources` to discover *project* skills from `.claude/skills/` relative to **cwd**. The research runtime already sets `cwd = settings.ROOT` (= the user's root in the per-tenant subprocess), so per-user skills are found natively. We pin `setting_sources=["project"]` so discovery is scoped to the user root (not the container's `~/.claude`).
- **Trigger:** at the end of a research turn (natural finish only, not stop/reject), a reflection nudge invites the supervisor to call `propose_skill` if the session followed a reusable workflow. Guarded so trivial turns don't reflect.
- **Approval + persistence:** `propose_skill` is an MCP tool (mirrors `evolution/propose_merge`): it raises `hitl.ask(kind="skill_proposal")` (blocks, shows in the UI HITL panel with a preview); on **approve**, the runtime writes the `SKILL.md` into its own root's `.claude/skills/`. No system-repo commit, no API persistence logic — the subprocess writes its own root, which it already owns.
- **Audit:** `skill.proposed` / `skill.approved` / `skill.rejected` events; proposals archived under the user's state for inspection.

## Steps
- [x] 1. `scaffold/settings.py`: `SKILLS_DIR`, `SKILL_PROPOSALS_ARCHIVE`, `SKILL_REFLECTION` flag; `ensure_runtime_dirs` makes the skills dir.
- [x] 2. `api/tenancy.py` (`_ensure_runtime_dirs`): create `<root>/.claude/skills/` (+ archive) for every user.
- [x] 3. `scaffold/skills.py` (new): `list_skills()`, `skill_path`, `save_skill`, `build_skill_md`, `run_proposal` (testable) + `make_propose_skill_server`.
- [x] 4. `research/runtime.py` `_build_options`: `skills="all"`, `setting_sources=["project"]`; register `propose_skill` + allow `mcp__propose_skill`.
- [x] 5a. In-process verification: store round-trip + propose_skill approve/reject/chat-reply/guard flows + tenancy dir (15/15).
- [ ] 5b. Live verification: hand-write a skill into a user root, confirm the supervisor discovers + invokes it (batched into final live check).
- [x] 6. `research/runtime.py`: `_maybe_propose_skill` after natural finish (guarded by `SKILL_REFLECTION` + `event_count >= 2`; suspends checkpoints; `client.query` + drain). Skips on stop/interrupt/reject.
- [x] 7. `research/supervisor_prompt.md`: documented `propose_skill` + the Skill tool (reusable/recurring only; not system modification).
- [x] 8. `ui/app.py`: `skill_proposal` HITL (description + SKILL.md preview) + `skill.proposed/approved/rejected` events.
- [x] 9. Live verification: hand-written skill **discovered + invoked** by the supervisor (`Skill` tool used; agent emitted the skill's exact output); trivial turn correctly skipped reflection; no regression to normal sessions. Containers reloaded; pushed.

(Note: the *auto-proposal* firing depends on a substantive turn — `event_count >= 2` — and the agent judging the workflow reusable; the propose→approve→save path itself is in-process-verified 15/15, and reflection errors are caught so they can't break a session.)

## Files touched
- `scaffold/settings.py`, `scaffold/skills.py` (new), `api/tenancy.py`, `research/runtime.py`, `research/supervisor_prompt.md`, `ui/app.py`.
- Tests: extend the smoke/in-process suite with skills-store + propose_skill checks.

## Verification
- In-process: `skills.save_skill`/`list_skills` round-trip in a temp root; `propose_skill` tool resolves via HITL approve→file written, reject→no file.
- Tenancy: `_ensure_runtime_dirs` creates `.claude/skills/`.
- Live (curl + UI): start a research session that does real work → on finish, a `skill_proposal` HITL appears → approve in UI → file lands in `state/users/<uid>/root/.claude/skills/` → a new session of that user can invoke it.
- Gate: `pytest tests/test_smoke_v0.py` + parse all changed `.py`.

## Risks / open questions
- **Reflection cost:** an extra LLM round-trip per turn. Mitigate with a guard (only on substantive turns) and an env flag to disable.
- **SDK discovery edge cases:** confirmed `skills="all"` + `cwd` works; watch for the CLI ignoring the field on older versions (image ships a current CLI).
- **Conversational-HITL interaction:** `hitl.ask` now resolves on a chat reply (`decision="answer"`); for `skill_proposal` treat only `approve` as save, everything else as no-save.
- **Skill-name collisions / unsafe names:** validate kebab-case, dedupe against existing skill dirs.

## Notes
- 2026-06-27: SDK skill discovery verified (`_apply_skills_defaults`: `skills="all"` → injects `Skill` tool, `setting_sources` default `["user","project"]`; project skills read from `.claude/skills/` relative to cwd). Pinning `setting_sources=["project"]` to scope to the user root.
- Admin console (view/manage/**promote per-user skill → shared**) is a separate future track; per-user file layout (`state/users/*/root/.claude/skills/`) makes it a straight directory walk.
