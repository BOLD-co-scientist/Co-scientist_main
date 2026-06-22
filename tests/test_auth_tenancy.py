from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def _reload_for_root(tmp_path, monkeypatch):
    monkeypatch.setenv("COSCIENTIST_ROOT", str(tmp_path))
    from scaffold import settings

    importlib.reload(settings)

    import api.auth as auth
    import api.tenancy as tenancy
    import api.server as server

    importlib.reload(auth)
    importlib.reload(tenancy)
    importlib.reload(server)
    return auth, tenancy, server


def _seed_base(root: Path) -> None:
    source = Path(__file__).resolve().parents[1]
    for name in ("scaffold", "research", "evolution", "tools", "roles", "prompts", "tests"):
        target = root / name
        if not target.exists():
            import shutil

            shutil.copytree(source / name, target)
    for name in ("pyproject.toml", "Dockerfile", "README.md", "CLAUDE.md", "ROADMAP.md", ".gitignore"):
        src = source / name
        if src.exists():
            (root / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def test_create_user_hashes_key_and_bootstraps_root(tmp_path, monkeypatch):
    _seed_base(tmp_path)
    auth, tenancy, _server = _reload_for_root(tmp_path, monkeypatch)

    user, api_key = auth.create_user("alice")
    assert api_key.startswith(f"csk_{user.user_id}_")
    assert auth.authenticate_api_key(api_key).user_id == user.user_id
    assert auth.authenticate_api_key(api_key + "x") is None

    root = tenancy.ensure_user_root(user.user_id)
    assert (root / ".git").exists()
    assert (root / "scaffold").is_dir()
    assert (root / "research").is_dir()
    assert not (root / "api").exists()
    assert (root / "tests" / "test_smoke_v0.py").exists()
    assert not (root / "tests" / "test_auth_tenancy.py").exists()
    assert (root / "state" / "sessions").is_dir()

    assert auth.delete_user(user.user_id) is True
    assert auth.authenticate_api_key(api_key) is None
    assert auth.delete_user(user.user_id) is False


def test_api_key_isolates_sessions(tmp_path, monkeypatch):
    _seed_base(tmp_path)
    auth, tenancy, server = _reload_for_root(tmp_path, monkeypatch)
    user_a, key_a = auth.create_user("alice")
    user_b, key_b = auth.create_user("bob")
    ctx_a = tenancy.context_for(user_a)
    ctx_b = tenancy.context_for(user_b)

    server._ensure_session_dirs(ctx_a, "same-session")
    server._append_event(ctx_a, "same-session", actor="human", kind="research.requested", task="A")
    server._ensure_session_dirs(ctx_b, "same-session")
    server._append_event(ctx_b, "same-session", actor="human", kind="research.requested", task="B")

    client = TestClient(server.app)
    headers_a = {"Authorization": f"Bearer {key_a}"}
    headers_b = {"Authorization": f"Bearer {key_b}"}

    events_a = client.get("/sessions/same-session/events", headers=headers_a).json()
    events_b = client.get("/sessions/same-session/events", headers=headers_b).json()
    assert events_a[0]["task"] == "A"
    assert events_b[0]["task"] == "B"

    assert client.delete("/sessions/same-session", headers=headers_a).status_code == 200
    assert not ctx_a.session_dir("same-session").exists()
    assert ctx_b.session_dir("same-session").exists()
