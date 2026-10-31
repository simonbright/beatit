"""Pick up WhatsApp Desktop / AirDrop chat exports from this Mac.

Only used when Bright Health is running locally on macOS. It looks in
Downloads, Desktop, and ~/Bright Health/whatsapp-inbox for files WhatsApp
names when you Export Chat. It never takes an arbitrary path from the client.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_EXPORT_BYTES = 12 * 1024 * 1024
_NAME_RE = re.compile(r"whatsapp\s+chat", re.I)
_SUFFIXES = {".zip", ".txt"}
_PREFIX = re.compile(r"^WhatsApp Chat\s*(?:with|[-–])\s*", re.I)


def inbox_enabled() -> bool:
    return sys.platform == "darwin" and not os.getenv("RENDER")


def inbox_dirs() -> list[Path]:
    home = Path.home()
    return [home / "Downloads", home / "Desktop", home / "Bright Health" / "whatsapp-inbox"]


def guess_chat_name(filename: str) -> str:
    stem = Path(filename or "").stem
    if stem.lower() == "_chat":
        return ""
    cleaned = _PREFIX.sub("", stem).strip(" -–_")
    return cleaned[:120]


def _is_export_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
        return False
    if path.stat().st_size > MAX_EXPORT_BYTES:
        return False
    return bool(_NAME_RE.search(path.name)) or path.name.lower() == "_chat.txt"


def _iter_export_paths(folders: list[Path] | None = None) -> list[Path]:
    found: list[Path] = []
    for folder in folders or inbox_dirs():
        if not folder.is_dir():
            continue
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_file() and _is_export_file(path):
                    found.append(path)
                elif path.is_dir() and _NAME_RE.search(path.name):
                    chat = path / "_chat.txt"
                    if chat.is_file() and chat.stat().st_size <= MAX_EXPORT_BYTES:
                        found.append(chat)
            except OSError:
                continue
    return found


def _export_row(path: Path) -> dict[str, Any]:
    stat = path.stat()
    display = path.name if path.name.lower() != "_chat.txt" else path.parent.name
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return {
        "name": display,
        "chat_name": guess_chat_name(display),
        "folder": path.parent.name if path.name.lower() == "_chat.txt" else path.parent.name,
        "size": stat.st_size,
        "modified": modified,
        "mtime": stat.st_mtime,
        "key": f"{display}|{stat.st_mtime:.0f}|{stat.st_size}",
    }


def list_exports(chat_hint: str = "", *, folders: list[Path] | None = None) -> list[dict[str, Any]]:
    hint = " ".join(str(chat_hint or "").lower().split())
    rows = [_export_row(path) for path in _iter_export_paths(folders)]
    rows.sort(key=lambda row: row["mtime"], reverse=True)
    if hint:
        matched = [row for row in rows if hint in (row.get("chat_name") or "").lower() or hint in row["name"].lower()]
        if matched:
            return matched
    return rows[:12]


def resolve_export(
    *,
    chat_hint: str = "",
    filename: str = "",
    folders: list[Path] | None = None,
) -> Path | None:
    """Return a real inbox file. `filename` is a basename only."""
    wanted = Path(filename or "").name
    if wanted in {".", "..", ""}:
        wanted = ""
    if wanted and ("/" in filename or "\\" in filename):
        return None
    candidates = _iter_export_paths(folders)
    if wanted:
        for path in candidates:
            display = path.name if path.name.lower() != "_chat.txt" else path.parent.name
            if display == wanted or path.name == wanted:
                return path
        return None
    rows = list_exports(chat_hint, folders=folders)
    if not rows:
        return None
    key = rows[0]["key"]
    for path in candidates:
        if _export_row(path)["key"] == key:
            return path
    return None


def inbox_status(chat_hint: str = "", *, folders: list[Path] | None = None) -> dict[str, Any]:
    folders = folders if folders is not None else inbox_dirs()
    present = [p.name for p in folders if p.is_dir()]
    return {
        "available": inbox_enabled(),
        "watching": inbox_enabled() and _watch_thread is not None and _watch_thread.is_alive(),
        "folders": present,
        "exports": list_exports(chat_hint, folders=folders) if inbox_enabled() else [],
        "last_import": dict(_last_import) if _last_import else None,
    }


def open_whatsapp_app() -> bool:
    if not inbox_enabled():
        return False
    import subprocess

    try:
        result = subprocess.run(["open", "-a", "WhatsApp"], check=False, timeout=8, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


_watch_thread = None
_watch_stop = None
_last_import: dict[str, Any] | None = None


def _state_path() -> Path:
    from app.config import settings

    return Path(settings.data_dir) / "whatsapp_mac_watch.json"


def _load_seen() -> set[str]:
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    seen = data.get("seen") if isinstance(data, dict) else None
    if isinstance(seen, list):
        return {str(x) for x in seen if x}
    if isinstance(seen, dict):
        return {str(k) for k in seen if k}
    return set()


def _save_seen(seen: set[str], last_import: dict[str, Any] | None = None) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"seen": sorted(seen)[-200:]}
        if last_import:
            payload["last_import"] = last_import
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return


def patients_for_chat(chat_hint: str) -> list[str]:
    from app.services.case_manager import get_patient_profile, list_patients

    hint = " ".join(str(chat_hint or "").lower().split())
    patients = list_patients()
    if not patients:
        return []
    hits: list[str] = []
    for row in patients:
        pid = str(row.get("id") or "")
        if not pid:
            continue
        profile = get_patient_profile(pid)
        last = " ".join(str(profile.get("last_whatsapp_chat") or "").lower().split())
        titles = {
            " ".join(str(ev.get("source_document_title") or "").lower().split())
            for ev in (profile.get("care_events") or [])
            if ev.get("source") == "whatsapp"
        }
        titles.discard("")
        if hint and (hint in last or last in hint or any(hint in title or title in hint for title in titles)):
            hits.append(pid)
    if hits:
        return hits
    if len(patients) == 1:
        return [str(patients[0]["id"])]
    return []


def import_new_exports(*, folders: list[Path] | None = None, mark_existing: bool = False) -> dict[str, Any]:
    """Import WhatsApp exports that appeared since the last check. Local Mac only."""
    global _last_import
    from app.services.case_manager import import_patient_care_events
    from app.services.whatsapp_import import messages_to_care_events, parse_whatsapp_export, text_from_export_bytes

    seen = _load_seen()
    current = [_export_row(path) | {"_path": path} for path in _iter_export_paths(folders)]
    keys = {row["key"] for row in current}
    if mark_existing or not seen:
        _save_seen(seen | keys, _last_import)
        return {"imported": 0, "skipped": len(keys), "marked": True}
    added_total = 0
    imported_files = 0
    for row in current:
        if row["key"] in seen:
            continue
        path: Path = row["_path"]
        try:
            text = text_from_export_bytes(path.read_bytes())
            messages = parse_whatsapp_export(text)
        except (OSError, ValueError):
            seen.add(row["key"])
            continue
        if not messages:
            seen.add(row["key"])
            continue
        chat_name = row.get("chat_name") or guess_chat_name(row["name"])
        targets = patients_for_chat(chat_name)
        if not targets:
            continue
        file_added = 0
        for pid in targets:
            result = import_patient_care_events(pid, messages_to_care_events(messages, chat_name=chat_name))
            if result:
                file_added += int(result.get("added") or 0)
        seen.add(row["key"])
        imported_files += 1
        added_total += file_added
        _last_import = {
            "name": row["name"],
            "chat_name": chat_name,
            "added": file_added,
            "patients": targets,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"WhatsApp inbox: {row['name']} → {file_added} new message(s) for {', '.join(targets)}", flush=True)
    _save_seen(seen, _last_import)
    return {"imported": imported_files, "added": added_total, "skipped": 0, "marked": False}


def start_inbox_watcher(*, interval: float = 12.0) -> bool:
    """Watch Downloads in the background while Bright Health is running on this Mac."""
    global _watch_thread, _watch_stop
    if not inbox_enabled():
        return False
    if _watch_thread and _watch_thread.is_alive():
        return True
    import threading

    import_new_exports(mark_existing=True)
    stop = threading.Event()
    _watch_stop = stop

    def loop() -> None:
        while not stop.wait(interval):
            try:
                import_new_exports()
            except Exception as exc:  # noqa: BLE001
                print(f"WhatsApp inbox watch failed: {exc}", flush=True)

    thread = threading.Thread(target=loop, name="whatsapp-mac-inbox", daemon=True)
    _watch_thread = thread
    thread.start()
    print("WhatsApp inbox: watching Downloads for new chat exports", flush=True)
    return True


def stop_inbox_watcher() -> None:
    global _watch_thread
    if _watch_stop:
        _watch_stop.set()
    _watch_thread = None
