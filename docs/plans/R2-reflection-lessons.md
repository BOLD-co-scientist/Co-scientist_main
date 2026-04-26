# R2 — Reflection → cross-session lessons buffer

**Status:** Planned
**Owner:** unassigned
**Started:** —
**Done when:** Sessions end with a reflection pass that distills ≤5 lessons into a JSONL store; the next research session on a similar task automatically retrieves and surfaces those lessons in the supervisor's prompt; near-duplicate lessons are periodically merged.

## Why

Single highest-ROI persistent component in the literature. Reflexion +20 EM HotpotQA, ExpeL +36/+31, AI Co-Scientist's Meta-review-into-prompt loop. Without R1's traces and R5's tier-routed reflection model, this is expensive and noisy — that's why this is sequenced last.

Sources: [Reflexion (arXiv:2303.11366)](https://arxiv.org/abs/2303.11366), [ExpeL (arXiv:2308.10144)](https://arxiv.org/abs/2308.10144), [Letta on "edit memory, don't append"](https://www.letta.com/blog/rag-vs-agent-memory), [FastEmbed](https://github.com/qdrant/fastembed), [sqlite-vec](https://github.com/asg017/sqlite-vec).

## Steps

### Dependencies + embedding pipeline

- [ ] 1. Add `fastembed>=0.4` and `sqlite-vec>=0.1.6` to [`pyproject.toml`](../../pyproject.toml) under `[project.dependencies]`.
- [ ] 2. Update [`Dockerfile`](../../Dockerfile) to pre-download the embedding model at build time so first session doesn't pay a 30 MB download:
  ```dockerfile
  RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
  ```
- [ ] 3. Create [`scaffold/embed.py`](../../scaffold/embed.py) with a `Embedder` class:
  - Wraps FastEmbed `BAAI/bge-small-en-v1.5` (384-dim).
  - Caches embeddings under `state/embed_cache/<sha8>.npy`.
  - Single method: `embed(text: str) -> np.ndarray`.
- [ ] 4. Add [`tests/test_embed.py`](../../tests/test_embed.py): cache hit/miss, dimensionality, deterministic output.

### Lesson store

- [ ] 5. Create [`scaffold/lessons.py`](../../scaffold/lessons.py) with a `Lessons` class. JSONL = source of truth at `state/memory/lessons.jsonl`. SQLite-vec sidecar at `state/memory/lessons.db` for ANN.
  - Schema: `{id (L-<hex8>), lesson, applies_when, scope (project|domain-general), confidence, evidence_event_ids, source_session, ts}`.
  - Methods: `add(obj)`, `update(id, obj)`, `delete(id)`, `recall(query, k=5, scope=None) -> list[Lesson]`.
  - Recall: top-K cosine via sqlite-vec; if sqlite-vec unavailable, brute-force numpy cosine over all rows (fallback). Optional scope filter.
  - Atomic rewrites for consolidation: write JSONL to tmp, fsync, rename.
- [ ] 6. Add [`tests/test_lessons.py`](../../tests/test_lessons.py): add/update/delete/recall roundtrip; scope filter; recall with sqlite-vec disabled (fallback path).

### Reflector

- [ ] 7. Write [`prompts/reflect.md`](../../prompts/reflect.md). Template:
  - Inputs: `{goal, outcome, trajectory (numbered events), existing_lessons (top-K JSON)}`.
  - Output JSON: `{lessons: [{op: ADD|EDIT|UPVOTE|DOWNVOTE, target_id, lesson, applies_when, scope, confidence, evidence_event_ids}]}`.
  - Hard rules: ≤5 entries; every ADD/EDIT cites ≥1 evidence_event_id; falsifiable lessons; return `{"lessons": []}` if session was clean success.
  (Full template in research notes — paste verbatim.)
- [ ] 8. Write [`prompts/consolidate.md`](../../prompts/consolidate.md). Inputs: a cluster of near-duplicate lessons. Output: a single merged `{op: MERGE, target_ids, lesson, ...}` plus retire-list.
- [ ] 9. Create [`scaffold/reflector.py`](../../scaffold/reflector.py) with `Reflector(session_id)` exposing `async reflect()`:
  1. Reads `state/sessions/<sid>/events.jsonl`. Trim to ~5K tokens (drop low-signal events: bus.send/drain, repeated tool errors after the first).
  2. Reads goal from `events.jsonl`'s `session.start` event.
  3. Calls `recall_lessons(goal, k=5)` for the existing-lessons input.
  4. Calls Claude with `reflect.md` rendered. Use `MODEL_DEFAULT` (Sonnet); reflection at Opus is wasteful.
  5. Parses returned JSON; dispatches ADD/EDIT/UPVOTE/DOWNVOTE via `Lessons` API.
  6. Writes a `reflection_summary.md` artifact under `state/sessions/<sid>/`.
- [ ] 10. Add [`tests/test_reflector.py`](../../tests/test_reflector.py): mock the SDK call; feed a hand-crafted trace and `existing_lessons`; assert correct ops dispatched.

### Consolidator

- [ ] 11. Create [`scaffold/consolidator.py`](../../scaffold/consolidator.py) with `async consolidate(threshold=0.92)`:
  - Embed all lessons; for each unprocessed lesson, find others with `cosine ≥ threshold` in the same scope.
  - For each cluster (size ≥ 2), call Claude with `consolidate.md`; replace cluster with merged lesson; retire others.
  - Atomic JSONL rewrite at end. Run in `O(n²)` over n lessons — fine until ~10k.
- [ ] 12. Add a CLI command in [`coscientist_cli/main.py`](../../coscientist_cli/main.py): `coscientist consolidate --threshold 0.92`.

### Wire into research runtime

- [ ] 13. Extend [`scaffold/system_tools.py`](../../scaffold/system_tools.py) `make_memory_server()` with a `recall_lessons(query, k, scope)` MCP tool that wraps `Lessons.recall`.
- [ ] 14. In [`research/runtime.py`](../../research/runtime.py), at session start: render `lessons_block` from top-5 lessons relevant to the task; inject into the supervisor system prompt under `## Lessons from past sessions`. Edit `supervisor_prompt.md` to include a placeholder `{{LESSONS_BLOCK}}`.
- [ ] 15. In [`research/runtime.py`](../../research/runtime.py), at session end (in the `finally:` of `run_session`): call `await Reflector(session_id).reflect()` if the session has > N events (skip trivial sessions).

### API + introspection

- [ ] 16. Add `POST /sessions/{sid}/reflect` to [`api/server.py`](../../api/server.py) — manually trigger reflection (useful for backfilling).
- [ ] 17. Add `GET /lessons?q=...&k=5&scope=...` to [`api/server.py`](../../api/server.py) — search lessons by query.

### Documentation

- [ ] 18. Add a "Lessons & reflection" section to [`CLAUDE.md`](../../CLAUDE.md). Cover: where lessons live, how to manually reflect, how to consolidate, scope semantics.
- [ ] 19. Mark **Status: Done**, move the row in [`ROADMAP.md`](../../ROADMAP.md), commit.

## Files touched

- New: `scaffold/embed.py`, `scaffold/lessons.py`, `scaffold/reflector.py`, `scaffold/consolidator.py`, `prompts/reflect.md`, `prompts/consolidate.md`, `tests/test_embed.py`, `tests/test_lessons.py`, `tests/test_reflector.py`
- Modified: `pyproject.toml`, `Dockerfile`, `scaffold/system_tools.py`, `research/runtime.py`, `research/supervisor_prompt.md`, `api/server.py`, `coscientist_cli/main.py`, `CLAUDE.md`

## Verification

```bash
# 1. Run a session that should produce a real lesson
curl -X POST http://localhost:8765/research/sessions \
  -d '{"task":"Summarize the LTEM growth-curve CSV; if it is malformed, say so."}'
# wait for completion

# 2. Inspect the lessons file
cat coscientist/state/memory/lessons.jsonl

# 3. Run a similar session and check the supervisor prompt got the lesson injected
# (the supervisor's first turn should reference past lessons; verify in events.jsonl)
curl -X POST http://localhost:8765/research/sessions \
  -d '{"task":"Summarize the LTEM cell-density CSV; report data-quality issues."}'

# 4. Manual consolidation pass
python -m coscientist_cli.main consolidate --threshold 0.92

# 5. Search lessons via API
curl 'http://localhost:8765/lessons?q=malformed%20csv&k=3'
```

## Risks / open questions

- **Cross-session transfer is empirically thinner than same-task retry.** Reflexion's wins are in-task. ExpeL is the closest published evidence for cross-task transfer, and its prompt evolved heavily — expect to iterate on `reflect.md` based on observed lesson quality.
- **Confidence calibration drifts.** Claude over-emits 0.8. Treat confidence as ordinal until we have ground-truth re-runs.
- **"Domain-general" scope is aspirational.** Most v0 lessons leak project specifics. Consolidator should re-tag during merges; don't trust first-pass scope.
- **Consolidation threshold (0.92)** is a guess. Tune it once we have ~50 lessons.
- **`sqlite-vec` is pre-v1.** Pin the version; the numpy fallback in step 5 covers us if we hit a breaking change.
- **Reflection cost.** ~$0.001–0.01 per session at Sonnet. Trim trace aggressively before the call. If cost grows, route reflection to Haiku (triage tier).
- **Privacy.** Lessons can contain task content (file paths, dataset names, hypotheses). `state/memory/` is gitignored; ensure no lessons land in public artifacts.

## Notes

- 2026-04-26 — Plan drafted. Decided against Mem0/LangMem/Letta — all add a server or fight our existing JSONL schema. Anthropic memory tool is a complementary in-session scratchpad, not a competitor; revisit if we want supervisor-side scratch persistence.
- Reflection prompt template in research notes is the v0 starting point; expect 2–3 rewrites in the first month.
