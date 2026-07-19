# R14 — Deep research (OpenAI Deep Research API)

**Status:** In Progress
**Owner:** unassigned
**Started:** 2026-07-09
**Done when:** The supervisor calls OpenAI deep research exactly once per session (after scoping the problem, before implementing) and can also run deep research in the background on request; both persist durable, pollable handles.

## Why
The supervisor plans and implements largely from its own priors. A single, mandatory external deep-research pass at the moment the problem is understood — but before any build/analysis work — grounds the plan in current literature and the real landscape. A background mode lets the human kick off a deep dig and keep working. OpenAI's Deep Research API (Responses API, `o3-deep-research` / `o4-mini-deep-research`, background mode) is the substrate; we wrap it as a durable async job like the R12 long-job runner so a report survives the per-turn supervisor subprocess.

## Design
- **New tool package `tools/deep_research/`** — auto-discovered by `tools_registry` (also wired explicitly).
  - `store.py` — durable handles under `state/sessions/<sid>/deep_research/`, report written to `<rid>/report.md`.
  - `client.py` — lazy-imported OpenAI wrapper (Responses API, `background=True`, `web_search_preview` tool); network calls via `asyncio.to_thread`. Import-safe without the key/SDK so the v0 smoke test still passes.
  - `server.py` — MCP tools: `run` (blocking scope call), `start` (background), `status`, `fetch`, `cancel`, `list`.
- **Mandate** lives in `research/supervisor_prompt.md`: once-per-session `deep_research.run` before implementing; `deep_research.start` for background on request. Prompt-level, matching how other supervisor behaviors are enforced.
- **Enable gate = presence of `OPENAI_API_KEY`.** No HITL prompt on the mandated call by default (would break "automatic once per run"); `COSCIENTIST_DEEPRESEARCH_HITL=1` opts into per-run approval. Cost/egress caveat is in the prompt.

## Steps
- [x] 1. `tools/deep_research/{__init__,store,client,server}.py`.
- [x] 2. Settings: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `DEEPRESEARCH_MODEL`, `DEEPRESEARCH_FALLBACK_MODEL`, `DEEPRESEARCH_MAX_WAIT_S`, `DEEPRESEARCH_POLL_S`, `DEEPRESEARCH_HITL`.
- [x] 3. Register `deep_research` in `tools_registry` + add to `roles/supervisor.yaml`.
- [x] 4. Supervisor prompt: mandatory scope pass + background usage.
- [x] 5. `.env.example` + `pyproject` (`openai>=1.66`).
- [x] 6. Tests: import-safe without key, store roundtrip, no-key friendly error, tool wiring, fallback path, hard-fail-without-fallback.
- [x] 7. **Live verification done (2026-07-09).** Found + fixed two real issues (below). A full host agent run had the supervisor call `deep_research.run` per the mandate, fall back to `o4-mini`, and deliver an 11.5 KB cited landscape to the human — who transparently relayed the fallback caveat.

## Live-run findings & fixes (2026-07-09)
1. **Sandbox lazy-import bug.** The runtime masks site-packages at tool-call time (memory: runtime-sandbox-strips-deps); the original lazy `import openai` inside the tool would raise `ModuleNotFoundError` in-runtime. Fixed: `client.py` imports `openai` at **module load** (process startup, before the mask), guarded so a host/env without the SDK still imports.
2. **OpenAI org-verification gate.** The `*-deep-research` models require OpenAI *organization verification*; unverified, the run submits fine but fails mid-flight ("organization must be verified"). Added an auto-fallback: on that failure the run retries once with `DEEPRESEARCH_FALLBACK_MODEL` (`o4-mini` + web_search, no verification needed) and the report/summary are clearly labeled as fallback output. Once the human verifies the org at platform.openai.com/settings/organization/general, the primary model succeeds with no code change.
   - **To get true Deep Research:** verify the OpenAI org (one-time, ~15 min to propagate), then runs use `o4-mini-deep-research`/`o3-deep-research` automatically.
- Note: `docker exec` into the API container has **no DNS** (`network_mode: service:tailscale`), so the live agent run was done on the host; the container's API-server-spawned runtime has working DNS.

## Files touched
- `tools/deep_research/__init__.py`, `store.py`, `client.py`, `server.py` (new)
- `scaffold/settings.py`, `scaffold/tools_registry.py`
- `roles/supervisor.yaml`, `research/supervisor_prompt.md`
- `.env.example`, `pyproject.toml`
- `tests/test_deep_research.py` (new)
- `ROADMAP.md`, `docs/plans/R14-deep-research.md`

## Verification
- Host, no key needed: `PYTHONPATH=. pytest tests/test_smoke_v0.py tests/test_deep_research.py -q` — imports, store roundtrip, no-key error path, registry wiring.
- With a key (costs ~a few $): drive a session task, confirm one `deep_research.started`/`deep_research.completed` pair in `state/sessions/<sid>/events.jsonl` before any implementation work, and a `report.md` under the session's `deep_research/`. Then a background `start` + `status`/`fetch` round-trip.
