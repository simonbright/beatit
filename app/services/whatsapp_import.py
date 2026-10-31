"""Parse a WhatsApp "Export chat" .txt into timeline care events.

Messages are imported verbatim — sender, time sent, text. Nothing is
reinterpreted. Re-importing a newer export of the same chat only adds
messages that are not already stored (dedupe on sender + time + text).

Supported line formats (export locale varies):
  iOS:      [2026-10-27, 3:14:22 PM] Name: message
            [10/27/26, 15:14:22] Name: message
  Android:  10/27/26, 3:14 PM - Name: message
            27/10/2026, 15:14 - Name: message
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_INVISIBLE = re.compile("[‎‏‪-‮﻿]")
_IOS = re.compile(r"^\[(?P<date>[^,\]]+),\s*(?P<time>[^\]]+)\]\s(?P<rest>.*)$")
_ANDROID = re.compile(r"^(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{1,4}),?\s(?P<time>\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[APap]\.?[Mm]\.?)?)\s[-–]\s(?P<rest>.*)$")
_SENDER = re.compile(r"^(?P<sender>[^:]{1,80}):\s(?P<text>.*)$", re.S)

SKIP_PATTERNS = (
    "messages and calls are end-to-end encrypted",
    "created group",
    "added you",
    " joined using this group",
    "changed the subject",
    "changed this group",
    "changed the group",
    "<media omitted>",
    "this message was deleted",
    "you deleted this message",
    "image omitted",
    "video omitted",
    "sticker omitted",
    "gif omitted",
    "audio omitted",
    "document omitted",
    "missed voice call",
    "missed video call",
    "null",
)


_EDITED = re.compile(r"\s*<This message was edited>\s*$", re.I)

# Words that mark a message as carrying care information rather than chatter.
_SIGNAL_WORDS = re.compile(
    r"\b(mom|mum|mother|she|her|stable|danger\w*|dr|doctor\w*|doc|nurse\w*|onc\w*|internist|hospitalist|specialist|surgeon|resident|rounds|"
    r"test|tests|scan|ct|mri|ultrasound|x-?ray|blood|bp|pressure|fever|temp\w*|oxygen|sat\w*|"
    r"culture|infection|antibiotic\w*|med\w*|dose|iv|fluids?|catheter|pain|icu|unit|room\w*|ward|moved|transfer\w*|"
    r"discharg\w*|admit\w*|result\w*|update|ate|eating|sleep\w*|alert|weak|stronger|confus\w*|"
    r"biopsy|chemo\w*|tumou?r|cancer|liver|kidney|lung|heart|plan|procedure|surgery)\b",
    re.I,
)


def is_substantive(text: str) -> bool:
    """True when a message likely carries care information (not 'thanks' / 'ok')."""
    t = text.strip()
    if len(t) >= 80:
        return True
    if len(t) < 12:
        return False
    return bool(_SIGNAL_WORDS.search(t) or re.search(r"\d", t))


def _clean_line(line: str) -> str:
    for ch in ("\u2066", "\u2067", "\u2068", "\u2069"):
        line = line.replace(ch, "")
    return _INVISIBLE.sub("", line).replace(" ", " ").replace("\xa0", " ").rstrip("\r")


def _date_order(samples: list[str]) -> str:
    """Return 'ymd', 'mdy' or 'dmy' from a sample of date strings."""
    first_gt12 = second_gt12 = False
    for raw in samples:
        parts = re.split(r"[./-]", raw.strip())
        if len(parts) != 3:
            continue
        if len(parts[0]) == 4:
            return "ymd"
        try:
            a, b = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        first_gt12 |= a > 12
        second_gt12 |= b > 12
    if first_gt12 and not second_gt12:
        return "dmy"
    return "mdy"


def _parse_dt(date_s: str, time_s: str, order: str, tz: ZoneInfo) -> datetime | None:
    parts = [p for p in re.split(r"[./-]", date_s.strip()) if p]
    if len(parts) != 3:
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if order == "ymd":
        y, m, d = nums
    elif order == "dmy":
        d, m, y = nums
    else:
        m, d, y = nums
    if y < 100:
        y += 2000
    t = time_s.strip().upper().replace(".", ":").replace("A:M:", "AM").replace("P:M:", "PM")
    t = t.replace("A:M", "AM").replace("P:M", "PM")
    ampm = None
    if t.endswith("AM") or t.endswith("PM"):
        ampm = t[-2:]
        t = t[:-2].strip()
    bits = [int(x) for x in t.split(":") if x.isdigit()]
    if len(bits) < 2:
        return None
    hh, mm = bits[0], bits[1]
    ss = bits[2] if len(bits) > 2 else 0
    if ampm == "PM" and hh < 12:
        hh += 12
    if ampm == "AM" and hh == 12:
        hh = 0
    try:
        return datetime(y, m, d, hh, mm, ss, tzinfo=tz)
    except ValueError:
        return None


def parse_whatsapp_export(text: str, *, tz_name: str = "America/Toronto") -> list[dict[str, Any]]:
    tz = ZoneInfo(tz_name)
    lines = [_clean_line(l) for l in (text or "").splitlines()]
    heads: list[tuple[int, re.Match[str]]] = []
    for i, line in enumerate(lines):
        m = _IOS.match(line) or _ANDROID.match(line)
        if m:
            heads.append((i, m))
    if not heads:
        return []
    order = _date_order([m.group("date") for _, m in heads[:400]])
    messages: list[dict[str, Any]] = []
    for idx, (i, m) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body = "\n".join([m.group("rest"), *lines[i + 1 : end]]).strip()
        sm = _SENDER.match(body)
        if not sm:
            continue  # system line (no sender)
        sender = sm.group("sender").strip()
        msg = _EDITED.sub("", sm.group("text")).strip()
        low = msg.lower()
        if not msg or any(p in low for p in SKIP_PATTERNS) or low.startswith("<attached:"):
            continue
        dt = _parse_dt(m.group("date"), m.group("time"), order, tz)
        if not dt:
            continue
        iso = dt.isoformat()
        ref = whatsapp_fingerprint(sender, iso, msg)
        messages.append(
            {
                "sender": sender,
                "sent_at": iso,
                "text": msg,
                "ref": ref,
                "substantive": is_substantive(msg),
            }
        )
    return messages


def normalize_wa_text(text: str) -> str:
    t = _EDITED.sub("", _clean_line(text or ""))
    return " ".join(t.split())


def normalize_wa_sender(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _occurred_minute(iso: str) -> str:
    """Wall-clock minute, ignoring seconds and timezone offset."""
    return (iso or "").replace(" ", "T")[:16]


def whatsapp_fingerprint(sender: str, sent_at: str, text: str) -> str:
    """Stable id across full re-exports (seconds, TZ label, hidden chars)."""
    raw = f"{normalize_wa_sender(sender)}|{_occurred_minute(sent_at)}|{normalize_wa_text(text)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def whatsapp_dedupe_keys(sender: str, sent_at: str, text: str, *, source_ref: str | None = None) -> set[str]:
    """All keys that should treat this message as already imported."""
    keys: set[str] = set()
    if source_ref:
        keys.add(source_ref)
    fp = whatsapp_fingerprint(sender, sent_at, text)
    keys.add(f"wa:{fp}")
    keys.add(fp)
    minute = _occurred_minute(sent_at)
    if minute:
        try:
            from datetime import datetime, timedelta

            dt = datetime.fromisoformat(minute)
            for delta in (-1, 0, 1):
                neighbor = (dt + timedelta(minutes=delta)).strftime("%Y-%m-%dT%H:%M")
                keys.add(f"wa:{whatsapp_fingerprint(sender, neighbor, text)}")
        except ValueError:
            pass
    return {k for k in keys if k}


def messages_to_care_events(messages: list[dict[str, Any]], *, chat_name: str = "") -> list[dict[str, Any]]:
    events = []
    for msg in messages:
        events.append(
            {
                "kind": "family_update",
                "occurred_at": msg["sent_at"],
                "summary": msg["text"][:4000],
                "recorded_by": msg["sender"],
                "location": "",
                "source": "whatsapp",
                "source_ref": f"wa:{msg['ref']}",
                "source_document_title": chat_name or None,
                "minor": not msg.get("substantive", True),
            }
        )
    return events


def _decode_chat_text(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    if len(raw) >= 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        try:
            return raw.decode("utf-16-le")
        except UnicodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def text_from_export_zip_bytes(raw: bytes, *, max_text_bytes: int = 5_000_000) -> str:
    """Pull the chat .txt out of a WhatsApp export .zip (media is ignored)."""
    import io
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("That file is not a valid WhatsApp export .zip") from exc
    names = [n for n in zf.namelist() if n.lower().endswith(".txt") and not n.startswith("__MACOSX")]
    if not names:
        raise ValueError("No chat .txt found inside the .zip")
    names.sort(key=lambda n: (0 if n.lower().endswith("_chat.txt") else 1, n))
    info = zf.getinfo(names[0])
    if info.file_size > max_text_bytes:
        raise ValueError("Chat text is too large to import")
    return _decode_chat_text(zf.read(names[0]))


def text_from_export_zip(zip_base64: str, *, max_text_bytes: int = 5_000_000) -> str:
    """Pull the chat .txt out of a WhatsApp export .zip sent as base64."""
    import base64

    raw = base64.b64decode(zip_base64, validate=False)
    return text_from_export_zip_bytes(raw, max_text_bytes=max_text_bytes)


def text_from_export_bytes(raw: bytes, *, max_text_bytes: int = 5_000_000) -> str:
    """Read a WhatsApp export that is either a .txt or a .zip (iPhone default)."""
    if not raw:
        raise ValueError("That file was empty")
    if raw[:2] == b"PK":
        return text_from_export_zip_bytes(raw, max_text_bytes=max_text_bytes)
    text = _decode_chat_text(raw)
    if len(text.encode("utf-8")) > max_text_bytes:
        raise ValueError("Chat text is too large to import")
    return text
