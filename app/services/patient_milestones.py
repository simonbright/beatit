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
    """Attach a stable color to each event (same med keeps one color across start/dose/stop)."""
    out: list[dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        row = dict(ev)
        if row.get("medication_id"):
            key = f"med:{row['medication_id']}"
        else:
            key = f"life:{row.get('id') or row.get('label') or 'milestone'}"
        row["color"] = milestone_color_for(key)
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
    return colorize_milestone_events(merged)
