# Plan files

Each file under this directory is one feature's executable plan. Cross-listed in [../../ROADMAP.md](../../ROADMAP.md).

## Why plain markdown

- Anyone (human or Claude session) can read and edit with no tools.
- Step state lives in `- [x]` checkboxes — git diffs show progress.
- No dashboard, no DB, no CLI — fewer moving parts to break.
- TodoWrite-style ephemeral tasks can mirror the next 3–5 unchecked steps for in-session focus.

## File naming

`<RoadmapID>-<short-slug>.md` — e.g. `R1-replay-event-log.md`. The Roadmap ID is the durable handle; the slug is for humans skimming filenames.

## Required structure

```markdown
# <ID> — <Feature title>

**Status:** Planned | In Progress | Blocked | Done | Deferred
**Owner:** <name or "unassigned">
**Started:** YYYY-MM-DD (or "—")
**Done when:** <one-line acceptance criterion>

## Why
Two or three sentences linking back to the rationale (literature, pain point, etc.).

## Steps
Ordered checklist. The first `- [ ]` is "current". Tick as you finish, in order.

- [ ] 1. First concrete step.
- [ ] 2. Next concrete step.
...

## Files touched
Bulleted list of paths created or modified.

## Verification
How to know it works end-to-end: a smoke command, a curl, a test.

## Risks / open questions
Bullets. Real risks, not "what if it breaks". Update as you discover things.

## Notes
Free-form running notes; append, don't rewrite. Discoveries, decisions, dead ends.
```

## Working a plan

1. Read the plan top to bottom.
2. Find the first unchecked step.
3. Do it. Commit. Tick the box.
4. If a step turns out to be wrong or missing, edit the list — don't pretend it was always right. Note the change under **Notes**.
5. When the **Done when** condition holds, flip status to **Done** and update [../../ROADMAP.md](../../ROADMAP.md).

## When NOT to create a plan file

- Trivial fixes (typo, single-line change, dep bump). Just commit.
- One-off experiments. Use a scratch dir, not a plan file.
- Anything you'll finish before the end of today. Plans are for multi-session work.

## What goes in `.claude/plans/` vs. here

`.claude/plans/` (in the parent OpenPhil repo) holds **architectural** plans — the v0 system design lives there. `docs/plans/` here holds **operational** feature work. The architectural plan is mostly read; the operational plans are constantly edited.
