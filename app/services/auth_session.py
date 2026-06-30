import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.config import settings

COOKIE_NAME = "beatit_session"
SESSION_DAYS = 7


def _signing_key() -> bytes:
    secret = settings.auth_secret or settings.auth_password
    return hashlib.sha256(f"beatit-session:{secret}".encode()).digest()


def verify_credentials(username: str, password: str) -> bool:
    username = username.strip()
    if not username or not password:
        return False
    user_ok = any(
        secrets.compare_digest(username, allowed)
        for allowed in settings.auth_usernames
    )
    pass_ok = secrets.compare_digest(password, settings.auth_password)
    return user_ok and pass_ok


def create_session_token(username: str) -> str:
    payload = {
        "u": username.strip(),
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
        if username not in settings.auth_usernames:
            return None
        return username
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
