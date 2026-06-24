from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import Header, HTTPException

from scaffold import settings


AUTH_DIR = settings.STATE / "auth"
USERS_DB = AUTH_DIR / "users.db"
_PBKDF2_ITERATIONS = 210_000


@dataclass(frozen=True)
class User:
    user_id: str
    display_name: str
    created_at: float
    disabled: bool = False


def _connect() -> sqlite3.Connection:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USERS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            key_hash TEXT NOT NULL,
            key_prefix TEXT NOT NULL,
            created_at REAL NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    return conn


def _hash_api_key(api_key: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", api_key.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    packed = salt + digest
    return base64.urlsafe_b64encode(packed).decode("ascii")


def _verify_api_key(api_key: str, stored: str) -> bool:
    try:
        packed = base64.urlsafe_b64decode(stored.encode("ascii"))
    except Exception:
        return False
    if len(packed) < 17:
        return False
    salt, expected = packed[:16], packed[16:]
    digest = hashlib.pbkdf2_hmac(
        "sha256", api_key.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return secrets.compare_digest(digest, expected)


def create_user(display_name: str) -> tuple[User, str]:
    name = display_name.strip()
    if not name:
        raise ValueError("display_name must not be empty")
    user_id = "u_" + secrets.token_hex(8)
    raw_secret = secrets.token_urlsafe(32)
    api_key = f"csk_{user_id}_{raw_secret}"
    created_at = time.time()
    key_hash = _hash_api_key(api_key)
    key_prefix = api_key[:18]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, display_name, key_hash, key_prefix, created_at, disabled)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (user_id, name, key_hash, key_prefix, created_at),
        )
        conn.commit()
    return User(user_id=user_id, display_name=name, created_at=created_at), api_key


def list_users(include_disabled: bool = True) -> list[User]:
    query = "SELECT * FROM users"
    if not include_disabled:
        query += " WHERE disabled = 0"
    query += " ORDER BY created_at DESC"
    with _connect() as conn:
        rows = conn.execute(query).fetchall()
    return [
        User(
            user_id=row["user_id"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            disabled=bool(row["disabled"]),
        )
        for row in rows
    ]


def set_disabled(user_id: str, disabled: bool) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET disabled = ? WHERE user_id = ?",
            (1 if disabled else 0, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_user(user_id: str) -> bool:
    with _connect() as conn:
        conn.execute("UPDATE users SET disabled = 1 WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def authenticate_api_key(api_key: str) -> User | None:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users WHERE disabled = 0").fetchall()
    for row in rows:
        if _verify_api_key(api_key, row["key_hash"]):
            return User(
                user_id=row["user_id"],
                display_name=row["display_name"],
                created_at=row["created_at"],
                disabled=False,
            )
    return None


def require_user(authorization: str | None = Header(default=None)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer API key")
    api_key = authorization.removeprefix("Bearer ").strip()
    user = authenticate_api_key(api_key)
    if user is None:
        raise HTTPException(401, "invalid API key")
    return user


def auth_db_path() -> Path:
    return USERS_DB
