# BOLD / FLAIR server operating rules — MANDATORY

**Source:** *BOLD Server Guide* (the FLAIR/BOLD server managers' living document).
This file is the in-repo canonical capture of the rules coscientist and any agent
or developer working on it **must** follow when anything runs on BOLD/FLAIR
hardware. Violations are a **cybersecurity and operational risk** and can get our
jobs — and our account — reaped by the server admins.

**Current deployment:** the service currently runs on **flair-node-05 (A100)**. A
move to the **flair-works** machines is under consideration — these rules apply
identically wherever it runs, so no rule below changes with the move.

> If this repo is ever deployed or run on a FLAIR node, every rule below applies.
> When in doubt, follow the source guide.

## The rules (verbatim intent)

### Rule 0 — Server tiering (use the right machine)
No formal assignment except near deadlines. Match the job to the tier:
- **flair-works-01..04** — debugging / small-scale experiments.
- **flair-node-01..04 (A40, 48 GB)** — medium experiments or high memory usage.
- **flair-node-05 (A100, 80 GB)** — large, memory-intensive experiments.
- **flair-node-06..12 (L40S, 48 GB)** — quick, large-scale experiments.
- **flair-head-00** — CPU-only login/head node. **Do not run jobs here.**
Be reasonable; give others room if you're running far more than everyone else.

### Rule 1 — NEVER run Docker as root  ← the critical one
- Running containers as root makes it **impossible to attribute jobs**, makes them
  **hard to stop**, and carries **security implications**.
- Every image **must** run as a non-root user matching the host user. The Dockerfile
  must contain the build-arg + user block, and end on a non-root `USER`:
  ```dockerfile
  ARG UID
  ARG GID
  RUN groupadd -o -g ${GID} myuser && useradd -u ${UID} -g ${GID} -m myuser
  RUN chown -R myuser:myuser /app
  USER myuser
  ```
- Build with the **real** host identity so ownership is correct on the shared
  filesystem:
  ```bash
  docker build --build-arg UID=$(id -u) --build-arg GID=$(id -g) -t ${USER}_coscientist .
  ```
- **Never store data inside a container.** Mount host directories with `-v`
  (`-v $(pwd):/app`, `-v .../state:/app/state`, …). Stopped containers and untagged
  images may be removed at any time — anything not on a mounted volume is lost.
- **Verify** before trusting a container: `docker exec <name> id` must **not** be
  `uid=0(root)`.

### Rule 2 — Never share a GPU
Only ever expose the GPUs you intend to use, explicitly:
```bash
docker run --gpus '"device=0"' ...        # single GPU
docker run --gpus '"device=2,3,5"' ...    # specific set; container sees them as 0,1,2
```
Never launch on a GPU someone else is using.

### Rule 3 — Don't kill other people's jobs
If you need capacity and there's none, ask in **`#compute`** on Slack. Do not stop
others' jobs (except assigned-GPU reclamation near deadlines, per the source guide).

### Rule 4 — No misuse
No crypto-mining or other abuse of the servers.

## Attribution & naming (required by Rule 1's intent)
- **Images:** `${USER}_<name>` (e.g. `${USER}_coscientist:dev`). Tag versions.
- **Containers:** `--name ${USER}_<name>` so managers can see who owns each one.
- Prefer `--rm` for ephemeral runs; `-d` + `docker logs` (or `-u` unbuffered piping
  to a mounted log file) for long detached jobs.

## How coscientist must comply
- **Dockerfile** keeps the `ARG UID/GID` + `useradd` + `USER myuser` block and ends
  non-root. **Never** remove `USER myuser` or add a later `USER root`.
- **docker-compose / run** must pass real `UID=$(id -u)` / `GID=$(id -g)` at build,
  use `${USER}_…` image + container names, and mount `state/` (and other data) via
  `-v`. Never bake researcher/state data into the image.
- **py_exec and every runtime subprocess** inherit the container user — so the
  container being non-root is what keeps executed research code non-root too.
- The evolution agent may edit `Dockerfile`/`docker-compose.yml`, but **must never**
  reintroduce root or remove the non-root user (enforced by the smoke gate; see
  `tests/test_smoke_v0.py::test_dockerfile_runs_nonroot`).
- If a long-running / GPU job runner is added, it must pass explicit `--gpus`
  (Rule 2) and run under the non-root user (Rule 1).
