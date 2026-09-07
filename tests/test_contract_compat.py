"""R17 backward-compatibility gate.

Drives the frozen golden fixture (``fixtures/golden_v1.json`` — durable records as
written by SCHEMA_VERSION=1) against the current code + the ``scaffold.contract``
declaration. If a future evolution removes/renames a durable field, or changes a
reader so it can no longer parse the old shape, this fails — and because it rides
the ``propose_merge`` smoke gate, that evolution is auto-rejected before the human
is asked. See docs/plans/R17-evolution-archive.md.

Do NOT relax an assertion to make a change pass: that is exactly the regression
the gate exists to catch. Evolve schemas ADDITIVELY, or bump SCHEMA_VERSION and
ship a migrator.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scaffold import archive, contract

FIXTURE = Path(__file__).parent / "fixtures" / "golden_v1.json"


def _golden() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _has(rec: dict, required: list[str]) -> list[str]:
    return [k for k in required if k not in rec]


def test_golden_records_still_satisfy_frozen_schemas():
    """Every frozen record must still carry the contract's required keys."""
    g = _golden()
    S = contract.DURABLE_SCHEMAS
    problems: list[str] = []
    for e in g["events"]:
        miss = _has(e, S["events.jsonl"]["required"])
        if miss:
            problems.append(f"events {e.get('kind')}: missing {miss}")
    if _has(g["sdk_session"], S["sdk_session.json"]["required"]):
        problems.append(f"sdk_session: missing {_has(g['sdk_session'], S['sdk_session.json']['required'])}")
    for m in g["memory_agent"]:
        if _has(m, S["memory.agent"]["required"]):
            problems.append(f"memory.agent: missing {_has(m, S['memory.agent']['required'])}")
    for m in g["memory_global"]:
        if _has(m, S["memory.global"]["required"]):
            problems.append(f"memory.global: missing {_has(m, S['memory.global']['required'])}")
    if _has(g["hitl_answered"], S["hitl.answered"]["required"]):
        problems.append(f"hitl.answered: missing {_has(g['hitl_answered'], S['hitl.answered']['required'])}")
    if _has(g["archive_meta"], S["archive.meta"]["required"]):
        problems.append(f"archive.meta: missing {_has(g['archive_meta'], S['archive.meta']['required'])}")
    assert not problems, "backward-compat break — old data no longer matches the frozen contract:\n" + "\n".join(problems)


def test_real_archive_reader_parses_frozen_meta(tmp_path):
    """The REAL archive reader must still interpret a SCHEMA_VERSION=1 node."""
    g = _golden()
    root = tmp_path
    # A minimal tenant git repo so list_versions' ver/* tag lookup works.
    def git(*a):
        subprocess.check_output(["git", *a], cwd=str(root), stderr=subprocess.STDOUT)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / ".gitignore").write_text("/state/\n", encoding="utf-8")
    (root / "f.txt").write_text("seed", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")
    vid = g["archive_meta"]["id"]
    git("tag", "ver/" + vid)
    node_dir = root / "state" / "archive" / "evolutions" / vid
    node_dir.mkdir(parents=True)
    (node_dir / "meta.json").write_text(json.dumps(g["archive_meta"]), encoding="utf-8")
    (root / "state" / "archive" / "evolutions" / "index.json").write_text(
        json.dumps(g["archive_index"]), encoding="utf-8"
    )

    lv = archive.list_versions(root)
    ids = [v["id"] for v in lv["versions"]]
    assert vid in ids, f"archive reader lost the frozen version node: {ids}"
    node = next(v for v in lv["versions"] if v["id"] == vid)
    assert node.get("summary") == "seed version"
    assert node.get("status") == "merged"


def test_schema_version_is_monotonic_with_fixture():
    """The fixture was written at v1; the code's SCHEMA_VERSION must be >= it (a
    drop would mean the code forgot how to read data it already emitted)."""
    assert contract.SCHEMA_VERSION >= _golden()["schema_version"]
