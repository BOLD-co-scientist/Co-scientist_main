# R5 — Cost ledger + tier routing + caching

**Status:** Planned
**Owner:** unassigned
**Started:** —
**Done when:** Every session writes per-call cost rows to `state/sessions/<sid>/cost.jsonl` with running totals in `cost.json`; sessions with `COSCIENTIST_BUDGET_USD` set get auto-blocked at 100% via a Stop hook; roles declare a `model_tier` that resolves to the right Claude model ID; supervisor's long system prompt is cache-eligible (1h TTL).

## Why

Multi-agent fan-out is ~15× tokens (Anthropic's published number for the parallel-subagent win). Without cost discipline this becomes researcher-unfriendly fast. Production patterns reliably cite 70–90% cost cuts from prefix caching alone. Tier routing (Haiku triage → Sonnet default → Opus synthesis/critique) is the consensus pattern.

Sources: [Anthropic Agent SDK cost-tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking), [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Anthropic Agent SDK hooks](https://code.claude.com/docs/en/agent-sdk/hooks).

## Steps

### Settings + env

- [ ] 1. Extend [`scaffold/settings.py`](../../scaffold/settings.py) with `MODEL_TRIAGE`, `MODEL_DEFAULT`, `MODEL_SYNTHESIS` (env-driven, sensible defaults: `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-7`), and `SESSION_BUDGET_USD` (default 5.00).
- [ ] 2. Update [`.env.example`](../../.env.example) to expose all four. Document briefly.

### Tier routing

- [ ] 3. Add `model_tier: str | None` to [`config_loader.RoleConfig`](../../scaffold/config_loader.py). Allowed values: `triage`, `default`, `synthesis`. (None = use `model` field directly.)
- [ ] 4. In [`scaffold/spawn.py`](../../scaffold/spawn.py), add `resolve_model(role: RoleConfig) -> str` that returns the tier-mapped model ID, falling back to `role.model`, falling back to `MODEL_DEFAULT`. Use it everywhere `role.model` was used.
- [ ] 5. Update [`roles/supervisor.yaml`](../../roles/supervisor.yaml) and [`roles/subagents/*.yaml`](../../roles/subagents/) to use `model_tier` instead of pinned `model`. Keep `model` as an opt-out.

### Cost ledger

- [ ] 6. Create [`scaffold/cost.py`](../../scaffold/cost.py) with class `CostLedger(session_id, budget_usd)` exposing:
  - `observe(rm: ResultMessage)` — appends a row to `cost.jsonl`, updates rollup `cost.json`, fires `budget.warn` event at 70% spend.
  - `stop_hook()` — returns a `HookMatcher` that blocks via `{"continue_": False, "decision": "block", ...}` when spend ≥ budget.
  - Cost row schema: `{ts, model, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, cost_usd}`.
- [ ] 7. Wire `CostLedger` into [`research/runtime.py`](../../research/runtime.py): instantiate per session, register `stop_hook()` in `ClaudeAgentOptions(hooks=...)`, call `ledger.observe(msg)` for every `ResultMessage` in the receive loop.
- [ ] 8. Wire same into [`evolution/runtime.py`](../../evolution/runtime.py).

### Caching

- [ ] 9. The `ENABLE_PROMPT_CACHING_1H=1` env is already set by R1's `telemetry.py`. Verify (after first run) that `cost.jsonl` rows show non-zero `cache_read_tokens` on second-and-later turns of a single session. If not, troubleshoot before claiming this step done. (Custom `cache_control` injection on long static prefixes is blocked on [claude-agent-sdk #626](https://github.com/anthropics/claude-agent-sdk-python/issues/626) — accept auto-caching only for v0.5.)

### Tests

- [ ] 10. Create [`tests/test_cost.py`](../../tests/test_cost.py):
  - Construct a `CostLedger` with `budget_usd=0.001`, feed two fake `ResultMessage`-shaped objects, assert `stop_hook` blocks on the second.
  - Tier-routing resolution: a role with `model_tier=triage` resolves to `MODEL_TRIAGE`; a role with explicit `model=...` keeps that.

### Documentation

- [ ] 11. Add a "Cost & budgets" section to [`CLAUDE.md`](../../CLAUDE.md). Document `COSCIENTIST_BUDGET_USD` and tier env vars.
- [ ] 12. Mark **Status: Done**, move the row in [`ROADMAP.md`](../../ROADMAP.md), commit.

## Files touched

- New: `scaffold/cost.py`, `tests/test_cost.py`
- Modified: `scaffold/settings.py`, `scaffold/config_loader.py`, `scaffold/spawn.py`, `research/runtime.py`, `evolution/runtime.py`, `roles/supervisor.yaml`, `roles/subagents/*.yaml`, `.env.example`, `CLAUDE.md`

## Verification

```bash
# 1. Set a tiny budget
echo 'COSCIENTIST_BUDGET_USD=0.001' >> coscientist/.env

# 2. Run a session — expect it to be auto-blocked
curl -X POST http://localhost:8765/research/sessions \
  -d '{"task":"Print 1+1 then summarize the answer in 200 words."}'

# 3. Inspect cost ledger
cat coscientist/state/sessions/<sid>/cost.jsonl
cat coscientist/state/sessions/<sid>/cost.json
# events.jsonl should contain budget.warn and budget.exhausted entries
```

## Risks / open questions

- **`total_cost_usd` is a client-side estimate.** Anthropic explicitly warns: don't bill end users from it; reconcile with the Usage & Cost API. We do not bill anyone — this is fine for our use case but document it clearly.
- **Stop hook fires post-turn.** Mid-turn pre-emption requires `PreToolUse`. v0.5 accepts post-turn enforcement; mid-turn is a follow-up.
- **`cache_control` not exposed in `ClaudeAgentOptions`** ([#626](https://github.com/anthropics/claude-agent-sdk-python/issues/626)). Auto-caching covers our supervisor's static prefix but not custom blocks. Revisit when SDK ships the field.
- **Opus 4.7 tokenizer change.** Up to ~35% more tokens for the same text vs. Opus 4.5. Budget defaults assume Opus 4.7; document under "Cost & budgets" in CLAUDE.md.

## Notes

- 2026-04-26 — Plan drafted. Considered LiteLLM as a proxy; rejected — adds an architectural hop for negligible benefit since the SDK already auto-caches and exposes `total_cost_usd`. Considered `tokencost` as fallback calculator; rejected — its `pricing_table.md` lacks 4.5+ models as of writing. Source of truth = SDK's own usage reports.
