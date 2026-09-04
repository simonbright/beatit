"""Derive medication start / dose-change / stop milestones for chart overlays."""

from __future__ import annotations

from datetime import date, datetime, timedelta
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
    # Always include year — starts can be many years before the chart range
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _name_key(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


def _parse_day(iso: str | None) -> date | None:
    d = _date_only(iso)
    if not d:
        return None
    try:
        return date.fromisoformat(d)
    except ValueError:
        return None


def _coalesce_stop_start_switches(
    events: list[dict[str, Any]],
    medications: list[dict[str, Any]] | None,
    *,
    max_gap_days: int = 3,
) -> list[dict[str, Any]]:
    """Merge stop + nearby start of the same drug into one dose-change marker."""
    meds_by_id = {
        str(m.get("id") or ""): m for m in (medications or []) if isinstance(m, dict) and m.get("id")
    }
    stops = [e for e in events if e.get("kind") == "stop"]
    starts = [e for e in events if e.get("kind") == "start"]
    used_stop: set[int] = set()
    used_start: set[int] = set()
    merged: list[dict[str, Any]] = []

    for si, stop in enumerate(stops):
        stop_day = _parse_day(stop.get("date"))
        if stop_day is None:
            continue
        stop_name = _name_key(stop.get("medication_name"))
        best: tuple[int, dict[str, Any], int] | None = None  # gap, start, start_index
        for ti, start in enumerate(starts):
            if ti in used_start:
                continue
            if _name_key(start.get("medication_name")) != stop_name:
                continue
            start_day = _parse_day(start.get("date"))
            if start_day is None:
                continue
            gap = (start_day - stop_day).days
            # Allow start 0–max_gap days after stop, or same-day / 1 day early
            if gap < -1 or gap > max_gap_days:
                continue
            abs_gap = abs(gap)
            if best is None or abs_gap < best[0]:
                best = (abs_gap, start, ti)
        if best is None:
            continue
        _, start, ti = best
        used_stop.add(si)
        used_start.add(ti)

        stop_med = meds_by_id.get(str(stop.get("medication_id") or ""))
        start_med = meds_by_id.get(str(start.get("medication_id") or ""))
        old_bits = str((stop_med or {}).get("dosage") or "").strip() or "?"
        new_bits = str((start_med or {}).get("dosage") or "").strip() or "?"
        name = str(stop.get("medication_name") or start.get("medication_name") or "Medication")
        # Prefer the new-regimen date for the chart marker
        when = str(start.get("date") or stop.get("date") or "")
        body = f"{name} {old_bits} → {new_bits}"
        merged.append(
            {
                "date": when,
                "label": _short_label(f"{_compact_date(when)} · {body}"),
                "body": body,
                "kind": "dose_change",
                "medication_id": str(start.get("medication_id") or stop.get("medication_id") or ""),
                "medication_name": name,
                "coalesced_from": "stop_start",
            }
        )

    out: list[dict[str, Any]] = []
    stop_i = 0
    start_i = 0
    for ev in events:
        kind = ev.get("kind")
        if kind == "stop":
            if stop_i in used_stop:
                stop_i += 1
                continue
            stop_i += 1
        elif kind == "start":
            if start_i in used_start:
                start_i += 1
                continue
            start_i += 1
        out.append(ev)
    out.extend(merged)
    return out


def medication_chart_events(
    medications: list[dict[str, Any]] | None,
    *,
    max_events: int = 40,
) -> list[dict[str, Any]]:
    """Build dated milestone markers from medication records.

    Each event: ``{date, label, kind, medication_id, medication_name}``
    where ``kind`` is ``start`` | ``dose_change`` | ``stop``.

    A stop followed soon by a start of the same drug is coalesced into one
    ``dose_change`` (e.g. Rosuvastatin 20mg → 40mg), not two chart markers.
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
                    "body": body,
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
                    "body": body,
                    "kind": "dose_change",
                    "medication_id": med_id,
                    "medication_name": name,
                }
            )

        stopped = _date_only(med.get("stopped_at"))
        if stopped and (med.get("status") or "") == "stopped":
            body = f"Stopped {name}"
            events.append(
                {
                    "date": stopped,
                    "label": _short_label(f"{_compact_date(stopped)} · {body}"),
                    "body": body,
                    "kind": "stop",
                    "medication_id": med_id,
                    "medication_name": name,
                }
            )

    events = _coalesce_stop_start_switches(events, medications)

    kind_order = {"stop": 0, "dose_change": 1, "start": 2}
    events.sort(
        key=lambda e: (
            e.get("date") or "",
            kind_order.get(str(e.get("kind") or ""), 9),
            e.get("label") or "",
        )
    )
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
