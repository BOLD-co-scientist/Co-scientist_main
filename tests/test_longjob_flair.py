"""R12 Phase 2 — FLAIR docker backend (spool protocol) + spooler guard.
No real docker: tests the container-side request/status protocol and the
host-side spooler's pure functions (path translation, GPU pick, argv build)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy import flair_spooler as fs  # noqa: E402


# --- spooler: path translation guard ----------------------------------------
def test_translate_workdir_ok():
    host = fs.translate_workdir("/app/state/users/u1/root/state/sessions/s/jobs/j/work",
                               "/data/coscientist/state")
    assert str(host).startswith("/data/coscientist/state/users/u1")


def test_translate_workdir_escape_rejected():
    with pytest.raises(fs.SpecError):
        fs.translate_workdir("/etc/passwd", "/data/coscientist/state")
    with pytest.raises(fs.SpecError):
        # traversal that climbs out of the state root
        fs.translate_workdir("/app/state/../../etc", "/data/coscientist/state")


# --- spooler: GPU selection (Rule 2/3) --------------------------------------
def test_pick_free_gpus():
    smi = "0, 0, 3\n1, 87, 40201\n2, 0, 12\n3, 0, 5"
    assert fs.pick_free_gpus(0, smi) == []
    assert fs.pick_free_gpus(2, smi) == ["0", "2"]  # 1 is busy, skipped


def test_pick_free_gpus_insufficient():
    smi = "0, 99, 8000\n1, 50, 4000"
    with pytest.raises(fs.SpecError):
        fs.pick_free_gpus(1, smi)


# --- spooler: docker argv is BOLD-compliant ---------------------------------
def _req(**kw):
    base = {"job_id": "j1", "image": "alice_cojob:dev", "command": "python train.py",
            "cpus": 4, "mem_mb": 8192, "env": {}}
    base.update(kw)
    return base


def test_build_argv_bold_compliant():
    argv = fs.build_docker_argv(_req(), Path("/data/state/j/work"), "1001:1001",
                               "alice_cojob_j1", ["0", "1"])
    s = " ".join(argv)
    assert argv[:2] == ["docker", "run"]
    assert "--user" in argv and "1001:1001" in argv                    # non-root (Rule 1)
    assert "--gpus" in argv and "device=0,1" in s                       # explicit GPUs (Rule 2)
    assert "--name" in argv and "alice_cojob_j1" in argv                # attribution
    assert "-v" in argv and "/data/state/j/work:/work" in s            # only the workdir mount
    assert "--privileged" not in argv and ":/host" not in s            # no host escape
    assert argv[-3:] == ["bash", "-lc", "python train.py"]             # command to the container shell


def test_build_argv_cpu_job_has_no_gpus():
    argv = fs.build_docker_argv(_req(), Path("/w"), "1:1", "u_cojob_j1", [])
    assert "--gpus" not in argv


def test_build_argv_rejects_bad_inputs():
    with pytest.raises(fs.SpecError):
        fs.build_docker_argv(_req(), Path("/w"), "1:1", "bad name!", ["0"])       # bad name
    with pytest.raises(fs.SpecError):
        fs.build_docker_argv(_req(image="evil; rm -rf /"), Path("/w"), "1:1", "u_j", [])  # bad image
    with pytest.raises(fs.SpecError):
        fs.build_docker_argv(_req(command=""), Path("/w"), "1:1", "u_j", [])      # empty command


def test_build_argv_image_allowlist():
    with pytest.raises(fs.SpecError):
        fs.build_docker_argv(_req(image="random/image:latest"), Path("/w"), "1:1",
                            "u_j", [], allowed_images=["alice_cojob:dev"])


# --- container-side backend: request/status/cancel round-trip ---------------
def test_flair_backend_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    monkeypatch.setenv("COSCIENTIST_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("COSCIENTIST_JOB_IMAGE", "alice_cojob:dev")
    import importlib
    from scaffold import settings as s
    importlib.reload(s)
    from tools.longjob.backends.flair_docker import FlairDockerBackend
    from tools.longjob import spool as sp, job as j

    sid = "flair"
    s.ensure_session_dirs(sid)
    spec = j.JobSpec(command="python x.py", backend="flair-docker", gpu=2, cpu=4)
    handle = j.make_handle(sid, spec)
    be = FlairDockerBackend()
    ref = be.submit(spec, handle["workdir"])
    # request written for the spooler
    reqs = sp.list_requests(ref["spool"])
    assert len(reqs) == 1
    req = sp.read_request(reqs[0])
    assert req["gpu"] == 2 and req["image"] == "alice_cojob:dev"
    assert req["container_workdir"] == handle["workdir"]
    # no status yet → queued
    assert be.poll(ref, handle["workdir"]) == ("queued", None)
    # spooler writes running, then done
    sp.write_status(ref["spool"], ref["job_id"], {"state": "running", "rc": None})
    assert be.poll(ref, handle["workdir"])[0] == "running"
    sp.write_status(ref["spool"], ref["job_id"], {"state": "done", "rc": 0})
    assert be.poll(ref, handle["workdir"]) == ("done", 0)
    # cancel writes a flag the spooler reads
    be.cancel(ref, handle["workdir"])
    assert sp.cancel_requested(ref["spool"], ref["job_id"])


def test_flair_backend_requires_image(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    monkeypatch.setenv("COSCIENTIST_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.delenv("COSCIENTIST_JOB_IMAGE", raising=False)
    import importlib
    from scaffold import settings as s
    importlib.reload(s)
    from tools.longjob.backends.flair_docker import FlairDockerBackend
    from tools.longjob import job as j
    sid = "noimg"
    s.ensure_session_dirs(sid)
    spec = j.JobSpec(command="x", backend="flair-docker")  # no image
    h = j.make_handle(sid, spec)
    with pytest.raises(ValueError):
        FlairDockerBackend().submit(spec, h["workdir"])
