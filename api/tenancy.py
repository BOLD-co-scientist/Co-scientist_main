from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scaffold import settings

from .auth import User


USERS_DIR = settings.STATE / "users"

_COPY_DIRS = (
    "scaffold",
    "research",
    "evolution",
    "tools",
    "roles",
    "prompts",
    "docs",
)
_COPY_FILES = (
    "pyproject.toml",
    "Dockerfile",
    "README.md",
    "CLAUDE.md",
    "ROADMAP.md",
    ".gitignore",
)
_COPY_TEST_FILES = ("__init__.py", "test_smoke_v0.py", "test_contract_compat.py")
_IGNORE_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "*.pyc",
    "*.egg-info",
}


@dataclass(frozen=True)
class UserContext:
    user: User
    root: Path

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def sessions(self) -> Path:
        return self.state / "sessions"

    @property
    def library(self) -> Path:
        return self.state / "library"

    @property
    def library_staging(self) -> Path:
        return self.library / ".staging"

    @property
    def library_events(self) -> Path:
        return self.state / "library_events.jsonl"

    @property
    def worktrees(self) -> Path:
        return self.root / "worktrees"

    @property
    def researcher_data(self) -> Path:
        return self.root / "researcher_data"

    def session_dir(self, session_id: str) -> Path:
        return self.sessions / session_id


def user_base_dir(user_id: str) -> Path:
    return USERS_DIR / user_id


def user_root(user_id: str) -> Path:
    return user_base_dir(user_id) / "root"


def context_for(user: User) -> UserContext:
    root = ensure_user_root(user.user_id)
    return UserContext(user=user, root=root)


def ensure_user_root(user_id: str) -> Path:
    root = user_root(user_id)
    marker = root / ".coscientist-user-root"
    if marker.exists():
        _ensure_runtime_dirs(root)
        return root

    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(*_IGNORE_NAMES)

    for dirname in _COPY_DIRS:
        src = settings.ROOT / dirname
        dst = root / dirname
        if src.exists() and not dst.exists():
            shutil.copytree(src, dst, ignore=ignore)

    for filename in _COPY_FILES:
        src = settings.ROOT / filename
        dst = root / filename
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    for filename in _COPY_TEST_FILES:
        src = settings.ROOT / "tests" / filename
        dst = tests_dir / filename
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    # R17: the golden fixtures the compat gate reads must travel into the tenant
    # repo too, or the smoke run in an evolution worktree can't find them.
    fixtures_src = settings.ROOT / "tests" / "fixtures"
    fixtures_dst = tests_dir / "fixtures"
    if fixtures_src.exists() and not fixtures_dst.exists():
        shutil.copytree(fixtures_src, fixtures_dst, ignore=ignore)

    # R17: seed base skills into the tenant's `.claude/skills`. Skills are durable,
    # cross-version *assets* (like memory/results), NOT version-bound harness —
    # `.claude/skills/` is gitignored, so a checkout never swaps them and this seed
    # stays out of the version. Only skills are copied (not the whole `.claude`,
    # which holds non-tenant tooling). No base skills ship yet — this is the seam
    # that carries them once they do.
    base_skills_src = settings.ROOT / ".claude" / "skills"
    base_skills_dst = root / ".claude" / "skills"
    if base_skills_src.exists() and not base_skills_dst.exists():
        shutil.copytree(base_skills_src, base_skills_dst, ignore=ignore)

    _ensure_runtime_dirs(root)
    _ensure_git_repo(root)
    _ensure_git_excludes(root)
    marker.write_text("This is a per-user coscientist harness root.\n", encoding="utf-8")
    return root


def _ensure_git_excludes(root: Path) -> None:
    """Repo-local excludes (never a tracked file) for the tenant marker and
    runtime dirs, so a version switch / import checkout is never blocked by
    "untracked working tree files would be overwritten". Idempotent; applies to
    existing tenants on their next request (their .gitignore is a frozen copy)."""
    info = root / ".git" / "info"
    if not (root / ".git").is_dir():
        return
    try:
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        cur = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        want = [".coscientist-user-root", "/state/", "/worktrees/", "/researcher_data/", ".claude/skills/"]
        missing = [w for w in want if w not in cur.splitlines()]
        if missing:
            exclude.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n") + "\n".join(missing) + "\n", encoding="utf-8")
    except OSError:
        pass


def _ensure_runtime_dirs(root: Path) -> None:
    _ensure_git_excludes(root)
    for path in (
        root / "state" / "sessions",
        root / "state" / "library" / ".staging",
        root / "state" / "memory",
        root / "state" / "archive" / "evolutions",
        root / "state" / "archive" / "skills",
        # Per-user skill library (R7). Discovered by the SDK as project skills
        # relative to the runtime's cwd (= this root).
        root / ".claude" / "skills",
        root / "worktrees",
        root / "researcher_data",
    ):
        path.mkdir(parents=True, exist_ok=True)


def _ensure_git_repo(root: Path) -> None:
    if (root / ".git").exists():
        return
    commands = (
        ("git", "init"),
        ("git", "config", "user.email", "evolution@coscientist.local"),
        ("git", "config", "user.name", "coscientist-evolution"),
        ("git", "add", "-A"),
        ("git", "commit", "-m", "tenant bootstrap"),
    )
    for cmd in commands:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if proc.returncode != 0 and cmd[:2] != ("git", "commit"):
            raise RuntimeError(f"{' '.join(cmd)} failed while bootstrapping {root}:\n{proc.stdout}")
