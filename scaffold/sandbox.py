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


def create(slug: str) -> Worktree:
    ensure_repo()
    settings.WORKTREES.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    name = f"{ts}__{slug}"
    branch = f"evo/{name}"
    path = settings.WORKTREES / name
    base = _git("rev-parse", "HEAD").strip()
    _git("worktree", "add", "-b", branch, str(path), base)
    return Worktree(path=path, branch=branch, base=base)


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


def in_worktree(wt: Worktree, p: Path) -> bool:
    """True iff `p` is inside the worktree path. The caller must use this guard."""
    try:
        p_abs = p.resolve()
        wt_abs = wt.path.resolve()
        return wt_abs in p_abs.parents or p_abs == wt_abs
    except OSError:
        return False
