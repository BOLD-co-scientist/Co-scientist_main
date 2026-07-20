"""Git worktree wrapper. The evolution agent's edits live inside one of these
until the human approves the merge."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import NamedTuple

from . import settings


class Worktree(NamedTuple):
    path: Path
    branch: str
    base: str  # commit sha at creation


class GitError(RuntimeError):
    pass


def _git(*args: str, cwd: Path | None = None) -> str:
    cwd_arg = str(cwd) if cwd else str(settings.ROOT)
    try:
        out = subprocess.check_output(
            ["git", *args], cwd=cwd_arg, stderr=subprocess.STDOUT, text=True
        )
        return out
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {' '.join(args)} failed:\n{e.output}") from e


def ensure_repo() -> None:
    if not (settings.ROOT / ".git").exists():
        _git("init")
        _git("config", "user.email", "evolution@coscientist.local")
        _git("config", "user.name", "coscientist-evolution")
        # Initial commit so worktrees have a base.
        _git("add", "-A")
        try:
            _git("commit", "-m", "v0 bootstrap")
        except GitError:
            # may already have a commit
            pass


def create(slug: str, base: str | None = None) -> Worktree:
    """Create an ``evo/*`` worktree branched off ``base`` (a commit sha or ref),
    or off HEAD when ``base`` is None. Passing a base lets the human pick which
    node in the evolution graph a new feature descends from."""
    ensure_repo()
    settings.WORKTREES.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    name = f"{ts}__{slug}"
    branch = f"evo/{name}"
    path = settings.WORKTREES / name
    base_sha = _git("rev-parse", (base or "HEAD")).strip()
    _git("worktree", "add", "-b", branch, str(path), base_sha)
    return Worktree(path=path, branch=branch, base=base_sha)


def mark_start(wt: Worktree, message: str) -> str:
    """Put an empty marker commit on the fresh evo branch so it *diverges from
    its base immediately* and shows up as an in-flight child node in the
    evolution graph — even before the agent commits any real edits."""
    _git("commit", "--allow-empty", "-m", message, cwd=wt.path)
    return _git("rev-parse", "HEAD", cwd=wt.path).strip()


def diff(wt: Worktree) -> str:
    return _git("diff", f"{wt.base}..HEAD", cwd=wt.path)


def commit_all(wt: Worktree, message: str) -> str | None:
    _git("add", "-A", cwd=wt.path)
    status = _git("status", "--porcelain", cwd=wt.path)
    if not status.strip():
        return None
    _git("commit", "-m", message, cwd=wt.path)
    return _git("rev-parse", "HEAD", cwd=wt.path).strip()


def merge_to_main(wt: Worktree) -> str:
    """Fast-forward main to the worktree's HEAD."""
    head = _git("rev-parse", "HEAD", cwd=wt.path).strip()
    # Apply via a non-FF-but-safe merge: we cherry-pick from the branch.
    _git("merge", "--ff-only", wt.branch)
    return head


def discard(wt: Worktree) -> None:
    try:
        _git("worktree", "remove", "--force", str(wt.path))
    except GitError:
        pass
    try:
        _git("branch", "-D", wt.branch)
    except GitError:
        pass


def remove_after_merge(wt: Worktree) -> None:
    discard(wt)


_LOG_FMT = "%H%x1f%P%x1f%an%x1f%at%x1f%D%x1f%s"
_FIELD_SEP = "\x1f"


def history(limit: int = 200, repo: Path | None = None) -> dict:
    """Read-only snapshot of the branch graph across *all* refs of ``repo``.

    ``repo`` defaults to ``settings.ROOT``, but callers should pass a specific
    repo path to scope the graph — e.g. the API passes the authenticated user's
    own harness root so a researcher sees only *their* evolution lineage (their
    ``evo/*`` branches and merges into their own ``main``), never the shared
    development repo or another tenant's history.

    Returns ``{"commits": [...], "head": <sha|None>}`` where each commit is a
    dict ``{sha, parents, author, ts, refs, subject}``. Parents drive the graph
    edges; ``refs`` carries decoration names (main, evo/* branch tips, tags) so
    the UI can colour merged history apart from in-flight evolution branches.
    Never mutates the repo — safe to call on every UI refresh.
    """
    if repo is None:
        ensure_repo()
        repo = settings.ROOT
    elif not (repo / ".git").exists():
        # A freshly created tenant root that has not been git-init'd yet has no
        # history to show; report an empty graph rather than erroring.
        return {"commits": [], "head": None}
    raw = _git(
        "log",
        "--all",
        f"--max-count={int(limit)}",
        f"--pretty=format:{_LOG_FMT}",
        cwd=repo,
    )
    commits: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split(_FIELD_SEP)
        if len(parts) < 6:
            continue
        sha, parents, author, ts, decor, subject = parts[:6]
        refs = [
            r.strip().removeprefix("HEAD -> ").removeprefix("tag: ").strip()
            for r in decor.split(",")
            if r.strip()
        ]
        commits.append(
            {
                "sha": sha,
                "parents": parents.split() if parents.strip() else [],
                "author": author,
                "ts": float(ts) if ts.strip() else 0.0,
                "refs": [r for r in refs if r],
                "subject": subject,
            }
        )
    try:
        head = _git("rev-parse", "HEAD", cwd=repo).strip()
    except GitError:
        head = None
    return {"commits": commits, "head": head}


def commit_detail(sha: str, repo: Path | None = None) -> dict:
    """What's *in* a commit: metadata plus the files it changed (with per-file
    added/removed line counts). Powers the clickable node detail in the
    evolution graph. ``repo`` defaults to ``settings.ROOT``; the API passes the
    authenticated user's own harness root. Read-only."""
    repo = repo or settings.ROOT
    meta = _git("show", "--no-patch", f"--format={_LOG_FMT}", sha, cwd=repo).strip()
    parts = meta.split(_FIELD_SEP)
    if len(parts) < 6:
        raise GitError(f"unexpected show output for {sha!r}")
    full_sha, parents, author, ts, _decor, subject = parts[:6]
    files: list[dict] = []
    raw = _git("show", "--numstat", "--format=", sha, cwd=repo)
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        adds, dels, path = cols[0], cols[1], cols[2]
        files.append(
            {
                "path": path,
                "additions": int(adds) if adds.isdigit() else 0,
                "deletions": int(dels) if dels.isdigit() else 0,
                "binary": adds == "-",
            }
        )
    return {
        "sha": full_sha,
        "subject": subject,
        "author": author,
        "ts": float(ts) if ts.strip() else 0.0,
        "parents": parents.split() if parents.strip() else [],
        "files": files,
    }


def in_worktree(wt: Worktree, p: Path) -> bool:
    """True iff `p` is inside the worktree path. The caller must use this guard."""
    try:
        p_abs = p.resolve()
        wt_abs = wt.path.resolve()
        return wt_abs in p_abs.parents or p_abs == wt_abs
    except OSError:
        return False
