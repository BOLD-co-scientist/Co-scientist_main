# R12 — Long-job runner (async dispatch; auto-engaged for long jobs)

**Status:** In Progress
**Owner:** yhg01
**Started:** 2026-07-01
**Done when:** From a research session, a job too big for `py_exec` (long CPU or GPU) is dispatched as a durable async job on FLAIR (flair-nodes / flair-works), the turn ends immediately, later turns poll status and fetch results — HITL-gated, BOLD-compliant, and the runner is engaged automatically (the agent doesn't hit the 60 s wall and die).

## Why
`tools/py_exec` hard-caps at ~60 CPU-seconds and runs **synchronously** (blocks the turn), so coscientist literally cannot run a real simulation or training job. The fix is a **control-plane / compute-plane split**: the agent stays in its container (LLM orchestration, cheap) and *dispatches* heavy compute as **durable async jobs**, polling for results. This is the same reframe as [R11](R11-remote-cluster-dispatch.md) — R11 is the **ARM64 HPC backend** of this runner (blocked on recon); R12 is the **runner core + the FLAIR docker backend** (unblocked: FLAIR's rules are fully known from [BOLD-server-guide.md](../BOLD-server-guide.md)).

## Scope (confirmed 2026-07-01)
- **Two clusters = flair-nodes + flair-works** — both x86 + Docker, same BOLD SOP → **one docker backend** covers both.
- **ARM64 remote (R11)** stays a **stubbed adapter** until Phase-0 recon lands.
- **Auto-bring-up:** the runner is engaged on-demand — the agent routes known-heavy work to it, and a `py_exec` run that would exceed the cap returns an *escalation* signal instead of a silent death, so long jobs get re-dispatched rather than lost.

## Architecture
```
supervisor / data_analyst
   ├─ py_exec        → short, in-container subprocess (unchanged; ~60 s cap)
   └─ longjob tool   → submit → durable handle (state/sessions/<sid>/jobs/<id>.json)
                         │
                         ▼  BackendAdapter (pluggable)
                ┌──────────────────────────────┬───────────────────────────┐
                │ local  (reference/tests)      │ flair-docker (real)       │  arm64-hpc (R11, stub)
                │ detached in-container proc     │ docker run --gpus on node │  ssh → scheduler
                └──────────────────────────────┴───────────────────────────┘
```
- **Durable, async handles — not synchronous tool calls.** `submit` returns a `job_id` immediately and persists a handle; `status`/`wait`/`fetch`/`logs`/`cancel` advance it. Jobs survive turn-subprocess exits and `docker compose down/up`; a reconcile step re-polls live jobs on resume (reuses the R10 durability posture).
- **BackendAdapter protocol** isolates *where/how* a job runs: `submit(spec, workdir) -> backend_ref`, `poll(ref) -> state`, `logs(ref)`, `cancel(ref)`, `collect(ref, dest)`. The runner core (handles, tools, HITL, routing) is backend-agnostic.
- **HITL-gated submit** (`scaffold/hitl.py`): a job spends real, shared, accountable resources — the same "get the account reaped" risk the BOLD guide guards against — so `submit` shows a resource summary (backend, GPUs, walltime, image) before anything launches, mirroring `propose_merge`.

## The FLAIR docker backend — dispatch security fork (DECIDE before Phase 2)
The runner runs **inside the now-non-root coscientist container**. To launch `docker run --gpus …` on the node it must reach the host Docker **without** regaining root:
- ❌ **Mount `/var/run/docker.sock`** — root-equivalent (mount host `/`, `--privileged`); **undoes the non-root work** (Rule 1). Rejected.
- ✅ **Host-side spooler (recommended):** container writes a job spec to a mounted `state/jobspool/`; a small host daemon **running as the user** executes the allow-listed `docker run`. No docker access / SSH creds in the container; trust boundary = one small auditable host script.
- ✅ **SSH-to-host + command allow-list:** reuse R11's `ssh.py` guard; only sanctioned `docker run/ps/logs/rm` argv, never a shell.
Whichever we pick, the launched job must obey BOLD: **non-root `--user $(id -u):$(id -g)`, explicit `--gpus '"device=N"'`, `${USER}_…` name, `-v` mounts, `--rm`-friendly, no data baked in.**

## Steps
### Phase 1 — Backend-agnostic core (unblocked; build now)
- [x] 1. `tools/longjob/job.py`: `JobSpec` (command, resources{cpu,gpu,walltime,mem}, image, env, backend) + durable handle read/write/list under `state/sessions/<sid>/jobs/`.
- [x] 2. `tools/longjob/backends/base.py`: `BackendAdapter` protocol + states (queued|running|done|failed|unreachable).
- [x] 3. `tools/longjob/backends/local.py`: reference backend — detached in-container subprocess (`start_new_session`, survives the turn), rc + logs in the handle dir.
- [x] 4. `tools/longjob/server.py`: MCP tools `submit` (HITL-gated) / `status` / `wait` / `logs` / `cancel` / `fetch`; auto-wired via the registry's generic loader; granted to `data_analyst`.
- [ ] 5. Reconcile-on-resume: re-poll live jobs when a session subprocess restarts.
- [x] 6. Tests (`tests/test_longjob.py`): submit→run→done→logs→fetch on local backend; HITL reject doesn't launch; handle round-trip; unknown-backend guard. 4/4 + smoke 9/9.

### Phase 2 — FLAIR docker backend (after the dispatch-fork decision)
- [ ] 7. Implement the chosen dispatch channel (host-spooler or ssh-guard) with the command allow-list guard at the tool layer.
- [ ] 8. `backends/flair_docker.py`: build a BOLD-compliant `docker run` (non-root `--user`, explicit `--gpus`, `${USER}_…` name, `-v` mounts), detached; poll `docker ps/logs`; collect outputs from the mounted workdir.
- [ ] 9. Resource caps + attribution enforced at the tool layer; GPU selection respects Rule 2 (never share).
- [ ] 10. Negative test: the dispatch channel rejects anything but the allow-listed docker argv (no shell, no `--privileged`, no `-v /:…`).

### Phase 3 — Auto-bring-up + routing
- [ ] 11. `py_exec` cap-exceeded returns a structured "escalate to longjob" result (not a bare timeout), so the agent re-dispatches instead of losing the work.
- [ ] 12. Supervisor/data_analyst prompt guidance: when to use `longjob` vs `py_exec`; declare resources.

### Phase 4 — Verify + roll out
- [ ] 13. Curl-driven backend pass: a session dispatches a trivial long job (local backend), polls to done, fetches output.
- [ ] 14. Live FLAIR pass once a node + backend are available; document the event trace.
- [ ] 15. Docs: CLAUDE.md (new tool + trust boundary), ROADMAP flip.

## Files touched
- `tools/longjob/` (new): `job.py`, `server.py`, `backends/{base,local,flair_docker}.py`.
- `scaffold/tools_registry.py`, `scaffold/settings.py` (job paths, backend selection, caps), `roles/*.yaml`, `prompts/`, `tools/py_exec/server.py` (escalation result), `CLAUDE.md`, `ROADMAP.md`, `tests/`.

## Verification
```bash
PYTHONPATH=. python3 -m pytest tests/test_longjob.py -q          # core + local backend + HITL + cancel
PYTHONPATH=. python3 -m pytest tests/test_longjob.py::test_dispatch_guard -q   # Phase 2 negative test
```

## Risks / open questions
- **Dispatch security fork** (above) — the load-bearing decision; must not regress the non-root posture. Host-spooler is the safest default.
- **Async vs the SDK synchronous tool model** — durable handles + `wait`/reconcile must be clean or long jobs block a tool call or get orphaned (settle in Phase 1).
- **In-container local backend** competes with the API/agent for the node's CPU/mem — it's a CPU fallback/reference, not the target; heavy/GPU work goes to the docker backend.
- **Resource-burn** — an agent submitting freely can hog a shared node (BOLD Rule 0/3). HITL + hard caps are load-bearing.
- **Reconcile correctness** — never re-submit a still-running job after an SSH/host blip (idempotency token on the handle).

## Notes
- 2026-07-01: Split from R11. R11 = ARM64 HPC backend (blocked on recon); R12 = runner core + FLAIR docker backend (unblocked). Two clusters confirmed = flair-nodes + flair-works (one docker backend). Dispatch security fork recorded; host-spooler recommended. Building Phase 1 (backend-agnostic core) first — it's independent of the fork.
