"""Per signed-in user active profile, isolated from other sessions.

The registry still holds a default for local/single-user mode. When someone is
signed in, their patient/case selection is stored here and read for that
request only.
"""

from __future__ import annotations

import json
import threading
from contextvars import ContextVar, Token
from typing import Any

from app.config import settings

_actor: ContextVar[str | None] = ContextVar("beatit_actor", default=None)
_pinned: ContextVar[tuple[str, str] | None] = ContextVar("beatit_pinned_scope", default=None)
_lock = threading.Lock()
_FILENAME = "active_contexts.json"


def set_request_actor(username: str | None) -> Token:
    return _actor.set((username or "").strip() or None)


def reset_request_actor(token: Token) -> None:
    _actor.reset(token)


def current_actor() -> str | None:
    return _actor.get()


def pin_active_scope(patient_id: str | None, case_id: str | None) -> Token:
    """Keep a background job on the profile it started with."""
    pid = str(patient_id or "").strip()
    cid = str(case_id or "").strip()
    if pid and cid:
        return _pinned.set((pid, cid))
    return _pinned.set(None)


def reset_pinned_scope(token: Token) -> None:
    _pinned.reset(token)


def pinned_scope() -> tuple[str, str] | None:
    return _pinned.get()


def _path():
    return settings.data_dir / _FILENAME


def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"users": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"users": {}}
    if not isinstance(data, dict):
        return {"users": {}}
    users = data.get("users")
    if not isinstance(users, dict):
        data["users"] = {}
    return data


def _save(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_saved_selection(username: str) -> tuple[str, str] | None:
    key = (username or "").strip().lower()
    if not key:
        return None
    with _lock:
        users = _load().get("users") or {}
    row = users.get(key) if isinstance(users, dict) else None
    if not isinstance(row, dict):
        return None
    pid = str(row.get("patient_id") or "").strip()
    cid = str(row.get("case_id") or "").strip()
    if not pid or not cid:
        return None
    return pid, cid


def save_selection(username: str, patient_id: str, case_id: str) -> None:
    key = (username or "").strip().lower()
    if not key:
        return
    with _lock:
        data = _load()
        users = data.setdefault("users", {})
        users[key] = {
            "patient_id": patient_id,
            "case_id": case_id,
            "username": username.strip(),
        }
        _save(data)
