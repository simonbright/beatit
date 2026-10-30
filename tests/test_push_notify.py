"""Web Push subscription store and family-update recipient rules."""

from __future__ import annotations

import json

from app.config import settings
from app.services import push_notify
from app.services.push_notify import (
    ensure_vapid_keys,
    load_subscriptions,
    recipients_for_update,
    remove_subscription,
    upsert_subscription,
    user_has_subscription,
)


def test_vapid_keys_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    first = ensure_vapid_keys()
    second = ensure_vapid_keys()
    assert first["public_key"] == second["public_key"]
    assert first["private_key"].startswith("-----BEGIN")
    stored = json.loads((tmp_path / "vapid.json").read_text(encoding="utf-8"))
    assert stored["public_key"] == first["public_key"]


def test_subscribe_dedupes_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    keys = {"p256dh": "abc", "auth": "def"}
    upsert_subscription(username="simon@x.com", endpoint="https://push.example/1", keys=keys)
    upsert_subscription(username="susan@x.com", endpoint="https://push.example/1", keys=keys)
    items = load_subscriptions()
    assert len(items) == 1
    assert items[0]["username"] == "susan@x.com"
    assert user_has_subscription("susan@x.com")
    assert not user_has_subscription("simon@x.com")
    assert remove_subscription(endpoint="https://push.example/1", username="susan@x.com")
    assert load_subscriptions() == []


def test_recipients_skip_actor_and_other_patients(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "auth_username", "simon@x.com,susan@x.com,other@x.com")
    monkeypatch.setattr(settings, "auth_password", "secret")
    monkeypatch.setattr(
        push_notify,
        "all_allowed_usernames",
        lambda: ["simon@x.com", "susan@x.com", "other@x.com"],
    )

    def access(username, patient_id):
        if username == "other@x.com":
            return patient_id == "someone-else"
        return patient_id == "p1"

    monkeypatch.setattr(push_notify, "can_access_patient", access)
    keys = {"p256dh": "abc", "auth": "def"}
    upsert_subscription(username="simon@x.com", endpoint="https://push.example/simon", keys=keys)
    upsert_subscription(username="susan@x.com", endpoint="https://push.example/susan", keys=keys)
    upsert_subscription(username="other@x.com", endpoint="https://push.example/other", keys=keys)

    people = [item["username"] for item in recipients_for_update(patient_id="p1", actor="simon@x.com")]
    assert people == ["susan@x.com"]
