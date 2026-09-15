"""Patient lifestyle / custom milestones for diagnostic chart overlays."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zlib import crc32

# Distinct, readable palette for dashed overlay lines + badges
MILESTONE_COLORS: list[str] = [
    "#0f766e",  # teal
    "#b45309",  # amber
    "#7c3aed",  # violet
    "#0369a1",  # sky
    "#be123c",  # rose
    "#15803d",  # green
    "#c2410c",  # orange
    "#4338ca",  # indigo
    "#0e7490",  # cyan
    "#a21caf",  # fuchsia
]

MILESTONE_PRESETS: list[dict[str, str]] = [
    {"label": "Exercise regularly", "kind": "exercise"},
    {"label": "Change in diet", "kind": "diet"},
    {"label": "Started sleep routine", "kind": "lifestyle"},
    {"label": "Quit / cut smoking", "kind": "lifestyle"},
    {"label": "Stress management", "kind": "lifestyle"},
    {"label": "Weight goal started", "kind": "lifestyle"},
    {"label": "Travel / schedule change", "kind": "lifestyle"},
    {"label": "Other", "kind": "other"},
]


def _date_only(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date().isoformat()
    except ValueError:
        return None


def _compact_date(iso: str | None) -> str:
    d = _date_only(iso)
    if not d:
        return ""
    try:
        dt = datetime.fromisoformat(d)
    except ValueError:
        return d
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _short_label(text: str, *, max_len: int = 48) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def milestone_color_for(key: str | None) -> str:
    raw = (key or "milestone").encode("utf-8", errors="ignore")
    return MILESTONE_COLORS[crc32(raw) % len(MILESTONE_COLORS)]


def colorize_milestone_events(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Attach distinct colors; same medication name shares a color, lifestyle events get their own."""
    key_index: dict[str, int] = {}

    def _key(row: dict[str, Any]) -> str:
        name = str(row.get("medication_name") or "").strip().lower()
        if name:
            return f"medname:{name}"
        if row.get("medication_id"):
            return f"med:{row['medication_id']}"
        return f"life:{row.get('id') or row.get('label') or 'milestone'}"

    out: list[dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        row = dict(ev)
        key = _key(row)
        if key not in key_index:
            key_index[key] = len(key_index)
        row["color"] = MILESTONE_COLORS[key_index[key] % len(MILESTONE_COLORS)]
        out.append(row)
    return out


def custom_milestones_as_events(milestones: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in milestones or []:
        if not isinstance(row, dict):
            continue
        when = _date_only(row.get("date") or row.get("recorded_at"))
        label_body = str(row.get("label") or "").strip()
        if not when or not label_body:
            continue
        mid = str(row.get("id") or "")
        kind = str(row.get("kind") or "lifestyle")
        body = label_body
        note = str(row.get("notes") or "").strip()
        if note:
            body = f"{label_body} — {note}"
        events.append(
            {
                "id": mid or f"{when}|lifestyle|{label_body}",
                "date": when,
                "label": _short_label(f"{_compact_date(when)} · {body}"),
                "body": body,
                "kind": kind if kind not in {"start", "dose_change", "stop"} else "lifestyle",
                "medication_id": "",
                "medication_name": "",
                "source": "custom",
            }
        )
    return events


def all_chart_milestones(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Medication + custom lifestyle milestones, colored for chart overlays."""
    from app.services.medication_events import medication_chart_events

    profile = profile or {}
    med_events = medication_chart_events(profile.get("medications"))
    custom = custom_milestones_as_events(profile.get("milestones"))
    merged = [*med_events, *custom]
    merged.sort(key=lambda e: (str(e.get("date") or ""), str(e.get("label") or "")))
    return colorize_milestone_events(ensure_milestone_ids(merged))


def ensure_milestone_ids(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Stable ids matching the Labs UI overlay checkboxes."""
    out: list[dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        row = dict(ev)
        if not str(row.get("id") or "").strip():
            when = str(row.get("date") or "")[:10]
            kind = str(row.get("kind") or "")
            body = str(row.get("body") or row.get("label") or "")
            if str(row.get("source") or "") == "custom" or kind not in {
                "start",
                "dose_change",
                "stop",
            }:
                label_body = body.split(" — ", 1)[0].strip() or body
                row["id"] = f"{when}|lifestyle|{label_body}"
            else:
                row["id"] = (
                    f"{when}|{kind}|{row.get('medication_id') or ''}|{body}"
                )
        out.append(row)
    return out


def filter_milestones_by_ids(
    events: list[dict[str, Any]] | None,
    selected_ids: list[str] | None,
) -> list[dict[str, Any]]:
    """Keep events whose id is in selected_ids. Empty selected_ids → none.

    Also matches medication events on ``date|kind|medication_id`` when the
    full id body text differs slightly between client and server.
    """
    rows = ensure_milestone_ids(events)
    if selected_ids is None:
        return rows
    wanted = {str(x) for x in selected_ids if str(x).strip()}
    if not wanted:
        return []
    key_wanted: set[tuple[str, str, str]] = set()
    for sid in wanted:
        parts = str(sid).split("|")
        if len(parts) >= 3 and parts[1] in {"start", "dose_change", "stop"}:
            key_wanted.add((parts[0][:10], parts[1], parts[2]))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ev in rows:
        eid = str(ev.get("id") or "")
        keep = eid in wanted
        if not keep:
            key = (
                str(ev.get("date") or "")[:10],
                str(ev.get("kind") or ""),
                str(ev.get("medication_id") or ""),
            )
            if key[2] and key in key_wanted:
                keep = True
        if not keep:
            continue
        dedupe = eid or f"{ev.get('date')}|{ev.get('kind')}|{ev.get('body')}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        out.append(ev)
    return out
