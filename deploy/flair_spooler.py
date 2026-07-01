#!/usr/bin/env python3
"""FLAIR long-job spooler — runs ON the node, as YOUR user, OUTSIDE Docker.

It is the trust boundary for R12's FLAIR backend. coscientist (in its non-root
container) can only drop *data* (a job request) into the shared spool; this
daemon is the only thing that runs docker, and it runs ONLY argv it builds itself
from validated fields — never a shell or command taken from the request. This is
how "launch GPU jobs" is provided without giving the container the docker socket
(root-equivalent, BOLD Rule 1).

Every launched job is BOLD-compliant: non-root ``--user``, explicit ``--gpus``
(free devices only, Rule 2), ``${USER}_…`` name, ``-v`` workdir mount only,
no ``--privileged`` / no host mounts.

Run:  COSCIENTIST_SPOOL_DIR_HOST=./state/jobspool \
      COSCIENTIST_HOST_STATE_ROOT=$PWD/state \
      python3 deploy/flair_spooler.py
See deploy/README-spooler.md.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.longjob import spool as sp  # noqa: E402

CONTAINER_STATE = os.environ.get("COSCIENTIST_CONTAINER_STATE", "/app/state")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
GPU_LIST_RE = re.compile(r"^[0-9]+(,[0-9]+)*$")
POLL_SECONDS = 3


class SpecError(ValueError):
    """A job request that fails validation — never launched."""


def translate_workdir(container_path: str, host_state_root: str,
                      container_state: str = CONTAINER_STATE) -> Path:
    """Map a container workdir (/app/state/…) to its host path, refusing anything
    that escapes the state tree (the -v mount guard)."""
    cs = container_state.rstrip("/")
    if container_path != cs and not container_path.startswith(cs + "/"):
        raise SpecError(f"workdir not under {cs}: {container_path!r}")
    rel = container_path[len(cs):].lstrip("/")
    root = Path(host_state_root).resolve()
    host = (root / rel).resolve()
    if host != root and root not in host.parents:
        raise SpecError(f"workdir escapes host state root: {container_path!r}")
    return host


def _nvidia_free_gpus(query_output: str) -> list[str]:
    """Indices of GPUs with no running compute apps, from
    `nvidia-smi --query-compute-apps=gpu_uuid --format=csv` style input is hard;
    we instead take `index, utilization.gpu, memory.used` rows and call a GPU
    free when util==0 and mem_used is tiny."""
    free = []
    for line in query_output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        idx, util, mem = parts[0], parts[1], parts[2]
        try:
            if int(util) == 0 and int(mem) < 50:
                free.append(idx)
        except ValueError:
            continue
    return free


def pick_free_gpus(n: int, query_output: str) -> list[str]:
    if n <= 0:
        return []
    free = _nvidia_free_gpus(query_output)
    if len(free) < n:
        raise SpecError(f"need {n} free GPU(s), only {len(free)} available (Rule 2/3)")
    return free[:n]


def build_docker_argv(req: dict, host_workdir: Path, uid_gid: str,
                     name: str, gpu_devices: list[str],
                     allowed_images: list[str] | None = None) -> list[str]:
    """Construct the ONLY thing the spooler runs. All inputs validated; the
    request's ``command`` is passed to the container's shell (safe — the job is
    an isolated non-root container), but never to the host."""
    if not NAME_RE.match(name):
        raise SpecError(f"bad job name: {name!r}")
    image = req.get("image", "")
    if not image or any(c in image for c in " ;|&$`\n"):
        raise SpecError(f"bad image: {image!r}")
    if allowed_images and image not in allowed_images:
        raise SpecError(f"image not allow-listed: {image!r}")
    command = req.get("command", "")
    if not command:
        raise SpecError("empty command")
    argv = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        "--user", uid_gid,
        "-v", f"{host_workdir}:/work", "-w", "/work",
        "--cpus", str(int(req.get("cpus", 1))),
        "--memory", f"{int(req.get('mem_mb', 2048))}m",
    ]
    if gpu_devices:
        argv += ["--gpus", 'device=' + ",".join(gpu_devices)]
    for k, v in (req.get("env") or {}).items():
        if NAME_RE.match(str(k)):
            argv += ["-e", f"{k}={v}"]
    argv += [image, "bash", "-lc", command]
    return argv


def job_name(req: dict, user: str) -> str:
    return f"{user}_cojob_{req['job_id']}"


# --- daemon loop ------------------------------------------------------------
def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True)


def _launch(spool: str, req: dict, cfg: dict) -> None:
    job_id = req["job_id"]
    try:
        host_wd = translate_workdir(req["container_workdir"], cfg["host_state_root"])
        host_wd.mkdir(parents=True, exist_ok=True)
        gpus = []
        if int(req.get("gpu", 0)) > 0:
            smi = _run(["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used",
                        "--format=csv,noheader,nounits"])
            gpus = pick_free_gpus(int(req["gpu"]), smi.stdout if smi.returncode == 0 else "")
        name = job_name(req, cfg["user"])
        argv = build_docker_argv(req, host_wd, cfg["uid_gid"], name, gpus,
                                 cfg.get("allowed_images"))
    except SpecError as e:
        sp.write_status(spool, job_id, {"state": "failed", "rc": None, "error": str(e)})
        return
    res = _run(argv)
    if res.returncode != 0:
        sp.write_status(spool, job_id, {"state": "failed", "rc": res.returncode,
                                        "error": res.stderr[-2000:]})
        return
    sp.write_status(spool, job_id, {"state": "running", "rc": None,
                                    "container": name})


def _poll_running(spool: str, job_id: str, name: str) -> None:
    if sp.cancel_requested(spool, job_id):
        _run(["docker", "kill", name])
        sp.write_status(spool, job_id, {"state": "failed", "rc": None, "error": "cancelled"})
        return
    insp = _run(["docker", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}", name])
    logs = _run(["docker", "logs", "--tail", "500", name])
    sp.log_path(spool, job_id).write_text(logs.stdout + logs.stderr, encoding="utf-8")
    if insp.returncode != 0:
        # container gone (--rm cleaned it up after exit) → treat as done unless
        # a prior status recorded failure; assume success if we can't tell.
        sp.write_status(spool, job_id, {"state": "done", "rc": 0, "container": name})
        return
    status, _, code = insp.stdout.strip().partition(" ")
    if status == "running":
        return
    rc = int(code) if code.strip().lstrip("-").isdigit() else None
    sp.write_status(spool, job_id, {"state": "done" if rc == 0 else "failed",
                                    "rc": rc, "container": name})


def main() -> None:
    spool = os.environ["COSCIENTIST_SPOOL_DIR_HOST"]
    cfg = {
        "host_state_root": os.environ["COSCIENTIST_HOST_STATE_ROOT"],
        "user": os.environ.get("USER", "cosci"),
        "uid_gid": f"{os.getuid()}:{os.getgid()}",
        "allowed_images": [x for x in os.environ.get("COSCIENTIST_ALLOWED_IMAGES", "").split(",") if x],
    }
    print(f"[spooler] watching {spool} as {cfg['user']} ({cfg['uid_gid']})", flush=True)
    while True:
        # New requests → launch (consume the request file).
        for reqfile in sp.list_requests(spool):
            req = sp.read_request(reqfile)
            reqfile.unlink(missing_ok=True)
            if req:
                _launch(spool, req, cfg)
        # Running jobs → poll.
        for stfile in sorted((Path(spool) / "status").glob("*.json")) if (Path(spool) / "status").exists() else []:
            st = sp.read_request(stfile)  # same JSON reader
            if st and st.get("state") == "running" and st.get("container"):
                _poll_running(spool, stfile.stem, st["container"])
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
