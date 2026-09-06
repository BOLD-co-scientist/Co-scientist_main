"""Evolution archive: the durable, queryable view of a tenant's version DAG.

Git is the source of truth for *code* (each merged evolution is a commit made
switchable by an immutable ``ver/<id>`` tag). This module maintains a durable
manifest *on top of* git — per-node ``meta.json`` files plus a top-level
``index.json`` under ``state/archive/evolutions/`` — that carries the metadata
git doesn't (summary, rationale, owner, schema_version, smoke result, status)
and, because it stores each node's ``diff.patch``, makes a node portable to
another tenant's repo by applying the patch (the cross-user seed; not built yet).

Design note (R17): the manifest lives under gitignored ``state/`` so it survives
a version switch (a ``git checkout`` of the code), and it must stay
*forward-readable* — a newer version must be able to read an older archive, since
the archive is the map used to navigate/switch versions. Keep changes additive.
See docs/plans/R17-evolution-archive.md.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from . import settings
from ._atomic import read_json, write_json

# Bumped only when the on-disk manifest shape changes. Additive changes (new
# optional fields) do NOT bump it; a non-additive change must ship a migrator.
SCHEMA_VERSION = 1

_TAG_PREFIX = "ver/"


class ArchiveError(RuntimeError):
    pass


def _git(*args: str, repo: Path) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(repo), stderr=subprocess.STDOUT, text=True
    )


def _archive_root(repo: Path) -> Path:
    """The archive dir for ``repo``. Defaults to ``settings.EVOLUTIONS_ARCHIVE``
    when ``repo`` is the process root; for a tenant repo it is that tenant's
    ``state/archive/evolutions``."""
    return repo / "state" / "archive" / "evolutions"


def _index_path(repo: Path) -> Path:
    return _archive_root(repo) / "index.json"


def version_id_from_archive(archive_dir_name: str) -> str:
    """The version id is the archive folder name (``<ts>__<slug>``) — already
    unique and human-readable. The git tag is ``ver/<id>``."""
    return archive_dir_name


def owner_of(repo: Path) -> str:
    """Identify the owner of a tenant repo from its path. A tenant root is
    ``state/users/<uid>/root``; anything else falls back to the dir name."""
    p = repo.resolve()
    if p.name == "root" and p.parent.parent.name == "users":
        return p.parent.name
    return p.name


def record_merged_version(
    repo: Path,
    *,
    archive_dir: Path,
    head_sha: str,
    base_sha: str,
    summary: str,
    rationale: str,
    owner: str,
    smoke: dict[str, Any] | None = None,
    origin_session: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote a just-merged evolution to a first-class version node.

    Tags ``head_sha`` as ``ver/<id>`` (immutable identity that survives, unlike
    the deleted evo branch), writes the node's ``meta.json`` into ``archive_dir``,
    and appends it to ``index.json``. Idempotent on the tag (re-tagging the same
    sha is a no-op). Returns the node dict.

    ``origin_session`` is the evolution session that produced this version — its
    provenance / "birth story". Stored so the UI can open that conversation from
    the node (R17). The events themselves live durably under ``state/`` and so
    survive version switches; this is just the link.
    """
    vid = version_id_from_archive(archive_dir.name)
    tag = _TAG_PREFIX + vid
    # Tag the merged commit. `-f` so a re-run is idempotent; annotated so the
    # tag carries the summary. Never fatal — a tag failure must not lose the
    # merge, which already landed.
    try:
        _git("tag", "-f", "-a", tag, head_sha, "-m", summary or vid, repo=repo)
    except subprocess.CalledProcessError:
        pass

    node = {
        "id": vid,
        "tag": tag,
        "sha": head_sha,
        "base_sha": base_sha,
        "summary": summary,
        "rationale": rationale,
        "owner": owner,
        "schema_version": SCHEMA_VERSION,
        "smoke": smoke or {"ran": False},
        "status": "merged",
        "origin_session": origin_session,
        "created_at": time.time(),
        "archive_dir": archive_dir.name,
        "diff": "diff.patch",
    }
    # Additive, optional metadata (R19: ``sibling`` for a child recorded beside
    # an already-merged sibling, ``proposal_id`` for planner provenance).
    for k, v in (extra or {}).items():
        node.setdefault(k, v)
    write_json(archive_dir / "meta.json", node)
    _append_to_index(repo, node)
    return node


def _append_to_index(repo: Path, node: dict[str, Any]) -> None:
    idx = read_json(_index_path(repo), default=None)
    if not isinstance(idx, dict) or "versions" not in idx:
        idx = {"schema_version": SCHEMA_VERSION, "versions": []}
    versions = idx["versions"]
    # Replace an existing entry with the same id (re-merge), else append.
    versions = [v for v in versions if v.get("id") != node["id"]]
    versions.append(
        {k: node[k] for k in ("id", "tag", "sha", "base_sha", "summary", "owner", "status", "origin_session", "created_at", "archive_dir")}
    )
    idx["versions"] = versions
    idx["schema_version"] = SCHEMA_VERSION
    write_json(_index_path(repo), idx)


def _current_sha(repo: Path) -> str | None:
    try:
        return _git("rev-parse", "HEAD", repo=repo).strip()
    except subprocess.CalledProcessError:
        return None


def _ver_tags(repo: Path) -> list[dict[str, str]]:
    """All ``ver/*`` tags with the sha they point at, from git (the truth)."""
    try:
        raw = _git(
            "for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/tags/" + _TAG_PREFIX + "*",
            repo=repo,
        )
    except subprocess.CalledProcessError:
        return []
    out: list[dict[str, str]] = []
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        ref, sha = line.split("\t", 1)
        # Annotated tags: resolve to the commit they wrap.
        try:
            commit = _git("rev-list", "-n", "1", ref, repo=repo).strip()
        except subprocess.CalledProcessError:
            commit = sha.strip()
        out.append({"tag": ref.strip(), "id": ref.strip()[len(_TAG_PREFIX):], "sha": commit})
    return out


def activate_version(repo: Path, ref: str) -> str:
    """Switch the tenant's working tree to version ``ref`` (a ``ver/<id>`` id, or
    a bare commit sha such as the bootstrap root) via a **detached** checkout —
    the tag is the source of truth, so we never move a branch pointer. Returns
    the resolved commit sha.

    IMPORTANT: the caller MUST ensure no research/evolution turn is running and
    must block new spawns for the duration — this swaps the agent-layer code that
    per-turn subprocesses read. This function only does the git checkout; the
    idle-guard + spawn-lock live in the API layer (see docs R17: switch mechanics).
    """
    target = None
    tag = _TAG_PREFIX + ref
    try:
        target = _git("rev-list", "-n", "1", tag, repo=repo).strip()
    except subprocess.CalledProcessError:
        target = None
    if not target:
        # Not a ver tag — accept a raw commit-ish (e.g. the bootstrap root sha).
        try:
            target = _git("rev-parse", "--verify", f"{ref}^{{commit}}", repo=repo).strip()
        except subprocess.CalledProcessError:
            raise ArchiveError(f"unknown version or commit: {ref!r}")
    try:
        _git("checkout", "-q", "--detach", target, repo=repo)
    except subprocess.CalledProcessError as e:
        raise ArchiveError(f"checkout failed (working tree not clean?):\n{e.output}")
    return target


def list_versions(repo: Path) -> dict[str, Any]:
    """The version DAG for ``repo``: every ``ver/*`` tag joined with its manifest
    metadata, plus which node is currently active (HEAD). Read-only; safe on
    every UI refresh. ``active`` is the id whose sha == HEAD, or None (detached
    at an untagged commit / no versions yet)."""
    if not (repo / ".git").exists():
        return {"versions": [], "active": None, "schema_version": SCHEMA_VERSION}
    arch = _archive_root(repo)
    tags = _ver_tags(repo)
    head = _current_sha(repo)
    versions: list[dict[str, Any]] = []
    for t in tags:
        meta = read_json(arch / t["id"] / "meta.json", default=None)
        node = dict(meta) if isinstance(meta, dict) else {}
        node.update({"id": t["id"], "tag": t["tag"], "sha": t["sha"]})
        node["active"] = head is not None and t["sha"] == head
        versions.append(node)
    versions.sort(key=lambda v: v.get("created_at") or 0)
    active = next((v["id"] for v in versions if v.get("active")), None)
    return {"versions": versions, "active": active, "head": head, "schema_version": SCHEMA_VERSION}
