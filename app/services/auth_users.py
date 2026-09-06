"""Persistent app users stored on DATA_DIR (survives deploys; no Render env edit needed).

Env AUTH_USERNAME / AUTH_PASSWORD / AUTH_USER_PASSWORDS remain the bootstrap allowlist.
Disk users in auth_users.json can add or override passwords without redeploying secrets.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

AUTH_USERS_FILENAME = "auth_users.json"
_PBKDF2_ITERATIONS = 200_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    return settings.data_dir / AUTH_USERS_FILENAME


def _default_store() -> dict[str, Any]:
    return {"users": []}


def load_auth_users() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return _default_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_store()
    if not isinstance(data, dict):
        return _default_store()
    users = data.get("users")
    if not isinstance(users, list):
        data["users"] = []
    return data


def save_auth_users(store: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _normalize_username(username: str) -> str:
    return (username or "").strip()


def hash_password(password: str, *, salt: str | None = None) -> tuple[str, str]:
    salt_hex = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        _PBKDF2_ITERATIONS,
    ).hex()
    return digest, salt_hex


def _entry_for(store: dict[str, Any], username: str) -> dict[str, Any] | None:
    needle = _normalize_username(username).lower()
    for entry in store.get("users") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("username") or "").strip().lower() == needle:
            return entry
    return None


def list_auth_usernames() -> list[str]:
    store = load_auth_users()
    names: list[str] = []
    for entry in store.get("users") or []:
        if not isinstance(entry, dict):
            continue
        name = _normalize_username(str(entry.get("username") or ""))
        if name:
            names.append(name)
    return names


def all_allowed_usernames() -> list[str]:
    """Env allowlist plus disk users (canonical casing preserved)."""
    seen: dict[str, str] = {}
    for name in settings.auth_usernames:
        key = name.lower()
        seen[key] = name
    for name in list_auth_usernames():
        key = name.lower()
        seen[key] = name
    return list(seen.values())


def resolve_username(username: str) -> str | None:
    needle = _normalize_username(username).lower()
    if not needle:
        return None
    for name in all_allowed_usernames():
        if name.lower() == needle:
            return name
    return None


def verify_disk_password(username: str, password: str) -> bool:
    entry = _entry_for(load_auth_users(), username)
    if not entry:
        return False
    salt = str(entry.get("salt") or "")
    expected = str(entry.get("password_hash") or "")
    if not salt or not expected:
        return False
    digest, _ = hash_password(password, salt=salt)
    return secrets.compare_digest(digest, expected)


def upsert_auth_user(username: str, password: str) -> dict[str, Any]:
    cleaned = _normalize_username(username)
    if not cleaned or "@" not in cleaned:
        raise ValueError("Username must be an email address")
    if len(cleaned) > 200:
        raise ValueError("Username is too long")
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(password) > 200:
        raise ValueError("Password is too long")

    store = load_auth_users()
    digest, salt = hash_password(password)
    existing = _entry_for(store, cleaned)
    now = _now_iso()
    if existing:
        existing["username"] = cleaned
        existing["password_hash"] = digest
        existing["salt"] = salt
        existing["updated_at"] = now
        entry = existing
    else:
        entry = {
            "username": cleaned,
            "password_hash": digest,
            "salt": salt,
            "created_at": now,
            "updated_at": now,
        }
        store.setdefault("users", []).append(entry)
    save_auth_users(store)
    return {"username": cleaned, "updated_at": entry["updated_at"]}


def delete_auth_user(username: str) -> bool:
    store = load_auth_users()
    needle = _normalize_username(username).lower()
    users = [u for u in (store.get("users") or []) if isinstance(u, dict)]
    kept = [u for u in users if str(u.get("username") or "").strip().lower() != needle]
    if len(kept) == len(users):
        return False
    store["users"] = kept
    save_auth_users(store)
    return True
