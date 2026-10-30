"""Web Push subscriptions and family-update notifications."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.auth_users import all_allowed_usernames
from app.services.profile_access import can_access_patient

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_FILE = "push_subscriptions.json"
VAPID_FILE = "vapid.json"
MAX_SUBSCRIPTIONS = 80


def _store_dir() -> Path:
    path = Path(settings.data_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def vapid_contact() -> str:
    mailto = (os.getenv("VAPID_MAILTO") or "").strip()
    if mailto:
        return mailto if mailto.startswith("mailto:") else f"mailto:{mailto}"
    users = settings.auth_usernames
    if users:
        return f"mailto:{users[0]}"
    return "mailto:bright-health@localhost"


def ensure_vapid_keys() -> dict[str, str]:
    env_pub = (os.getenv("VAPID_PUBLIC_KEY") or "").strip()
    env_priv = (os.getenv("VAPID_PRIVATE_KEY") or "").strip()
    if env_pub and env_priv:
        return {"public_key": env_pub, "private_key": env_priv}

    path = _store_dir() / VAPID_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if data.get("public_key") and data.get("private_key"):
            return {"public_key": str(data["public_key"]), "private_key": str(data["private_key"])}

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    numbers = key.public_key().public_numbers()
    raw = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    public_key = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    data = {"public_key": public_key, "private_key": private_pem}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def _subscriptions_path() -> Path:
    return _store_dir() / SUBSCRIPTIONS_FILE


def load_subscriptions() -> list[dict[str, Any]]:
    path = _subscriptions_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("subscriptions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("endpoint")]


def save_subscriptions(items: list[dict[str, Any]]) -> None:
    path = _subscriptions_path()
    path.write_text(
        json.dumps({"subscriptions": items[:MAX_SUBSCRIPTIONS]}, indent=2) + "\n",
        encoding="utf-8",
    )


def _normalize_username(username: str | None) -> str:
    return (username or "").strip()


def user_has_subscription(username: str | None) -> bool:
    key = _normalize_username(username).lower()
    if not key:
        return False
    return any(_normalize_username(item.get("username")).lower() == key for item in load_subscriptions())


def upsert_subscription(
    *,
    username: str,
    endpoint: str,
    keys: dict[str, str],
    user_agent: str = "",
) -> dict[str, Any]:
    endpoint = (endpoint or "").strip()
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        raise ValueError("That notification subscription is incomplete")
    now = datetime.now(timezone.utc).isoformat()
    items = [item for item in load_subscriptions() if item.get("endpoint") != endpoint]
    record = {
        "username": _normalize_username(username) or "local",
        "endpoint": endpoint,
        "keys": {"p256dh": p256dh, "auth": auth},
        "user_agent": (user_agent or "")[:240],
        "created_at": now,
        "updated_at": now,
    }
    items.append(record)
    save_subscriptions(items)
    return record


def remove_subscription(*, endpoint: str, username: str | None = None) -> bool:
    endpoint = (endpoint or "").strip()
    if not endpoint:
        return False
    key = _normalize_username(username).lower()
    kept: list[dict[str, Any]] = []
    removed = False
    for item in load_subscriptions():
        if item.get("endpoint") != endpoint:
            kept.append(item)
            continue
        if key and _normalize_username(item.get("username")).lower() != key:
            kept.append(item)
            continue
        removed = True
    if removed:
        save_subscriptions(kept)
    return removed


def _drop_endpoints(endpoints: set[str]) -> None:
    if not endpoints:
        return
    save_subscriptions([item for item in load_subscriptions() if item.get("endpoint") not in endpoints])


def _subscription_info(item: dict[str, Any]) -> dict[str, Any]:
    keys = item.get("keys") if isinstance(item.get("keys"), dict) else {}
    return {
        "endpoint": item["endpoint"],
        "keys": {"p256dh": keys.get("p256dh"), "auth": keys.get("auth")},
    }


def _deliver(item: dict[str, Any], payload: dict[str, Any], vapid: dict[str, str]) -> str:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=_subscription_info(item),
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=vapid["private_key"],
            vapid_claims={"sub": vapid_contact()},
        )
        return "sent"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {404, 410}:
            return "gone"
        logger.warning("Web push failed (%s): %s", status or "error", exc)
        return "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Web push failed: %s", exc)
        return "error"


def recipients_for_update(*, patient_id: str, actor: str | None) -> list[dict[str, Any]]:
    actor_key = _normalize_username(actor).lower()
    known = {name.lower() for name in all_allowed_usernames()}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in load_subscriptions():
        endpoint = str(item.get("endpoint") or "")
        if not endpoint or endpoint in seen:
            continue
        user = _normalize_username(item.get("username"))
        user_key = user.lower()
        if actor_key and user_key == actor_key:
            continue
        if settings.auth_enabled and known and user_key not in known:
            continue
        if not can_access_patient(user or None, patient_id):
            continue
        seen.add(endpoint)
        out.append(item)
    return out


async def notify_patient_update(
    *,
    patient_id: str,
    actor: str | None,
    title: str,
    body: str,
    url_path: str = "/#timeline",
) -> dict[str, int]:
    if not settings.auth_enabled:
        return {"sent": 0, "skipped": 0, "gone": 0}
    recipients = recipients_for_update(patient_id=patient_id, actor=actor)
    if not recipients:
        return {"sent": 0, "skipped": 0, "gone": 0}
    vapid = ensure_vapid_keys()
    payload = {
        "title": (title or "Bright Health").strip() or "Bright Health",
        "body": (body or "There is a new update.").strip()[:220],
        "url": url_path or "/#timeline",
    }
    results = await asyncio.gather(
        *[asyncio.to_thread(_deliver, item, payload, vapid) for item in recipients]
    )
    gone = {item["endpoint"] for item, status in zip(recipients, results, strict=False) if status == "gone"}
    _drop_endpoints(gone)
    return {
        "sent": sum(1 for status in results if status == "sent"),
        "skipped": 0,
        "gone": len(gone),
    }


async def send_subscription_confirmation(item: dict[str, Any]) -> None:
    vapid = ensure_vapid_keys()
    payload = {
        "title": "Bright Health",
        "body": "Notifications are on. We'll ping this device when someone records an update.",
        "url": "/#timeline",
    }
    status = await asyncio.to_thread(_deliver, item, payload, vapid)
    if status == "gone":
        _drop_endpoints({item["endpoint"]})


def schedule_patient_update_push(
    *,
    patient_id: str,
    actor: str | None,
    title: str,
    body: str,
    url_path: str = "/#timeline",
) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        notify_patient_update(
            patient_id=patient_id,
            actor=actor,
            title=title,
            body=body,
            url_path=url_path,
        )
    )


def patient_display_name(patient_id: str) -> str:
    from app.services.case_manager import list_patients

    for patient in list_patients():
        if patient.get("id") == patient_id:
            return str(patient.get("label") or "").strip() or "Family"
    return "Family"
