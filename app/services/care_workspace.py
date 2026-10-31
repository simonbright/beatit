"""Family care workspace: who has the ball, visit close-out, and the Ask brief."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from app.services.care_timeline import hospital_stay, normalize_care_event_kind

BALL_KINDS = (
    ("basketball", "🏀", "Basketball"),
    ("soccer", "⚽", "Soccer"),
    ("football", "🏈", "Football"),
    ("baseball", "⚾", "Baseball"),
    ("tennis", "🎾", "Tennis"),
    ("volleyball", "🏐", "Volleyball"),
    ("rugby", "🏉", "Rugby"),
    ("softball", "🥎", "Softball"),
)
DEFAULT_BALL_KIND = "basketball"
_BALL_KIND_KEYS = {key for key, _emoji, _label in BALL_KINDS}
_BALL_KIND_ALIASES = {
    "hoops": "basketball",
    "nba": "basketball",
    "futbol": "soccer",
    "football_soccer": "soccer",
    "american_football": "football",
    "nfl": "football",
}


def normalize_ball_kind(raw: Any) -> str:
    key = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = _BALL_KIND_ALIASES.get(key, key)
    return key if key in _BALL_KIND_KEYS else DEFAULT_BALL_KIND


def ball_kind_emoji(kind: Any) -> str:
    wanted = normalize_ball_kind(kind)
    return next((emoji for key, emoji, _label in BALL_KINDS if key == wanted), "🏀")


def ball_kinds_payload() -> list[dict[str, str]]:
    return [{"value": key, "emoji": emoji, "label": label} for key, emoji, label in BALL_KINDS]


PROFESSIONAL_VISIT_KINDS = frozenset(
    {"visit", "call", "family_meeting", "transcript", "procedure"}
)

CARE_STARTER_PROMPTS = [
    "Who has the ball?",
    "Which items are open?",
    "What is the plan for discharge?",
    "Who is the Internal Medicine care team / attending?",
    "What happened at the most recent staff visit, and what is due next?",
]

INTERNAL_MEDICINE_HINTS = (
    "internal medicine",
    "internist",
    "hospitalist",
    "attending",
    "im team",
    "general internal",
    "general medicine",
)

DISCHARGE_HINTS = (
    "discharge",
    "discharged",
    "going home",
    "home plan",
    "disposition",
    "out of hospital",
    "step down",
    "rehab",
)


def is_professional_visit_kind(kind: str | None) -> bool:
    return normalize_care_event_kind(kind) in PROFESSIONAL_VISIT_KINDS


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _day(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _due(value: Any) -> str:
    """Date, or date plus time when one was recorded."""
    text = str(value or "").strip().replace("Z", "").replace(" ", "T", 1)
    day = _day(text)
    if not day:
        return ""
    if len(text) >= 16 and text[10] == "T":
        try:
            datetime.strptime(text[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            return day
        return text[:16]
    return day


def _holder_stamp(value: Any, *, end_of_day: bool = False) -> str:
    text = _due(value)
    if not text:
        return ""
    if "T" in text:
        return text
    return f"{text}T23:59" if end_of_day else f"{text}T00:00"


def normalize_ball_holder(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name") or raw.get("person"), 120)
    if not name:
        return None
    holder_id = str(raw.get("id") or "").strip() or str(uuid4())
    since = _due(raw.get("since") or raw.get("from") or raw.get("as_of"))
    until = _due(raw.get("until") or raw.get("through"))
    return {
        "id": holder_id,
        "name": name,
        "title": _clean(raw.get("title"), 80),
        "department": _clean(raw.get("department") or raw.get("dept"), 80),
        "role": _clean(raw.get("role") or raw.get("why") or raw.get("expect") or raw.get("what"), 240),
        "success": _clean(raw.get("success") or raw.get("done_when") or raw.get("outcome"), 240),
        "since": since or None,
        "until": until or None,
        "notes": _clean(raw.get("notes"), 240),
        "lead": bool(raw.get("lead") or raw.get("call_first") or raw.get("primary")),
    }


def normalize_ball_checkin(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    who = _clean(raw.get("who") or raw.get("name") or raw.get("person"), 120)
    what = _clean(raw.get("what") or raw.get("role") or raw.get("expect") or raw.get("cover"), 240)
    at = _due(raw.get("at") or raw.get("when") or raw.get("due_at") or raw.get("checkin"))
    if not who and not what and not at:
        return None
    return {"who": who, "what": what, "at": at or ""}


def normalize_ball_holders(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        holder = normalize_ball_holder(row)
        if not holder or holder["id"] in seen:
            continue
        seen.add(holder["id"])
        out.append(holder)
    lead_used = False
    for holder in out:
        if holder["lead"] and not lead_used:
            lead_used = True
        else:
            holder["lead"] = False
    out.sort(key=lambda h: (0 if h.get("lead") else 1, h.get("since") or "9999", h["name"].lower()))
    return out[:12]


def current_ball_holders(
    raw: Any,
    *,
    today: str | None = None,
) -> list[dict[str, Any]]:
    """People in charge right now — until date/time has not passed."""
    if today:
        now = _due(today) or datetime.now().strftime("%Y-%m-%dT%H:%M")
        if "T" not in now:
            now = f"{now}T23:59"
    else:
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    out: list[dict[str, Any]] = []
    for holder in normalize_ball_holders(raw):
        until = holder.get("until")
        if until and _holder_stamp(until, end_of_day=True) < now:
            continue
        out.append(holder)
    return out


def normalize_visit_actions(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        title = _clean(row.get("title") or row.get("action") or row.get("what"), 160)
        due = _due(row.get("due_at") or row.get("due") or row.get("by"))
        if not title:
            continue
        key = (title.lower(), due)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "title": title,
                "due_at": due,
                "notes": _clean(row.get("notes"), 400),
            }
        )
    return out[:12]


def complete_visit_actions(actions: list[dict[str, str]] | None) -> list[dict[str, str]]:
    return [a for a in (actions or []) if a.get("title")]


def _role_is_internal_medicine(role: str) -> bool:
    low = (role or "").lower()
    return any(hint in low for hint in INTERNAL_MEDICINE_HINTS)


def find_internal_medicine_team(
    care_team: list[dict[str, Any]] | None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    named: list[dict[str, str]] = []
    unnamed_roles: list[str] = []
    seen: set[str] = set()
    for member in care_team or []:
        role = str(member.get("role") or "")
        name = str(member.get("name") or "").strip()
        if not _role_is_internal_medicine(role) and not _role_is_internal_medicine(name):
            continue
        key = name.lower() or role.lower()
        if key in seen:
            continue
        seen.add(key)
        if name:
            named.append({"name": name, "role": role})
        elif role:
            unnamed_roles.append(role)
    for ev in events or []:
        role = str(ev.get("role") or "")
        name = str(ev.get("practitioner") or "").strip()
        if not _role_is_internal_medicine(role):
            continue
        key = name.lower() or role.lower()
        if key in seen:
            continue
        seen.add(key)
        if name:
            named.append({"name": name, "role": role})
        elif role:
            unnamed_roles.append(role)
    return {"named": named, "unnamed_roles": unnamed_roles}


def _format_holder(holder: dict[str, Any]) -> str:
    bits = [holder["name"]]
    title = holder.get("title") or ""
    department = holder.get("department") or ""
    if title and department:
        bits.append(f"({title}, {department})")
    elif title or department:
        bits.append(f"({title or department})")
    if holder.get("role"):
        bits.append(f"— do: {holder['role']}")
    if holder.get("success"):
        bits.append(f"— success: {holder['success']}")
    if holder.get("lead"):
        bits.append("— call first")
    if holder.get("since"):
        bits.append(f"from {holder['since']}")
    if holder.get("until"):
        bits.append(f"to {holder['until']}")
    if holder.get("notes"):
        bits.append(f"— {holder['notes']}")
    return " ".join(bits)


def _open_items(profile: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for item in profile.get("care_items") or []:
        if item.get("status") != "open":
            continue
        items.append(item)
    items.sort(key=lambda i: (i.get("due_at") or "9999", i.get("title") or ""))
    return items


def _recent_visits(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visits = [
        ev
        for ev in events
        if is_professional_visit_kind(ev.get("kind")) and ev.get("source") != "whatsapp"
    ]
    visits.sort(key=lambda ev: str(ev.get("occurred_at") or ev.get("created_at") or ""), reverse=True)
    return visits[:12]


def _discharge_mentions(events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for ev in events:
        kind = ev.get("kind") or ""
        text = " ".join(
            [
                str(ev.get("summary") or ""),
                str(ev.get("practitioner") or ""),
                str(ev.get("role") or ""),
            ]
        ).lower()
        if kind == "discharge" or any(hint in text for hint in DISCHARGE_HINTS):
            when = _day(ev.get("occurred_at") or ev.get("created_at"))
            who = ev.get("practitioner") or ev.get("recorded_by") or "note"
            snippet = _clean(ev.get("summary"), 180)
            lines.append(f"- {when or 'undated'} · {who}: {snippet or kind}")
        if len(lines) >= 8:
            break
    return lines


def _latest_labs(diagnostics: list[dict[str, Any]] | None, limit: int = 8) -> list[str]:
    rows = [d for d in diagnostics or [] if isinstance(d, dict) and d.get("name")]
    rows.sort(key=lambda d: str(d.get("recorded_at") or ""), reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = str(row.get("name") or "")
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        value = row.get("value")
        unit = row.get("unit") or ""
        when = _day(row.get("recorded_at"))
        out.append(f"- {name}: {value} {unit}".strip() + (f" ({when})" if when else ""))
        if len(out) >= limit:
            break
    return out


def _actions_for_event(items: list[dict[str, Any]], event_id: str) -> list[dict[str, Any]]:
    return [i for i in items if i.get("source_event_id") == event_id]


def build_care_workspace_brief(patient_id: str | None) -> str:
    """Compact family-facts pack for the Ask agent. No AI — recorded data only."""
    if not patient_id:
        return ""
    from app.services.case_manager import get_patient_profile

    profile = get_patient_profile(patient_id)
    events = list(profile.get("care_events") or [])
    events_newest = sorted(
        events,
        key=lambda ev: str(ev.get("occurred_at") or ev.get("created_at") or ""),
        reverse=True,
    )
    holders = current_ball_holders(profile.get("ball_holders"))
    team = profile.get("care_team") or []
    family = profile.get("family_team") or []
    items = profile.get("care_items") or []
    open_items = _open_items(profile)
    questions = [
        q
        for q in (profile.get("doctor_questions") or [])
        if (q.get("status") or "open") == "open"
    ]
    im = find_internal_medicine_team(team, events)
    stay = None
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Toronto")
        now = datetime.now(tz)
        stay = hospital_stay(events, tz, now)
    except Exception:
        stay = None

    lines = [
        "=== CARE WORKSPACE (family facts — use these first for who / what / when questions) ===",
        "",
        "WHO TO CALL NOW FOR CLARITY (who has the ball — the people in charge right now):",
    ]
    if holders:
        lead = next((h for h in holders if h.get("lead")), None)
        if lead and len(holders) > 1:
            lines.append(f"- Call first: {_format_holder(lead)}")
            others = [h for h in holders if h["id"] != lead["id"]]
            lines.extend(f"- Also in charge: {_format_holder(h)}" for h in others)
        else:
            lines.extend(f"- Call now: {_format_holder(h)}" for h in holders)
        lines.append("- If the family asks who has the ball, name these people and how to reach them. Do not list expired assignments.")
    else:
        lines.append(
            "- Nobody is in charge right now. Say that clearly. Tell the family to set Who has the ball on Timeline so everyone knows who to call."
        )

    checkin = normalize_ball_checkin(profile.get("ball_checkin"))
    lines += ["", "NEXT CHECK-IN (who, what, when):"]
    if checkin:
        bits = [checkin["who"] or "who is not set"]
        if checkin.get("what"):
            bits.append(f"— {checkin['what']}")
        if checkin.get("at"):
            bits.append(f"at {checkin['at']}")
        lines.append("- " + " ".join(bits))
    else:
        lines.append("- No next check-in is set. Tell the family to add who checks in, what they cover, and when.")

    lines += ["", "PROFESSIONAL CARE TEAM:"]
    if team:
        for member in team:
            name = member.get("name") or "Unnamed"
            role = member.get("role") or "role not recorded"
            last = ""
            lines.append(f"- {name} — {role}{last}")
    else:
        lines.append("- No named professional care team yet.")

    lines += ["", "INTERNAL MEDICINE / ATTENDING:"]
    if im["named"]:
        for row in im["named"]:
            lines.append(f"- {row['name']}" + (f" — {row['role']}" if row.get("role") else ""))
    if im["unnamed_roles"]:
        lines.append(
            "- Internal Medicine appears as a role on the record, but no attending name is saved. "
            "Say so, and tell the family to add the name on the care team or the next visit."
        )
    if not im["named"] and not im["unnamed_roles"]:
        lines.append("- No Internal Medicine / hospitalist / attending is on file.")

    lines += ["", "FAMILY:"]
    if family:
        for member in family:
            roles = ", ".join(member.get("roles") or []) or (member.get("relationship") or "")
            lines.append(f"- {member.get('name')}" + (f" — {roles}" if roles else ""))
    else:
        lines.append("- No family members listed yet.")

    lines += ["", "OPEN ACTION ITEMS:"]
    if open_items:
        for item in open_items[:20]:
            due = item.get("due_at") or "no date"
            who = item.get("contact") or ""
            extra = f" · {who}" if who else ""
            lines.append(f"- {item.get('title')} — due {due}{extra}")
    else:
        lines.append("- None open.")

    lines += ["", "OPEN QUESTIONS FOR DOCTORS:"]
    if questions:
        for q in questions[:12]:
            who = q.get("doctor") or q.get("role") or "unassigned"
            lines.append(f"- {q.get('text') or q.get('question')} — {who}")
    else:
        lines.append("- None.")

    lines += ["", "RECENT STAFF VISITS:"]
    visits = _recent_visits(events_newest)
    if visits:
        for ev in visits:
            when = _day(ev.get("occurred_at") or ev.get("created_at")) or "undated"
            who = ev.get("practitioner") or "unnamed staff"
            role = ev.get("role") or ""
            label = f"{who} ({role})" if role else who
            closeout = _actions_for_event(items, ev.get("id") or "")
            lines.append(f"- {when} · {label}: {_clean(ev.get('summary'), 200)}")
            if closeout:
                for act in closeout:
                    due = act.get("due_at") or "no date"
                    status = act.get("status") or "open"
                    lines.append(f"    next: {act.get('title')} by {due} ({status})")
            else:
                lines.append("    next: not recorded")
    else:
        lines.append("- No staff visits recorded yet.")

    lines += ["", "DISCHARGE / STAY:"]
    if stay:
        admitted = _day(stay.get("admitted_at"))
        discharged = _day(stay.get("discharged_at"))
        loc = stay.get("location") or ""
        if stay.get("active"):
            lines.append(
                f"- In hospital since {admitted} · day {stay.get('days')}"
                + (f" · {loc}" if loc else "")
            )
        else:
            lines.append(
                f"- Stay {admitted} to {discharged} · {stay.get('days')} days"
                + (f" · {loc}" if loc else "")
            )
    discharges = [ev for ev in events if ev.get("kind") == "discharge"]
    if discharges:
        last = sorted(discharges, key=lambda e: str(e.get("occurred_at") or ""), reverse=True)[0]
        lines.append(f"- Discharge recorded {_day(last.get('occurred_at')) or 'undated'}: {_clean(last.get('summary'), 180)}")
    else:
        lines.append("- No discharge date is recorded.")
    mentions = _discharge_mentions(events_newest)
    if mentions:
        lines.append("- Mentions of discharge in the record:")
        lines.extend(mentions)

    labs = _latest_labs(profile.get("diagnostics"))
    lines += ["", "LATEST LABS:"]
    lines.extend(labs or ["- None recorded."])

    return "\n".join(lines)
