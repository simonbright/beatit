"""Rule-based log observations (frequency + time-of-day patterns)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.pdf_export import (
    filter_journal_for_export,
    journal_export_days_label,
    normalize_journal_export_days,
)

EASTERN = ZoneInfo("America/New_York")

POSITIVE_LABELS = frozenset(
    {
        "feel fine",
        "feeling good",
        "better",
        "ok / normal",
        "energetic",
        "pain-free",
    }
)

# One-tap noise: only surface if they hit a higher count threshold.
NOISY_LABELS = frozenset(
    {
        "water",
        "slept",
        "bathroom #2",
        "took shower",
        "shower",
        "ate/drank",
        "note",
    }
)

_SITE_SUFFIX_RE = re.compile(
    r"\s*[·•\-–—]\s*(upper|mid|lower|left|right|both|across|"
    r"left side|right side|whole back)\s*$",
    re.IGNORECASE,
)


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_eastern(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN)


def base_journal_label(label: str) -> str:
    cleaned = " ".join((label or "").strip().split())
    if not cleaned:
        return ""
    stripped = _SITE_SUFFIX_RE.sub("", cleaned).strip()
    return stripped or cleaned


def _format_clock(minutes: int) -> str:
    minutes = int(minutes) % (24 * 60)
    hour24 = minutes // 60
    minute = minutes % 60
    suffix = "AM" if hour24 < 12 else "PM"
    hour12 = hour24 % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"


def _range_phrase(days: int | str) -> str:
    if days == "all":
        return "across all logs"
    n = int(days)
    if n == 1:
        return "today"
    return f"in the past {n} days"


def _min_count_for_days(days: int | str) -> int:
    if days == "all":
        return 3
    n = int(days)
    if n >= 7:
        return 3
    return 2


def _is_interesting_entry(entry: dict[str, Any], base: str) -> bool:
    kind = str(entry.get("kind") or "note").strip().lower()
    key = base.lower()
    if key in POSITIVE_LABELS:
        return False
    if kind in {"symptom", "feeling"}:
        return True
    if entry.get("severity") is not None:
        return True
    if key in NOISY_LABELS:
        return True  # still group; threshold filters later
    if kind in {"medication", "note"}:
        return True
    return False


def _noisy_threshold(days: int | str, base: str) -> int:
    if base.lower() not in NOISY_LABELS:
        return _min_count_for_days(days)
    # Require more signal for routine one-taps
    base_min = _min_count_for_days(days)
    return max(base_min + 1, 4 if days == "all" or (isinstance(days, int) and days >= 7) else 3)


def _best_time_cluster(minutes_list: list[int]) -> tuple[int, int, int] | None:
    """Return (hit_count, start_min, end_min) for densest ≤3h window, or None."""
    if len(minutes_list) < 2:
        return None
    vals = sorted(minutes_list)
    window = 3 * 60
    best_count = 0
    best_start = vals[0]
    best_end = vals[0]
    left = 0
    for right, end in enumerate(vals):
        while end - vals[left] > window:
            left += 1
        count = right - left + 1
        if count > best_count:
            best_count = count
            best_start = vals[left]
            best_end = end
    if best_count < 2:
        return None
    if best_count / len(vals) < 0.6:
        return None
    # Expand single-instant cluster slightly for readability
    if best_end == best_start:
        best_end = min(best_start + 30, 24 * 60 - 1)
    return best_count, best_start, best_end


def _named_bucket(minutes_list: list[int]) -> tuple[str, int] | None:
    """If ≥60% fall in a named day-part, return (label, count)."""
    if len(minutes_list) < 2:
        return None
    buckets = {
        "morning (5:00 AM–12:00 PM)": 0,
        "afternoon (12:00–5:00 PM)": 0,
        "evening (5:00–10:00 PM)": 0,
        "night (10:00 PM–5:00 AM)": 0,
    }
    for m in minutes_list:
        hour = (m // 60) % 24
        if 5 <= hour < 12:
            buckets["morning (5:00 AM–12:00 PM)"] += 1
        elif 12 <= hour < 17:
            buckets["afternoon (12:00–5:00 PM)"] += 1
        elif 17 <= hour < 22:
            buckets["evening (5:00–10:00 PM)"] += 1
        else:
            buckets["night (10:00 PM–5:00 AM)"] += 1
    label, count = max(buckets.items(), key=lambda kv: kv[1])
    if count < 2 or count / len(minutes_list) < 0.6:
        return None
    return label, count


def _severity_phrase(entries: list[dict[str, Any]]) -> str | None:
    sevs: list[int] = []
    for e in entries:
        raw = e.get("severity")
        if raw is None:
            continue
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 5:
            sevs.append(v)
    if not sevs:
        return None
    avg = sum(sevs) / len(sevs)
    lo, hi = min(sevs), max(sevs)
    if lo == hi:
        whole = f"{avg:.1f}".replace(".0", "")
        return f"avg {whole}/5"
    return f"avg {avg:.1f}/5 (range {lo}–{hi})"


def _build_observation_text(
    *,
    label: str,
    count: int,
    days: int | str,
    entries: list[dict[str, Any]],
) -> str:
    parts = [f"{count}× {label} {_range_phrase(days)}"]

    minutes: list[int] = []
    for e in entries:
        dt = _parse_iso(str(e.get("recorded_at") or e.get("created_at") or ""))
        if not dt:
            continue
        eastern = _to_eastern(dt)
        minutes.append(eastern.hour * 60 + eastern.minute)

    cluster = _best_time_cluster(minutes)
    if cluster:
        hit, start, end = cluster
        parts.append(f"{hit} of {count} between {_format_clock(start)}–{_format_clock(end)}")
    else:
        bucket = _named_bucket(minutes)
        if bucket:
            name, hit = bucket
            parts.append(f"{hit} of {count} in the {name}")

    sev = _severity_phrase(entries)
    if sev:
        parts.append(sev)

    return " · ".join(parts)


def build_log_observations(
    entries: list[dict[str, Any]] | None,
    days: int | str = 1,
    *,
    now: datetime | None = None,
    max_items: int = 5,
) -> list[dict[str, Any]]:
    """Return up to max_items structured observations for the given day window."""
    days_key = normalize_journal_export_days(days)
    rows = filter_journal_for_export(entries, days_key, now=now)
    groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        label_raw = str(row.get("label") or "").strip()
        base = base_journal_label(label_raw)
        if not base or not _is_interesting_entry(row, base):
            continue
        key = base.lower()
        group = groups.get(key)
        if not group:
            group = {
                "label": base,
                "kind": str(row.get("kind") or "note"),
                "entries": [],
            }
            groups[key] = group
        kind = str(row.get("kind") or "note")
        if kind in {"symptom", "feeling"} and group["kind"] not in {"symptom", "feeling"}:
            group["kind"] = kind
        group["entries"].append(row)

    scored: list[dict[str, Any]] = []
    for key, group in groups.items():
        count = len(group["entries"])
        threshold = _noisy_threshold(days_key, group["label"])
        if count < threshold:
            continue
        text = _build_observation_text(
            label=group["label"],
            count=count,
            days=days_key,
            entries=group["entries"],
        )
        sevs: list[int] = []
        for e in group["entries"]:
            raw = e.get("severity")
            if raw is None:
                continue
            try:
                v = int(raw)
            except (TypeError, ValueError):
                continue
            if 1 <= v <= 5:
                sevs.append(v)
        avg_sev = sum(sevs) / len(sevs) if sevs else 0.0
        scored.append(
            {
                "id": f"obs:{key}",
                "text": text,
                "label": group["label"],
                "count": count,
                "kind": group["kind"],
                "_rank": (count, avg_sev),
            }
        )

    scored.sort(key=lambda o: (o["_rank"][0], o["_rank"][1], o["label"]), reverse=True)
    out: list[dict[str, Any]] = []
    for item in scored[: max(1, max_items)]:
        item.pop("_rank", None)
        out.append(item)
    return out


def log_observations_payload(
    entries: list[dict[str, Any]] | None,
    days: int | str = 1,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    days_key = normalize_journal_export_days(days)
    return {
        "days": days_key,
        "days_label": journal_export_days_label(days_key),
        "observations": build_log_observations(entries, days_key, now=now),
    }
