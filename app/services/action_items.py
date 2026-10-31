"""Pull concrete to-dos out of chats and visit notes onto the family plan."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.care_timeline import parse_event_datetime

MAX_TITLE = 120
MAX_SCAN_EVENTS = 80
MAX_LLM_CHARS = 12_000
DEFAULT_TZ = ZoneInfo("America/Toronto")

CLINICAL_KINDS = frozenset(
    {"visit", "call", "family_meeting", "transcript", "procedure", "result"}
)

ACTION_CUE = re.compile(
    r"\b("
    r"follow[- ]?up|book|schedule|arrange|call|phone|"
    r"ask(?:\s+the)?\s+(?:dr|doctor|oncologist|nurse)|"
    r"bring|start|stop|hold|increase|decrease|taper|"
    r"order|refer|see(?:\s+the)?\s+(?:dr|doctor)|"
    r"appointment|return|come back|repeat|"
    r"need(?:s)? to|please|must|to[- ]do|action item|"
    r"next step|plan is to|we will|will send|"
    r"send(?:\s+the)?\s+(?:results?|labs?|scan|report|cd|disc)|"
    r"pick up|fill(?:\s+the)?\s+prescription"
    r")\b",
    re.I,
)

WHATSAPP_VERB = re.compile(
    r"\b(follow[- ]?up|book|schedule|call|phone|need(?:s)? to|please|"
    r"send|pick up|start|stop|ask|bring|fill)\b",
    re.I,
)

PLAN_HEADING = re.compile(
    r"^(?:plan|next steps?|to[- ]dos?|action items?|follow[- ]ups?|instructions?)\s*:?\s*$",
    re.I,
)

SKIP_LINE = re.compile(
    r"^(thanks|thank you|ok|okay|yes|no|lol|haha|love you|good night|gm|morning)\b",
    re.I,
)

ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
NAMED_DATE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?\b",
    re.I,
)
IN_DURATION = re.compile(
    r"\b(?:in|within)\s+(\d+|a|an|one|two|three)\s+(day|days|week|weeks)\b",
    re.I,
)
RELATIVE = re.compile(r"\b(today|tonight|tomorrow|tmrw)\b", re.I)
WEEKDAY = re.compile(
    r"\b(?:on\s+|this\s+|next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I,
)

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}

KIND_HINTS = (
    (re.compile(r"\b(ct|mri|ultrasound|x-?ray|scan|imaging|biopsy)\b", re.I), "exam"),
    (re.compile(r"\b(lab|blood|culture|cbc|inr)\b", re.I), "diagnostic"),
    (re.compile(r"\b(appointment|clinic|see|return|come back)\b", re.I), "appointment"),
    (re.compile(r"\b(call|phone|book|schedule)\b", re.I), "call"),
    (re.compile(r"\b(send|fax|forward|share|portal)\b", re.I), "send"),
    (re.compile(r"\b(follow[- ]?up)\b", re.I), "follow_up"),
)

VISIT_ACTIONS_SYSTEM = """Extract concrete family to-dos from these visit notes.
Reply with a JSON array only. Each object:
{"title":"short action","due":"YYYY-MM-DD or null","kind":"exam|diagnostic|appointment|call|send|follow_up|other","who":""}
Only things someone must do (book, call, bring, start/stop a medicine, send results, follow up).
Use only facts in the notes. Empty array if none."""


def action_item_fingerprint(
    title: str,
    *,
    due_at: str | None = None,
    source_event_id: str | None = None,
) -> str:
    key = "|".join(
        [
            (source_event_id or "").strip(),
            re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip(),
            (due_at or "").strip(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _anchor(occurred_at: str | None) -> datetime:
    try:
        raw = parse_event_datetime(occurred_at) if occurred_at else ""
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DEFAULT_TZ)
        return dt.astimezone(DEFAULT_TZ)
    except Exception:
        return datetime.now(DEFAULT_TZ)


def _iso(day: datetime) -> str:
    return day.date().isoformat()


def resolve_due(text: str, *, occurred_at: str | None = None) -> str | None:
    """Best-effort due date from a sentence, relative to when it was said."""
    anchor = _anchor(occurred_at)
    iso = ISO_DATE.search(text or "")
    if iso:
        return iso.group(1)
    named = NAMED_DATE.search(text or "")
    if named:
        month = MONTHS.get(named.group(1).lower())
        day = int(named.group(2))
        year = int(named.group(3) or anchor.year)
        if month:
            try:
                due = datetime(year, month, day, tzinfo=DEFAULT_TZ)
            except ValueError:
                due = None
            if due:
                if due.date() < anchor.date() and not named.group(3):
                    try:
                        due = datetime(year + 1, month, day, tzinfo=DEFAULT_TZ)
                    except ValueError:
                        due = None
                if due:
                    return _iso(due)
    duration = IN_DURATION.search(text or "")
    if duration:
        raw_n, unit = duration.group(1), duration.group(2).lower()
        n = WORD_NUM.get(raw_n.lower()) or int(raw_n)
        delta = timedelta(weeks=n) if unit.startswith("week") else timedelta(days=n)
        return _iso(anchor + delta)
    rel = RELATIVE.search(text or "")
    if rel:
        word = rel.group(1).lower()
        if word in {"today", "tonight"}:
            return _iso(anchor)
        return _iso(anchor + timedelta(days=1))
    weekday = WEEKDAY.search(text or "")
    if weekday:
        target = WEEKDAY_INDEX[weekday.group(1).lower()]
        ahead = (target - anchor.weekday()) % 7
        if ahead == 0:
            ahead = 7 if re.search(r"\bnext\b", text or "", re.I) else 0
        return _iso(anchor + timedelta(days=ahead))
    return None


def infer_kind(text: str) -> str:
    for pattern, kind in KIND_HINTS:
        if pattern.search(text or ""):
            return kind
    return "other"


def _clean_title(line: str) -> str:
    title = re.sub(r"^[\s>*\-•–—\d\.\)\(]+", "", line or "").strip()
    title = re.sub(
        r"^(please\s+|we (?:will|should|need to)\s+|need(?:s)? to\s+|they (?:will|want us to)\s+)",
        "",
        title,
        flags=re.I,
    )
    title = " ".join(title.split())
    if title and title[-1] not in ".!?":
        title = title.rstrip(" ,;")
    if len(title) > MAX_TITLE:
        title = title[: MAX_TITLE - 1].rsplit(" ", 1)[0].rstrip(" ,;") + "…"
    return title


def _split_lines(text: str) -> list[str]:
    chunks: list[str] = []
    protected = re.sub(r"\b(Dr|Mr|Mrs|Ms|Prof)\.", r"\1·", text or "", flags=re.I)
    for raw in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z“\"])", protected):
        line = " ".join(raw.replace("·", ".").split()).strip(" -•")
        if line:
            chunks.append(line)
    return chunks


def extract_action_items(
    text: str,
    *,
    occurred_at: str | None = None,
    source_event_id: str | None = None,
    source_kind: str = "note",
    contact: str | None = None,
    aggressive: bool = False,
) -> list[dict[str, Any]]:
    """Rule-based to-dos. Aggressive mode is for visit / doctor notes."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    in_plan = False
    for line in _split_lines(text):
        if PLAN_HEADING.match(line.rstrip(":")):
            in_plan = True
            continue
        if SKIP_LINE.match(line) or len(line) < 8:
            continue
        if not aggressive and not ACTION_CUE.search(line):
            continue
        if aggressive and not in_plan and not ACTION_CUE.search(line):
            continue
        title = _clean_title(line)
        if len(title) < 8:
            continue
        due = resolve_due(line, occurred_at=occurred_at)
        ref = action_item_fingerprint(title, due_at=due, source_event_id=source_event_id)
        if ref in seen:
            continue
        seen.add(ref)
        items.append(
            {
                "title": title,
                "kind": infer_kind(line),
                "due_at": due,
                "contact": (contact or "")[:120] or None,
                "notes": f"From {source_kind.replace('_', ' ')}",
                "source": "extracted",
                "source_ref": ref,
                "source_event_id": source_event_id,
                "source_kind": source_kind,
            }
        )
    return items


def items_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(event, dict):
        return []
    text = str(event.get("summary") or "").strip()
    if not text:
        return []
    source = str(event.get("source") or "manual")
    kind = str(event.get("kind") or "")
    if source == "whatsapp" and event.get("minor"):
        return []
    aggressive = kind in CLINICAL_KINDS or source == "transcript"
    source_kind = "whatsapp" if source == "whatsapp" else ("visit" if aggressive else "note")
    who = str(event.get("practitioner") or event.get("recorded_by") or "").strip()
    items = extract_action_items(
        text,
        occurred_at=str(event.get("occurred_at") or ""),
        source_event_id=str(event.get("id") or "") or None,
        source_kind=source_kind,
        contact=who or None,
        aggressive=aggressive,
    )
    if source == "whatsapp":
        items = [
            item
            for item in items
            if len(item.get("title") or "") <= 140 and WHATSAPP_VERB.search(item.get("title") or "")
        ]
    return items


def prune_noisy_extracted_items(profile: dict[str, Any]) -> int:
    """Drop chat extracts that are really just long messages."""
    items = list(profile.get("care_items") or [])
    kept: list[dict[str, Any]] = []
    removed = 0
    for row in items:
        if row.get("source") == "extracted" and row.get("source_kind") == "whatsapp":
            title = str(row.get("title") or "")
            if len(title) > 140 or not WHATSAPP_VERB.search(title):
                removed += 1
                continue
        kept.append(row)
    if removed:
        profile["care_items"] = kept
    return removed


def _store_items(profile: dict[str, Any], incoming: list[dict[str, Any]]) -> int:
    from app.services.case_manager import _normalize_care_item

    items = list(profile.get("care_items") or [])
    seen = {str(row.get("source_ref") or "") for row in items if row.get("source_ref")}
    seen |= {
        action_item_fingerprint(str(row.get("title") or ""), due_at=row.get("due_at"))
        for row in items
    }
    added = 0
    for raw in incoming:
        ref = str(raw.get("source_ref") or "")
        if ref and ref in seen:
            continue
        title_key = action_item_fingerprint(str(raw.get("title") or ""), due_at=raw.get("due_at"))
        if title_key in seen:
            continue
        entry = _normalize_care_item(raw)
        if not entry:
            continue
        for key in ("source", "source_ref", "source_event_id", "source_kind"):
            if raw.get(key):
                entry[key] = raw[key]
        items.append(entry)
        if ref:
            seen.add(ref)
        seen.add(title_key)
        added += 1
    if added:
        profile["care_items"] = items
    return added


def harvest_event_actions(
    patient_id: str,
    event: dict[str, Any],
    *,
    extra_text: str = "",
    extra_items: list[dict[str, Any]] | None = None,
) -> int:
    """Extract and persist to-dos from one care event. Returns how many were added."""
    from app.services.case_manager import get_patient_profile, save_patient_profile

    profile = get_patient_profile(patient_id)
    incoming = items_from_event(event)
    if extra_text:
        incoming.extend(
            extract_action_items(
                extra_text,
                occurred_at=str(event.get("occurred_at") or ""),
                source_event_id=str(event.get("id") or "") or None,
                source_kind="visit",
                contact=str(event.get("practitioner") or "") or None,
                aggressive=True,
            )
        )
    incoming.extend(extra_items or [])
    added = _store_items(profile, incoming)
    events = list(profile.get("care_events") or [])
    for i, row in enumerate(events):
        if row.get("id") == event.get("id"):
            events[i] = {**row, "actions_scanned": True}
            profile["care_events"] = events
            break
    save_patient_profile(patient_id, profile, reload=False)
    return added


def harvest_unscanned_events(patient_id: str, *, limit: int = MAX_SCAN_EVENTS) -> int:
    """Backfill to-dos from events that have not been scanned yet (rules only)."""
    from app.services.case_manager import get_patient_profile, save_patient_profile

    profile = get_patient_profile(patient_id)
    pruned = prune_noisy_extracted_items(profile)
    events = list(profile.get("care_events") or [])
    added = 0
    scanned = 0
    changed = pruned > 0
    for i, event in enumerate(events):
        if event.get("actions_scanned"):
            continue
        if scanned >= limit:
            break
        incoming = items_from_event(event)
        added += _store_items(profile, incoming)
        events[i] = {**event, "actions_scanned": True}
        scanned += 1
        changed = True
    if changed:
        profile["care_events"] = events
        save_patient_profile(patient_id, profile, reload=False)
    return added


async def extract_visit_actions_llm(
    text: str,
    *,
    llm: Any | None,
    occurred_at: str | None = None,
    who: str = "",
    role: str = "",
    source_event_id: str | None = None,
) -> list[dict[str, Any]]:
    if not llm or not (text or "").strip():
        return []
    body = (text or "").strip()
    if len(body) > MAX_LLM_CHARS:
        body = body[:MAX_LLM_CHARS].rsplit(" ", 1)[0] + "\n…"
    prompt = f"Visit with: {who}{f' ({role})' if role else ''}\nDate: {occurred_at or ''}\n\n{body}"
    try:
        raw = await llm.generate(prompt=prompt, system=VISIT_ACTIONS_SYSTEM, temperature=0.1)
    except Exception:
        return []
    blob = (raw or "").strip()
    start, end = blob.find("["), blob.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(blob[start : end + 1])
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        title = _clean_title(str(row.get("title") or ""))
        if len(title) < 8:
            continue
        due = resolve_due(str(row.get("due") or title), occurred_at=occurred_at)
        if row.get("due") and ISO_DATE.search(str(row.get("due"))):
            due = ISO_DATE.search(str(row.get("due"))).group(1)  # type: ignore[union-attr]
        kind = str(row.get("kind") or infer_kind(title)).strip().lower()
        if kind not in {"exam", "diagnostic", "appointment", "call", "send", "follow_up", "other"}:
            kind = infer_kind(title)
        contact = " ".join(str(row.get("who") or who or "").split())[:120] or None
        out.append(
            {
                "title": title,
                "kind": kind,
                "due_at": due,
                "contact": contact,
                "notes": "From visit notes",
                "source": "extracted",
                "source_ref": action_item_fingerprint(
                    title, due_at=due, source_event_id=source_event_id
                ),
                "source_event_id": source_event_id,
                "source_kind": "visit",
            }
        )
    return out
