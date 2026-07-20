#!/usr/bin/env python3
"""Sandbox doctor — answers ONE question for THIS host: does coscientist's tool
execution run in an isolated context that strips third-party Python packages
(and /usr/bin) from tools?

Why it matters: on Mac Docker Desktop we found MCP tools + py_exec run in a
sandbox where installed packages (pandas/numpy/rapidocr) and system binaries are
invisible — even though the container has them. That blocks any tool needing a
real dependency, including OCR, and it silently breaks py_exec's pandas/numpy.
It MAY be specific to unprivileged Docker on Mac; this checks the real host
(e.g. FLAIR).

It reuses the EXISTING v0 `py_exec` tool (no new code deployed): it drives a real
research session that runs an environment probe via py_exec and reads the result
straight out of the session event log. That reproduces the exact tool-execution
sandbox.

RUN IT inside the coscientist container on the target host (the API must be up):

    docker compose cp deploy/sandbox_doctor.py coscientist-api:/tmp/doctor.py
    docker compose exec coscientist-api python3 /tmp/doctor.py

Env knobs: COSCIENTIST_URL (default http://localhost:8765).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("COSCIENTIST_URL", "http://localhost:8765").rstrip("/")

# The probe. Distinctive markers so we can grep them out of the agent's report.
PROBE = (
    "import sys, os\n"
    "print('DOC_EXE', sys.executable)\n"
    "print('DOC_UID', os.getuid())\n"
    "print('DOC_USRBIN', len(os.listdir('/usr/bin')) if os.path.isdir('/usr/bin') else 'NA')\n"
    "try:\n"
    "    import numpy; print('DOC_NUMPY', 'OK', numpy.__version__)\n"
    "except Exception as e:\n"
    "    print('DOC_NUMPY', 'FAIL', type(e).__name__)\n"
)

TASK = (
    "Call the tool mcp__py_exec__run exactly once with the following code, then "
    "report its FULL stdout and stderr VERBATIM in your reply (do not summarise, "
    "do not fix anything, include every DOC_ line):\n\n" + PROBE
)


def _api(method: str, path: str, key: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> int:
    print(f"Sandbox doctor → {BASE}")
    # 1. Provision a throwaway user via the CLI (writes to the shared state mount).
    env = {**os.environ, "PYTHONPATH": os.environ.get("PYTHONPATH", "/app")}
    cp = subprocess.run(
        [sys.executable, "-m", "coscientist_cli.main", "users", "create", "sandbox_doctor"],
        capture_output=True, text=True, env=env,
    )
    m = re.search(r"csk_[A-Za-z0-9_-]+", cp.stdout)  # token_urlsafe may contain '-'
    if not m:
        print("ERROR: could not create a user via the CLI.\n", cp.stdout, cp.stderr)
        return 2
    key = m.group(0)
    print("provisioned throwaway user OK")

    # 2. Kick off a probe research session.
    try:
        sid = _api("POST", "/research/sessions", key, {"task": TASK})["session_id"]
    except Exception as e:
        print(f"ERROR: could not start a session ({e}). Is the API up at {BASE}?")
        return 2
    print(f"session {sid} started — waiting for the py_exec probe to run...")

    # 3. Poll the event log for the probe markers / completion.
    report = ""
    for i in range(40):
        try:
            evs = _api("GET", f"/sessions/{sid}/events?limit=200", key)
        except Exception:
            time.sleep(6)
            continue
        evs = evs if isinstance(evs, list) else evs.get("events", [])
        text = json.dumps(evs)
        done = any(e.get("kind") in ("session.idle", "research.complete") for e in evs)
        if "DOC_NUMPY" in text or done:
            for e in evs:
                blob = json.dumps(e.get("payload", ""))
                if "DOC_NUMPY" in blob or "DOC_UID" in blob:
                    report = blob
            if "DOC_NUMPY" in text or done:
                break
        time.sleep(6)

    print("\n" + "=" * 60)
    print("PROBE RESULT (from py_exec, inside the tool sandbox)")
    print("=" * 60)
    if not report:
        print("Could not capture the probe output from the event log.")
        print("Re-run, or inspect the session's events.jsonl manually.")
        return 3

    def grab(tag):
        mm = re.search(tag + r"[^\"\\]*", report)
        return mm.group(0) if mm else f"{tag} (not found)"

    numpy_line = grab("DOC_NUMPY")
    print(" ", grab("DOC_EXE"))
    print(" ", grab("DOC_UID"))
    print(" ", grab("DOC_USRBIN"))
    print(" ", numpy_line)

    print("\n" + "=" * 60)
    print("VERDICT")
    print("=" * 60)
    if "DOC_NUMPY OK" in report:
        print("NO STRIPPING on this host — py_exec sees the real environment.")
        print("=> In-runtime OCR (RapidOCR) and py_exec deps WILL work here.")
        return 0
    if "DOC_NUMPY FAIL" in report:
        print("SANDBOX STRIPS THE ENVIRONMENT on this host (numpy import failed).")
        print("=> In-runtime OCR / py_exec deps do NOT work; route OCR via the")
        print("   longjob spooler (host-side, real environment).")
        return 0
    print("Inconclusive — the probe ran but numpy status was not captured.")
    print("Raw:", report[:500])
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
