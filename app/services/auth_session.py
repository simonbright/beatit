import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.config import settings
from app.services.auth_users import (
    all_allowed_usernames,
    resolve_username,
    verify_disk_password,
)

COOKIE_NAME = "beatit_session"
SESSION_DAYS = 7


def _signing_key() -> bytes:
    secret = (
        settings.auth_secret
        or settings.auth_password
        or "|".join(sorted(f"{u}:{p}" for u, p in settings.auth_password_by_user.items()))
    )
    return hashlib.sha256(f"beatit-session:{secret}".encode()).digest()


def verify_credentials(username: str, password: str) -> bool:
    username = (username or "").strip()
    if not username or not password:
        return False
    matched = resolve_username(username)
    if not matched:
        return False
    # Disk users override env passwords so new accounts work without Render secrets.
    if verify_disk_password(matched, password):
        return True
    # Only env-listed users may use AUTH_PASSWORD / AUTH_USER_PASSWORDS.
    env_names = {u.lower() for u in settings.auth_usernames}
    if matched.lower() not in env_names:
        return False
    expected = settings.password_for(matched)
    if not expected:
        return False
    return secrets.compare_digest(password, expected)


def create_session_token(username: str) -> str:
    canonical = resolve_username(username) or username.strip()
    payload = {
        "u": canonical,
        "exp": int(time.time()) + SESSION_DAYS * 86400,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode()


def verify_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode())
        raw, sig = decoded.rsplit(b".", 1)
        expected = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload: dict[str, Any] = json.loads(raw.decode())
        if payload.get("exp", 0) < time.time():
            return None
        username = payload.get("u")
        if not isinstance(username, str) or not username:
            return None
        allowed = {u.lower() for u in all_allowed_usernames()}
        if username.lower() not in allowed:
            return None
        return resolve_username(username) or username
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
