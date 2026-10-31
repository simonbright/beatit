"""Local Mac WhatsApp export inbox — Downloads / Desktop only."""

import io
import zipfile
from pathlib import Path

from app.services.whatsapp_mac import (
    guess_chat_name,
    import_new_exports,
    inbox_status,
    list_exports,
    resolve_export,
)


def _chat_zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("_chat.txt", text)
    return buf.getvalue()


def test_guess_chat_name_from_desktop_export():
    assert guess_chat_name("WhatsApp Chat - Susan Health Updates.zip") == "Susan Health Updates"
    assert guess_chat_name("WhatsApp Chat with Hadas.zip") == "Hadas"


def test_list_exports_prefers_named_chat(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "notes.txt").write_text("ignore me", encoding="utf-8")
    (downloads / "WhatsApp Chat - Other.zip").write_bytes(b"PK\x03\x04other")
    match = downloads / "WhatsApp Chat - Susan Health Updates.zip"
    match.write_bytes(b"PK\x03\x04susan")
    rows = list_exports("Susan Health Updates", folders=[downloads])
    assert [row["name"] for row in rows] == ["WhatsApp Chat - Susan Health Updates.zip"]
    assert rows[0]["chat_name"] == "Susan Health Updates"


def test_resolve_export_rejects_path_escape(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "WhatsApp Chat - Family.zip").write_bytes(b"PK\x03\x04ok")
    assert resolve_export(filename="../.env", folders=[downloads]) is None
    assert resolve_export(filename="WhatsApp Chat - Family.zip", folders=[downloads]) == downloads / "WhatsApp Chat - Family.zip"


def test_inbox_status_lists_present_folders(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr("app.services.whatsapp_mac.inbox_enabled", lambda: True)
    status = inbox_status(folders=[downloads, tmp_path / "Desktop"])
    assert status["available"] is True
    assert status["folders"] == ["Downloads"]
    assert status["exports"] == []


def test_background_import_skips_existing_then_adds_new(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old = downloads / "WhatsApp Chat - Old.zip"
    old.write_bytes(_chat_zip("[2026-10-28, 9:00:00 AM] Hadas: old note about mom\n"))
    monkeypatch.setattr("app.services.whatsapp_mac._state_path", lambda: tmp_path / "watch.json")
    monkeypatch.setattr("app.services.whatsapp_mac.patients_for_chat", lambda hint: ["susan"])
    imported: list[tuple[str, int]] = []

    def fake_import(pid, events):
        imported.append((pid, len(events)))
        return {"added": len(events), "skipped": 0}

    monkeypatch.setattr("app.services.case_manager.import_patient_care_events", fake_import)
    first = import_new_exports(folders=[downloads], mark_existing=True)
    assert first["marked"] is True
    assert imported == []
    (downloads / "WhatsApp Chat - Susan Health Updates.zip").write_bytes(
        _chat_zip("[2026-10-31, 2:00:00 PM] Hadas: new update about mom labs\n")
    )
    second = import_new_exports(folders=[downloads])
    assert second["imported"] == 1
    assert imported and imported[0][0] == "susan"
