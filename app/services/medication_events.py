"""Derive medication start / dose-change / stop milestones for chart overlays."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _date_only(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date().isoformat()
    except ValueError:
        return None


def _dose_bits(dosage: Any, frequency: Any) -> str:
    bits = [str(x).strip() for x in (dosage, frequency) if x and str(x).strip()]
    return " · ".join(bits)


def _short_label(text: str, *, max_len: int = 48) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _compact_date(iso: str | None) -> str:
    d = _date_only(iso)
    if not d:
        return ""
    try:
        dt = datetime.fromisoformat(d)
    except ValueError:
        return d
    return f"{dt.strftime('%b')} {dt.day}"


def medication_chart_events(
    medications: list[dict[str, Any]] | None,
    *,
    max_events: int = 40,
) -> list[dict[str, Any]]:
    """Build dated milestone markers from medication records.

    Each event: ``{date, label, kind, medication_id, medication_name}``
    where ``kind`` is ``start`` | ``dose_change`` | ``stop``.
    Labels include a compact date so chart markers stay unambiguous.
    """
    events: list[dict[str, Any]] = []
    for med in medications or []:
        if not isinstance(med, dict):
            continue
        name = str(med.get("name") or "").strip() or "Medication"
        med_id = str(med.get("id") or "")
        history = [h for h in (med.get("dosage_history") or []) if isinstance(h, dict)]

        started = _date_only(med.get("started_at"))
        if started:
            if history:
                initial = _dose_bits(history[0].get("dosage"), history[0].get("frequency"))
            else:
                initial = _dose_bits(med.get("dosage"), med.get("frequency"))
            body = f"Started {name}" + (f" {initial}" if initial else "")
            events.append(
                {
                    "date": started,
                    "label": _short_label(f"{_compact_date(started)} · {body}"),
                    "kind": "start",
                    "medication_id": med_id,
                    "medication_name": name,
                }
            )

        for i, row in enumerate(history):
            effective = _date_only(row.get("effective_at")) or _date_only(row.get("changed_at"))
            if not effective:
                continue
            old_bits = _dose_bits(row.get("dosage"), row.get("frequency")) or "?"
            if i + 1 < len(history):
                nxt = history[i + 1]
                new_bits = _dose_bits(nxt.get("dosage"), nxt.get("frequency")) or "?"
            else:
                new_bits = _dose_bits(med.get("dosage"), med.get("frequency")) or "?"
            note = str(row.get("note") or "").strip()
            if note:
                body = f"{name}: {note}"
            else:
                body = f"{name}: {old_bits} → {new_bits}"
            events.append(
                {
                    "date": effective,
                    "label": _short_label(f"{_compact_date(effective)} · {body}"),
                    "kind": "dose_change",
                    "medication_id": med_id,
                    "medication_name": name,
                }
            )

        stopped = _date_only(med.get("stopped_at"))
        if stopped and (med.get("status") or "") == "stopped":
            events.append(
                {
                    "date": stopped,
                    "label": _short_label(f"{_compact_date(stopped)} · Stopped {name}"),
                    "kind": "stop",
                    "medication_id": med_id,
                    "medication_name": name,
                }
            )

    _kind_order = {"stop": 0, "dose_change": 1, "start": 2}
    events.sort(
        key=lambda e: (
            e.get("date") or "",
            _kind_order.get(str(e.get("kind") or ""), 9),
            e.get("label") or "",
        )
    )
    # De-dupe identical date+label pairs
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for ev in events:
        key = (str(ev.get("date") or ""), str(ev.get("label") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    return unique[:max_events]


def filter_events_for_range(
    events: list[dict[str, Any]] | None,
    *,
    start: str | None,
    end: str | None,
    pad_days: int = 14,
) -> list[dict[str, Any]]:
    """Keep milestones that fall within [start - pad, end + pad]."""
    if not events or not start or not end:
        return []
    try:
        from datetime import date, timedelta

        d0 = date.fromisoformat(start[:10]) - timedelta(days=pad_days)
        d1 = date.fromisoformat(end[:10]) + timedelta(days=pad_days)
    except ValueError:
        return []
    out: list[dict[str, Any]] = []
    for ev in events:
        d = _date_only(ev.get("date"))
        if not d:
            continue
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        if d0 <= dd <= d1:
            out.append(ev)
    return out
