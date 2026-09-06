"""The explicit, independently-maintainable declaration of R17's frozen surface.

This is the one place that names what any future version MUST keep able to read:
the durable on-disk schemas (Tier-3 data under ``state/``) and the physical
anchors. It exists so the boundary is a *checkable* artifact, not a promise in a
prompt — ``tests/test_contract_compat.py`` drives the real readers against frozen
golden fixtures, and that test rides the ``propose_merge`` smoke gate, so an
evolution that would break reading old data is auto-rejected before the human is
asked. See docs/plans/R17-evolution-archive.md ("Hard-constraint implementation").

Maintaining a new frozen item: (1) add it here, (2) drop a golden sample into the
fixture, (3) the compat test enforces it thereafter. Schema changes must be
ADDITIVE (new optional fields); a non-additive change must bump ``SCHEMA_VERSION``
and ship a migrator.
"""
from __future__ import annotations

# Bumped only on a non-additive change to any durable schema below. Additive
# changes (new optional fields / new appended lines / new event kinds) do NOT
# bump it — old readers ignore what they don't know.
SCHEMA_VERSION = 1

# Durable on-disk schemas: the keys a record of each kind is guaranteed to carry.
# A future version may ADD keys; it may not remove or rename these without a
# migrator + a SCHEMA_VERSION bump. Keyed by a human label; ``required`` is the
# frozen key set the compat test asserts on the golden fixtures.
DURABLE_SCHEMAS: dict[str, dict] = {
    "events.jsonl": {
        "path": "state/sessions/<sid>/events.jsonl",
        "kind": "jsonl",
        "required": ["id", "ts", "session", "actor", "kind"],
    },
    "sdk_session.json": {
        "path": "state/sessions/<sid>/sdk_session.json",
        "kind": "json",
        "required": ["sdk_session_id", "turns"],
    },
    "memory.agent": {
        "path": "state/sessions/<sid>/memory/agent/<agent>.jsonl",
        "kind": "jsonl",
        "required": ["id", "ts", "agent", "text", "tags"],
    },
    "memory.global": {
        "path": "state/memory/global.jsonl",
        "kind": "jsonl",
        "required": ["id", "ts", "source", "text", "tags"],
    },
    "hitl.answered": {
        "path": "state/sessions/<sid>/hitl/answered/<rid>.json",
        "kind": "json",
        "required": ["id", "ts", "kind", "decision"],
    },
    "archive.meta": {
        "path": "state/archive/evolutions/<node>/meta.json",
        "kind": "json",
        "required": ["id", "tag", "sha", "status", "schema_version"],
    },
}

# Physical anchors — you cannot migrate around these because you need them to
# *find* the data in the first place. Relocating any of these is forbidden.
ANCHORS: dict[str, str] = {
    "state_root": "state/",
    "session_dir": "state/sessions/<sid>/",
    "claude_config_dir": "state/.claude/",  # SDK resume transcript store
    "archive_dir": "state/archive/evolutions/",
}
