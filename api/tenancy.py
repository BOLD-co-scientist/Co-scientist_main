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
_COPY_TEST_FILES = ("__init__.py", "test_smoke_v0.py")
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

    _ensure_runtime_dirs(root)
    _ensure_git_repo(root)
    marker.write_text("This is a per-user coscientist harness root.\n", encoding="utf-8")
    return root


def _ensure_runtime_dirs(root: Path) -> None:
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
