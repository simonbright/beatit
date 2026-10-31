"""Pull temperature, heart rate, blood pressure, and oxygen from chats and visit notes."""

from __future__ import annotations

import hashlib
import re
from typing import Any

TEMP = re.compile(
    r"\b(?:temp(?:erature)?)\s*[:=]?\s*(\d{2}(?:\.\d{1,2})?)\s*°?\s*([cCfF])?\b"
    r"|"
    r"\b(\d{2}(?:\.\d{1,2})?)\s*°\s*([cCfF])\b",
    re.I,
)
HR = re.compile(
    r"\b(?:hr|heart\s*rate|pulse(?!\s*ox))\s*[:=]?\s*(\d{2,3})\b",
    re.I,
)
BP = re.compile(
    r"\b(?:bp|b\.p\.|blood\s*pressure)\s*[:=]?\s*(\d{2,3})\s*/\s*(\d{2,3})\b"
    r"|"
    r"\b(\d{2,3})\s*/\s*(\d{2,3})\s*mmhg\b",
    re.I,
)
O2 = re.compile(
    r"\b(?:s?p?o2|pulse\s*ox|o2\s*sat(?:uration)?|oxygen(?:\s+sat(?:uration)?)?|sats?)\s*[:=]?\s*(\d{2,3})\s*%?",
    re.I,
)

SOURCE_LABEL = {
    "whatsapp": "From chat",
    "visit": "From visit",
    "transcript": "From visit",
    "note": "From note",
}


def reading_fingerprint(name: str, value: float, recorded_at: str, source_event_id: str = "") -> str:
    key = "|".join(
        [
            (source_event_id or "").strip(),
            re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip(),
            f"{float(value):g}",
            str(recorded_at or "")[:10],
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _plausible_temp(value: float, unit: str) -> bool:
    if unit == "°F":
        return 93 <= value <= 108
    return 34 <= value <= 42


def _guess_temp_unit(value: float, explicit: str | None) -> str | None:
    if explicit:
        return "°F" if explicit.upper() == "F" else "°C"
    if 93 <= value <= 108:
        return "°F"
    if 34 <= value <= 42:
        return "°C"
    return None


def extract_readings(
    text: str,
    *,
    occurred_at: str | None = None,
    source_event_id: str | None = None,
    source_kind: str = "note",
    contact: str | None = None,
) -> list[dict[str, Any]]:
    blob = str(text or "")
    if len(blob) < 4:
        return []
    when = str(occurred_at or "")[:16] or None
    day = (when or "")[:10]
    if day and len(day) < 10:
        day = ""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str, value: float, unit: str) -> None:
        ref = reading_fingerprint(name, value, when or day, source_event_id or "")
        if ref in seen:
            return
        seen.add(ref)
        label = SOURCE_LABEL.get(source_kind, "Extracted")
        who = (contact or "").strip()
        notes = f"{label} · {who}" if who else label
        out.append(
            {
                "name": name,
                "value": value,
                "unit": unit,
                "recorded_at": when or day,
                "category": "vital",
                "notes": notes,
                "source": "extracted",
                "source_kind": source_kind,
                "source_event_id": source_event_id,
                "source_ref": ref,
            }
        )

    for match in TEMP.finditer(blob):
        raw = match.group(1) or match.group(3)
        unit_raw = match.group(2) or match.group(4)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        unit = _guess_temp_unit(value, unit_raw)
        if unit and _plausible_temp(value, unit):
            add("Temperature", value, unit)

    for match in HR.finditer(blob):
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 30 <= value <= 220:
            add("Heart rate", value, "bpm")

    for match in BP.finditer(blob):
        sys_raw = match.group(1) or match.group(3)
        dia_raw = match.group(2) or match.group(4)
        try:
            systolic = float(sys_raw)
            diastolic = float(dia_raw)
        except (TypeError, ValueError):
            continue
        if 70 <= systolic <= 250 and 40 <= diastolic <= 160 and systolic > diastolic:
            add("Systolic BP", systolic, "mmHg")
            add("Diastolic BP", diastolic, "mmHg")

    for match in O2.finditer(blob):
        try:
            value = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 70 <= value <= 100:
            add("Oxygen saturation", value, "%")

    return out


def readings_from_event(event: dict[str, Any], extra_text: str = "") -> list[dict[str, Any]]:
    if not isinstance(event, dict):
        return []
    text = "\n".join(
        part
        for part in (str(event.get("summary") or ""), extra_text)
        if part and part.strip()
    )
    if not text.strip():
        return []
    source = str(event.get("source") or "manual")
    kind = str(event.get("kind") or "")
    source_kind = "whatsapp" if source == "whatsapp" else ("visit" if kind in {"visit", "transcript", "call"} or source == "transcript" else "note")
    who = str(event.get("practitioner") or event.get("recorded_by") or "").strip()
    return extract_readings(
        text,
        occurred_at=str(event.get("occurred_at") or ""),
        source_event_id=str(event.get("id") or "") or None,
        source_kind=source_kind,
        contact=who or None,
    )


def store_extracted_readings(profile: dict[str, Any], incoming: list[dict[str, Any]]) -> int:
    rows = list(profile.get("diagnostics") or [])
    seen_refs = {str(row.get("source_ref") or "") for row in rows if row.get("source_ref")}
    seen_same = {
        (
            str(row.get("name") or "").casefold(),
            f"{float(row['value']):g}" if row.get("value") is not None else "",
            str(row.get("recorded_at") or "")[:10],
        )
        for row in rows
        if row.get("value") is not None
    }
    added = 0
    for raw in incoming:
        ref = str(raw.get("source_ref") or "")
        name = str(raw.get("name") or "").strip()
        try:
            value = float(raw.get("value"))
        except (TypeError, ValueError):
            continue
        day = str(raw.get("recorded_at") or "")[:10]
        same = (name.casefold(), f"{value:g}", day)
        if (ref and ref in seen_refs) or same in seen_same:
            continue
        from uuid import uuid4

        entry = {
            "id": str(uuid4()),
            "name": name,
            "value": value,
            "unit": raw.get("unit"),
            "recorded_at": raw.get("recorded_at") or day,
            "category": "vital",
            "notes": raw.get("notes"),
            "result": None,
            "source": "extracted",
            "source_kind": raw.get("source_kind"),
            "source_event_id": raw.get("source_event_id"),
            "source_ref": ref,
        }
        from app.services.lab_units import enrich_diagnostic_units

        rows.append(enrich_diagnostic_units(entry))
        if ref:
            seen_refs.add(ref)
        seen_same.add(same)
        added += 1
    if added:
        profile["diagnostics"] = rows
    return added
