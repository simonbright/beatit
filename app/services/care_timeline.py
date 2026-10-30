"""Family timeline: a facts-only, chronological view of the patient's care.

Merges manually recorded care events (practitioner visits, calls, tests,
transfers, family notes) with dated facts the app already holds — lab
readings, medication changes, plan items, milestones and library uploads.

Nothing here is AI-generated. Every row carries its provenance so the family
can see where a fact came from and who recorded it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CARE_EVENT_KINDS: dict[str, dict[str, str]] = {
    "visit": {"label": "Practitioner visit", "category": "visit"},
    "call": {"label": "Call with care team", "category": "visit"},
    "family_meeting": {"label": "Family meeting", "category": "visit"},
    "test_ordered": {"label": "Test ordered", "category": "test"},
    "test_done": {"label": "Test / scan done", "category": "test"},
    "result": {"label": "Result shared", "category": "result"},
    "procedure": {"label": "Procedure", "category": "care"},
    "medication": {"label": "Medication change", "category": "medication"},
    "admission": {"label": "Admitted", "category": "care"},
    "transfer": {"label": "Transfer / room move", "category": "care"},
    "discharge": {"label": "Discharged", "category": "care"},
    "status": {"label": "Status observed", "category": "family"},
    "family_update": {"label": "Family update", "category": "family"},
    "transcript": {"label": "Visit transcript", "category": "visit"},
    "note": {"label": "Family note", "category": "family"},
}

ROLE_PRESETS: list[str] = [
    "Oncology",
    "Infectious disease",
    "Internal medicine (internist)",
    "Hospitalist",
    "Surgery",
    "Gastroenterology",
    "Radiology",
    "Palliative care",
    "Nursing",
    "Nurse practitioner",
    "Pharmacy",
    "Social work",
    "Case manager",
    "Physiotherapy",
    "Dietitian",
]

# Kinds that count as "a practitioner was in" for the care-team roll-up.
CONTACT_KINDS = frozenset({"visit", "call", "family_meeting", "procedure", "result", "transcript"})

LINKABLE_SOURCE_TYPES = frozenset({"text", "pdf", "video", "youtube", "url"})

ENVIRONMENT_PRESETS: list[str] = [
    "Hospital — inpatient",
    "ICU",
    "Cancer centre",
    "Outpatient clinic",
    "Family doctor",
    "Home care",
    "Pharmacy",
    "Virtual",
]


CONTACT_METHODS: dict[str, str] = {
    "phone": "Phone call",
    "text": "Text message",
    "email": "Email",
    "pager": "Pager",
    "portal": "Patient portal message",
    "nurse_station": "Through the nurse station",
    "in_person": "In person / at rounds",
}


def normalize_contact_method(value: Any) -> str:
    cleaned = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned if cleaned in CONTACT_METHODS else ""


FAMILY_ROLE_PRESETS: list[dict[str, str]] = [
    {"key": "point_person", "label": "Point person", "hint": "Single voice to the care team; final say on what to ask"},
    {"key": "bedside", "label": "Bedside rotation", "hint": "At the hospital; runs the question list with whoever comes in"},
    {"key": "medical_liaison", "label": "Medical liaison", "hint": "Reviews results and prepares questions for the doctors"},
    {"key": "records", "label": "Records keeper", "hint": "Uploads results, captures transcripts, keeps the timeline current"},
    {"key": "communicator", "label": "Family communicator", "hint": "Sends the daily update to everyone"},
    {"key": "admin", "label": "Admin & insurance", "hint": "Billing, approvals, forms and paperwork"},
    {"key": "logistics", "label": "Logistics", "hint": "Home, meals, transport, pets, overnight bags"},
    {"key": "support", "label": "Emotional support", "hint": "Calls, visits, cards and photos for the patient"},
]
_FAMILY_ROLE_BY_KEY = {r["key"]: r for r in FAMILY_ROLE_PRESETS}
_FAMILY_ROLE_BY_LABEL = {r["label"].lower(): r for r in FAMILY_ROLE_PRESETS}


def family_role_key(value: Any) -> str:
    """Preset key for a preset role (by key or label); custom roles keep their label."""
    text = " ".join(str(value or "").split())[:60]
    if not text:
        return ""
    low = text.lower().replace(" ", "_")
    if low in _FAMILY_ROLE_BY_KEY:
        return low
    preset = _FAMILY_ROLE_BY_LABEL.get(text.lower())
    return preset["key"] if preset else text


def family_role_label(key: str) -> str:
    preset = _FAMILY_ROLE_BY_KEY.get(key)
    return preset["label"] if preset else key


def normalize_family_member(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name"), 120)
    if not name:
        return None
    member_id = str(raw.get("id") or "").strip()
    if not member_id:
        from uuid import uuid4

        member_id = str(uuid4())
    roles: list[str] = []
    roles_raw = raw.get("roles") or []
    if isinstance(roles_raw, str):
        roles_raw = roles_raw.split(",")
    for r in roles_raw:
        key = family_role_key(r)
        if key and key not in roles:
            roles.append(key)
    since_raw = raw.get("role_since") if isinstance(raw.get("role_since"), dict) else {}
    role_since = {k: str(since_raw.get(k)) for k in roles if since_raw.get(k)}
    return {
        "id": member_id,
        "name": name,
        "relationship": _clean(raw.get("relationship"), 60),
        "phone": _clean(raw.get("phone"), 60),
        "email": _clean(raw.get("email"), 120),
        "preferred_contact": normalize_contact_method(raw.get("preferred_contact")),
        "availability": _clean(raw.get("availability"), 200),
        "roles": roles[:12],
        "role_since": role_since,
        "aliases": [a for a in (_clean(x, 120) for x in (raw.get("aliases") or [])) if a][:6],
        "created_at": str(raw.get("created_at") or _now_iso()),
        "updated_at": str(raw.get("updated_at") or "") or None,
    }


def normalize_family_members(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        member = normalize_family_member(row)
        if not member:
            continue
        key = _name_key(member["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(member)
    return out


def normalize_role_history(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw:
        if not isinstance(row, dict) or not row.get("role") or not row.get("person"):
            continue
        action = row.get("action") if row.get("action") in {"assigned", "unassigned"} else "assigned"
        out.append(
            {
                "at": str(row.get("at") or _now_iso()),
                "role": str(row["role"])[:60],
                "person": _clean(row.get("person"), 120),
                "action": action,
                "by": _clean(row.get("by"), 120) or None,
            }
        )
    return out[-500:]


def family_roles_payload(members: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Role board: every preset role plus custom roles in use, with who holds them."""
    keys = [r["key"] for r in FAMILY_ROLE_PRESETS]
    for m in members or []:
        for r in m.get("roles") or []:
            if r not in keys:
                keys.append(r)
    board = []
    for key in keys:
        preset = _FAMILY_ROLE_BY_KEY.get(key)
        holders = [
            {"id": m["id"], "name": m["name"], "since": (m.get("role_since") or {}).get(key)}
            for m in members or []
            if key in (m.get("roles") or [])
        ]
        board.append(
            {
                "key": key,
                "label": preset["label"] if preset else key,
                "hint": preset["hint"] if preset else "",
                "custom": preset is None,
                "holders": holders,
            }
        )
    return board


def role_history_rows(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = []
    for i, h in enumerate(history or []):
        label = family_role_label(h["role"])
        assigned = h["action"] == "assigned"
        rows.append(
            _row(
                row_id=f"role:{i}:{h['at']}",
                at=h["at"],
                category="family",
                kind="role_change",
                kind_label="Family role",
                title=f"{h['person']} — {label}" if assigned else f"{h['person']} no longer {label}",
                detail="",
                provenance=f"Changed by {h.get('by') or 'family'}",
                provenance_type="family_team",
            )
        )
    return rows


def _name_key(name: Any) -> str:
    return " ".join(str(name or "").lower().replace(".", " ").split())


def normalize_team_member(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name"), 120)
    if not name:
        return None
    member_id = str(raw.get("id") or "").strip()
    if not member_id:
        from uuid import uuid4

        member_id = str(uuid4())
    envs_raw = raw.get("environments")
    if isinstance(envs_raw, str):
        envs_raw = envs_raw.split(",")
    envs: list[str] = []
    for env in envs_raw or []:
        cleaned = _clean(env, 80)
        if cleaned and cleaned.lower() not in {e.lower() for e in envs}:
            envs.append(cleaned)
    return {
        "id": member_id,
        "name": name,
        "role": _clean(raw.get("role"), 80),
        "environments": envs[:8],
        "phone": _clean(raw.get("phone"), 60),
        "email": _clean(raw.get("email"), 120),
        "contact_other": _clean(raw.get("contact_other"), 160),
        "preferred_contact": normalize_contact_method(raw.get("preferred_contact")),
        "notes": _clean(raw.get("notes"), 400),
        "created_at": str(raw.get("created_at") or _now_iso()),
        "updated_at": str(raw.get("updated_at") or "") or None,
    }


def normalize_team_members(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        member = normalize_team_member(row)
        if not member:
            continue
        key = _name_key(member["name"])
        if key in seen:
            continue
        seen.add(key)
        out.append(member)
    out.sort(key=lambda m: _name_key(m["name"]))
    return out


def find_team_member(members: list[dict[str, Any]] | None, name: Any) -> dict[str, Any] | None:
    key = _name_key(name)
    if not key:
        return None
    return next((m for m in members or [] if _name_key(m.get("name")) == key), None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_care_event_kind(kind: str | None) -> str:
    cleaned = str(kind or "note").strip().lower().replace("-", "_").replace(" ", "_")
    return cleaned if cleaned in CARE_EVENT_KINDS else "note"


def parse_event_datetime(raw: str | None) -> str:
    """Return an ISO string: full datetime (tz-aware) or bare YYYY-MM-DD."""
    text = str(raw or "").strip()
    if not text:
        return _now_iso()
    if len(text) == 10:
        try:
            return datetime.fromisoformat(text).date().isoformat()
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO date or datetime") from exc
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("occurred_at must be an ISO date or datetime") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _clean_block(text: Any, limit: int) -> str:
    """Keep line breaks (summaries are often several facts)."""
    lines = [" ".join(line.split()) for line in str(text or "").splitlines()]
    out = "\n".join(line for line in lines if line).strip()
    return out[:limit]


def normalize_care_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    summary = _clean_block(raw.get("summary"), 4000)
    practitioner = _clean(raw.get("practitioner"), 120)
    kind = normalize_care_event_kind(raw.get("kind"))
    if not summary and not practitioner:
        return None
    event_id = str(raw.get("id") or "").strip()
    if not event_id:
        from uuid import uuid4

        event_id = str(uuid4())
    try:
        occurred = parse_event_datetime(raw.get("occurred_at"))
    except ValueError:
        occurred = str(raw.get("created_at") or _now_iso())
    return {
        "id": event_id,
        "kind": kind,
        "occurred_at": occurred,
        "practitioner": practitioner,
        "role": _clean(raw.get("role"), 80),
        "location": _clean(raw.get("location"), 120),
        "summary": summary,
        "source_document_id": _clean(raw.get("source_document_id"), 80) or None,
        "source_document_title": _clean(raw.get("source_document_title"), 200) or None,
        "recorded_by": _clean(raw.get("recorded_by"), 120) or None,
        "source": _clean(raw.get("source"), 20) or "manual",
        "source_ref": _clean(raw.get("source_ref"), 80) or None,
        "minor": bool(raw.get("minor")),
        "created_at": str(raw.get("created_at") or _now_iso()),
        "updated_at": str(raw.get("updated_at") or "") or None,
        "updated_by": _clean(raw.get("updated_by"), 120) or None,
    }


def normalize_care_events(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    rows = [e for e in (normalize_care_event(r) for r in raw) if e]
    rows.sort(key=lambda e: _sort_key(e.get("occurred_at")), reverse=True)
    return rows


def _sort_key(value: Any) -> str:
    """Comparable UTC string. Date-only values sort as midday."""
    text = str(value or "")
    if len(text) == 10:
        return f"{text}T12:00:00+00:00"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return text


def _date_only(value: Any) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _fmt_number(value: Any) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num.is_integer():
        return str(int(num))
    return f"{num:g}"


# ---------------------------------------------------------------------------
# Timeline rows
# ---------------------------------------------------------------------------


def _row(
    *,
    row_id: str,
    at: str,
    category: str,
    kind: str,
    kind_label: str,
    title: str,
    detail: str = "",
    provenance: str,
    provenance_type: str,
    **extra: Any,
) -> dict[str, Any]:
    row = {
        "id": row_id,
        "at": at,
        "has_time": len(str(at)) > 10,
        "category": category,
        "kind": kind,
        "kind_label": kind_label,
        "title": title,
        "detail": detail,
        "provenance": provenance,
        "provenance_type": provenance_type,
        "editable": False,
    }
    row.update(extra)
    return row


def care_event_rows(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in events or []:
        meta = CARE_EVENT_KINDS.get(ev.get("kind") or "note", CARE_EVENT_KINDS["note"])
        who = ev.get("practitioner") or ""
        role = ev.get("role") or ""
        is_whatsapp = ev.get("source") == "whatsapp"
        kind_label = meta["label"]
        if who and role:
            title = f"{who} ({role})"
        elif who or role:
            title = who or role
        elif is_whatsapp and ev.get("kind") == "family_update":
            title = ev.get("recorded_by") or "Family"
            kind_label = "WhatsApp"
        else:
            title = ""
        recorded = ev.get("recorded_by") or "family"
        if ev.get("source") == "whatsapp":
            prov = f"From WhatsApp · sent by {recorded}"
        elif ev.get("source") == "transcript":
            prov = f"Recorded visit · uploaded by {recorded}"
        else:
            prov = f"Recorded by {recorded}"
        if ev.get("updated_at"):
            prov += f" · edited by {ev.get('updated_by') or recorded}"
        rows.append(
            _row(
                row_id=f"care:{ev['id']}",
                at=ev.get("occurred_at") or ev.get("created_at") or "",
                category=meta["category"],
                kind=ev.get("kind") or "note",
                kind_label=kind_label,
                title=title,
                detail=ev.get("summary") or "",
                provenance=prov,
                provenance_type="family_record",
                editable=True,
                care_event_id=ev["id"],
                source=ev.get("source") or "manual",
                minor=bool(ev.get("minor")),
                sender=recorded if ev.get("source") == "whatsapp" else "",
                practitioner=who,
                role=role,
                location=ev.get("location") or "",
                source_document_id=ev.get("source_document_id"),
                source_document_title=ev.get("source_document_title"),
            )
        )
    return rows


def lab_rows(diagnostics: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    by_date: dict[str, list[dict[str, Any]]] = {}
    for d in diagnostics or []:
        if not isinstance(d, dict):
            continue
        when = _date_only(d.get("recorded_at"))
        if not when:
            continue
        by_date.setdefault(when, []).append(d)
    rows: list[dict[str, Any]] = []
    for when, items in by_date.items():
        items.sort(key=lambda d: str(d.get("name") or "").lower())
        lines: list[str] = []
        for d in items:
            name = str(d.get("name") or "Test").strip()
            if d.get("value") is not None:
                unit = str(d.get("unit") or "").strip()
                lines.append(f"{name}: {_fmt_number(d.get('value'))}{(' ' + unit) if unit else ''}")
            else:
                text = str(d.get("result") or d.get("notes") or "").strip()
                if len(text) > 220:
                    text = text[:219].rstrip() + "…"
                lines.append(f"{name}: {text}" if text else name)
        count = len(items)
        title = f"{count} result{'s' if count != 1 else ''} recorded"
        names = sorted({str(d.get('name') or '').strip() for d in items if d.get("name")})
        if names:
            preview = ", ".join(names[:4]) + ("…" if len(names) > 4 else "")
            title = f"{title} — {preview}"
        rows.append(
            _row(
                row_id=f"labs:{when}",
                at=when,
                category="result",
                kind="lab_results",
                kind_label="Results",
                title=title,
                detail="\n".join(lines),
                provenance="From the Labs record (date of collection)",
                provenance_type="lab_record",
            )
        )
    return rows


def medication_rows(medications: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    from app.services.medication_events import medication_chart_events

    labels = {"start": "Medication started", "dose_change": "Dose changed", "stop": "Medication stopped"}
    rows: list[dict[str, Any]] = []
    for ev in medication_chart_events(medications, max_events=500):
        when = _date_only(ev.get("date"))
        if not when:
            continue
        kind = str(ev.get("kind") or "start")
        rows.append(
            _row(
                row_id=f"med:{when}:{kind}:{ev.get('medication_id') or ev.get('body')}",
                at=when,
                category="medication",
                kind=f"med_{kind}",
                kind_label=labels.get(kind, "Medication"),
                title=str(ev.get("body") or ev.get("label") or "Medication change"),
                provenance="From the Medications record",
                provenance_type="medication_record",
            )
        )
    return rows


def plan_rows(care_items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in care_items or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        status = item.get("status") or "open"
        contact = str(item.get("contact") or "").strip()
        if status == "done" and item.get("completed_at"):
            rows.append(
                _row(
                    row_id=f"plan-done:{item.get('id')}",
                    at=str(item["completed_at"]),
                    category="plan",
                    kind="plan_done",
                    kind_label="Plan item done",
                    title=title,
                    detail=contact,
                    provenance="From the Plan",
                    provenance_type="plan",
                )
            )
        elif status == "open" and item.get("due_at"):
            rows.append(
                _row(
                    row_id=f"plan-due:{item.get('id')}",
                    at=str(item["due_at"]),
                    category="plan",
                    kind="plan_due",
                    kind_label="Planned",
                    title=title,
                    detail=contact,
                    provenance="From the Plan (scheduled, not yet done)",
                    provenance_type="plan",
                    upcoming=True,
                )
            )
        for idx, act in enumerate(item.get("activity") or []):
            if not isinstance(act, dict):
                continue
            akind = str(act.get("kind") or "note")
            if akind not in {"sent", "call"}:
                continue
            rows.append(
                _row(
                    row_id=f"plan-act:{item.get('id')}:{idx}",
                    at=str(act.get("at") or ""),
                    category="plan",
                    kind=f"plan_{akind}",
                    kind_label="Sent" if akind == "sent" else "Call",
                    title=f"{title}",
                    detail=str(act.get("note") or ""),
                    provenance="From the Plan activity log",
                    provenance_type="plan",
                )
            )
    return rows


def milestone_rows(milestones: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in milestones or []:
        if not isinstance(m, dict):
            continue
        when = _date_only(m.get("date"))
        label = str(m.get("label") or "").strip()
        if not when or not label:
            continue
        rows.append(
            _row(
                row_id=f"milestone:{m.get('id') or when}",
                at=when,
                category="care",
                kind="milestone",
                kind_label="Milestone",
                title=label,
                detail=str(m.get("notes") or ""),
                provenance="From Profile → Timeline milestones",
                provenance_type="milestone",
            )
        )
    return rows


def document_rows(documents: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Library uploads. Imaging slices collapse to one row per day."""
    rows: list[dict[str, Any]] = []
    imaging_by_day: dict[str, int] = {}
    type_labels = {
        "text": "Notes / transcript added",
        "pdf": "Report added",
        "url": "Web page added",
        "youtube": "Video link added",
        "video": "Video added",
        "facebook": "Video link added",
    }
    for doc in documents or []:
        if not isinstance(doc, dict):
            continue
        created = str(doc.get("created_at") or "")
        if not created:
            continue
        stype = str(doc.get("source_type") or "")
        if stype == "imaging":
            day = created[:10]
            imaging_by_day[day] = imaging_by_day.get(day, 0) + 1
            continue
        title = str(doc.get("citation_display_name") or doc.get("title") or "Document")
        case_label = str(doc.get("case_label") or "")
        rows.append(
            _row(
                row_id=f"doc:{doc.get('id')}",
                at=created,
                category="document",
                kind=f"doc_{stype or 'other'}",
                kind_label=type_labels.get(stype, "Document added"),
                title=title,
                detail="",
                provenance="Library upload" + (f" · {case_label}" if case_label else ""),
                provenance_type="library",
                source_document_id=doc.get("id"),
                source_document_title=title,
            )
        )
    for day, count in imaging_by_day.items():
        rows.append(
            _row(
                row_id=f"imaging:{day}",
                at=day,
                category="document",
                kind="doc_imaging",
                kind_label="Imaging added",
                title=f"{count} imaging file{'s' if count != 1 else ''} added to the library",
                provenance="Library upload",
                provenance_type="library",
            )
        )
    return rows


def build_timeline(
    profile: dict[str, Any] | None,
    documents: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    profile = profile or {}
    # A document already linked to a timeline entry (e.g. a visit transcript)
    # is shown on that entry, not again as a separate "added to library" row.
    linked = {
        str(e.get("source_document_id"))
        for e in profile.get("care_events") or []
        if e.get("source_document_id")
    }
    documents = [d for d in documents or [] if str(d.get("id")) not in linked]
    rows = [
        *care_event_rows(profile.get("care_events")),
        *lab_rows(profile.get("diagnostics")),
        *medication_rows(profile.get("medications")),
        *plan_rows(profile.get("care_items")),
        *milestone_rows(profile.get("milestones")),
        *role_history_rows(profile.get("family_role_history")),
        *document_rows(documents),
    ]
    rows = [r for r in rows if r.get("at")]
    rows.sort(key=lambda r: _sort_key(r.get("at")), reverse=True)
    return rows


def build_care_team(
    events: list[dict[str, Any]] | None,
    members: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Directory members plus anyone recorded on a visit, with contact stats.

    Each row: name, role, environments, member_id (None when not yet in the
    directory), contacts, last_contact_at, first_at, last_at, last_summary.
    """
    team: dict[str, dict[str, Any]] = {}
    for m in members or []:
        team[_name_key(m["name"])] = {
            "member_id": m["id"],
            "name": m["name"],
            "role": m.get("role") or "",
            "environments": list(m.get("environments") or []),
            "phone": m.get("phone") or "",
            "email": m.get("email") or "",
            "contact_other": m.get("contact_other") or "",
            "preferred_contact": m.get("preferred_contact") or "",
            "notes": m.get("notes") or "",
            "contacts": 0,
            "events": 0,
            "first_at": None,
            "last_at": None,
            "last_contact_at": None,
            "last_summary": "",
        }
    for ev in events or []:
        who = str(ev.get("practitioner") or "").strip()
        role = str(ev.get("role") or "").strip()
        if not who and not role:
            continue
        key = _name_key(who) if who else f"role:{role.lower()}"
        at = ev.get("occurred_at") or ev.get("created_at") or ""
        entry = team.setdefault(
            key,
            {
                "member_id": None,
                "name": who,
                "role": role,
                "environments": [],
                "phone": "",
                "notes": "",
                "contacts": 0,
                "events": 0,
                "first_at": None,
                "last_at": None,
                "last_contact_at": None,
                "last_summary": "",
            },
        )
        if role and not entry["role"]:
            entry["role"] = role
        loc = str(ev.get("location") or "").strip()
        if loc and not entry["member_id"] and loc not in entry["environments"]:
            entry["environments"].append(loc)
        entry["events"] += 1
        if not entry["first_at"] or _sort_key(at) < _sort_key(entry["first_at"]):
            entry["first_at"] = at
        if not entry["last_at"] or _sort_key(at) >= _sort_key(entry["last_at"]):
            entry["last_at"] = at
            entry["last_summary"] = (ev.get("summary") or "")[:280]
        if (ev.get("kind") or "") in CONTACT_KINDS:
            entry["contacts"] += 1
            if not entry["last_contact_at"] or _sort_key(at) > _sort_key(entry["last_contact_at"]):
                entry["last_contact_at"] = at
    out = list(team.values())
    out.sort(key=lambda t: _sort_key(t.get("last_contact_at") or t.get("last_at") or "0000-01-01"), reverse=True)
    return out


def linkable_documents(documents: list[dict[str, Any]] | None, limit: int = 300) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in documents or []:
        if not isinstance(doc, dict):
            continue
        if str(doc.get("source_type") or "") not in LINKABLE_SOURCE_TYPES:
            continue
        out.append(
            {
                "id": doc.get("id"),
                "title": str(doc.get("citation_display_name") or doc.get("title") or "Document"),
                "source_type": doc.get("source_type"),
                "created_at": doc.get("created_at"),
                "case_label": doc.get("case_label"),
            }
        )
    out.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return out[:limit]


def kinds_payload() -> list[dict[str, str]]:
    return [
        {"value": k, "label": v["label"], "category": v["category"]}
        for k, v in CARE_EVENT_KINDS.items()
        if k != "transcript"
    ]


# ---------------------------------------------------------------------------
# What's missing — deterministic checks over recorded facts.
# Every flag says "not recorded", never "didn't happen".
# ---------------------------------------------------------------------------

STAY_START_KINDS = frozenset({"admission"})
STAY_END_KINDS = frozenset({"discharge"})
TEST_KINDS = frozenset({"test_ordered", "test_done"})
RESULT_KINDS = frozenset({"result"})
KEY_FAMILY_ROLES = ("point_person", "bedside")


def _local_dt(value: Any, tz) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            d = datetime.fromisoformat(text)
        except ValueError:
            return None
        return d.replace(hour=12, tzinfo=tz)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def hospital_stay(events: list[dict[str, Any]] | None, tz, now: datetime) -> dict[str, Any] | None:
    """Current or most recent stay from admission / discharge entries."""
    marks = []
    for ev in events or []:
        kind = ev.get("kind")
        if kind in STAY_START_KINDS or kind in STAY_END_KINDS:
            dt = _local_dt(ev.get("occurred_at"), tz)
            if dt:
                marks.append((dt, kind, ev))
    if not marks:
        return None
    marks.sort(key=lambda m: m[0])
    start = end = None
    location = ""
    for dt, kind, ev in marks:
        if kind in STAY_START_KINDS:
            start, end = dt, None
            location = ev.get("location") or ""
        elif start and kind in STAY_END_KINDS:
            end = dt
    if not start:
        return None
    return {
        "admitted_at": start.isoformat(),
        "discharged_at": end.isoformat() if end else None,
        "active": end is None,
        "location": location,
        "days": (((end or now).date() - start.date()).days + 1),
    }


def build_gaps(
    profile: dict[str, Any] | None,
    *,
    tz_name: str = "America/Toronto",
    now: datetime | None = None,
    window_days: int = 14,
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    profile = profile or {}
    tz = ZoneInfo(tz_name)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    today = now.date()
    events = profile.get("care_events") or []
    stay = hospital_stay(events, tz, now)

    by_day: dict[str, dict[str, int]] = {}
    chat_mentions: dict[str, dict[str, int]] = {}
    import re as _re

    care_words = _re.compile(r"\b(dr|doctor\w*|nurse\w*|resident|physician|specialist|rounds|oncolog\w*|internist|hospitalist)\b", _re.I)
    test_words = _re.compile(r"\b(test\w*|scan|ct|mri|x-?ray|ultrasound|blood\s?work|hemoglobin|culture\w*|result\w*|biopsy)\b", _re.I)

    def bump(day, key):
        k = day.isoformat()
        by_day.setdefault(k, {"contacts": 0, "tests": 0, "family": 0})
        by_day[k][key] += 1

    for ev in events:
        dt = _local_dt(ev.get("occurred_at"), tz)
        if not dt:
            continue
        kind = ev.get("kind")
        if kind in CONTACT_KINDS:
            bump(dt.date(), "contacts")
        if kind in TEST_KINDS or kind in RESULT_KINDS:
            bump(dt.date(), "tests")
        meta = CARE_EVENT_KINDS.get(kind or "", {})
        if meta.get("category") == "family" and not ev.get("minor"):
            bump(dt.date(), "family")
            text = str(ev.get("summary") or "")
            day_m = chat_mentions.setdefault(dt.date().isoformat(), {"care": 0, "tests": 0})
            if care_words.search(text):
                day_m["care"] += 1
            if test_words.search(text):
                day_m["tests"] += 1
    for d in profile.get("diagnostics") or []:
        when = _date_only(d.get("recorded_at"))
        if when:
            bump(datetime.fromisoformat(when).date(), "tests")

    items: list[dict[str, Any]] = []
    day_flags: dict[str, list[str]] = {}

    def add(severity, kind, text, *, day=None, action=None, ref=None):
        items.append(
            {
                "severity": severity,
                "kind": kind,
                "text": text,
                "day": day,
                "action": action,
                "ref": ref,
            }
        )
        if day:
            day_flags.setdefault(day, []).append(kind)

    # 1) Hospital stay: per-day coverage
    if stay:
        start_day = datetime.fromisoformat(stay["admitted_at"]).date()
        end_day = (
            datetime.fromisoformat(stay["discharged_at"]).date() if stay["discharged_at"] else today
        )
        first = max(start_day, today - _timedelta(days=window_days - 1))
        day = first
        while day <= end_day and day <= today:
            key = day.isoformat()
            counts = by_day.get(key, {"contacts": 0, "tests": 0, "family": 0})
            is_today = day == today
            label = "today so far" if is_today else _fmt_day(day)
            sev = "info" if is_today else "warn"
            mentions = chat_mentions.get(key, {"care": 0, "tests": 0})

            def hint(n, what):
                if not n:
                    return ""
                return f" — {n} family message{'s' if n != 1 else ''} mention{'' if n != 1 else 's'} {what}; use Classify to record it"

            if not counts["contacts"]:
                add(
                    sev,
                    "no_visit",
                    f"No care-team visit recorded {label}" + hint(mentions["care"], "the care team"),
                    day=key,
                    action="log_visit",
                )
            if not counts["tests"]:
                add(
                    sev,
                    "no_tests",
                    f"No tests or results recorded {label}" + hint(mentions["tests"], "tests"),
                    day=key,
                    action="log_test",
                )
            if not counts["family"]:
                add("info", "no_family_update", f"No family update {label}", day=key, action="family_note")
            day += _timedelta(days=1)
    else:
        add(
            "info",
            "no_stay",
            "Not marked as in hospital. Log an admission so each day of the stay can be checked for visits and tests.",
            action="log_admission",
        )

    # 2) Tests with no result recorded after them
    results_after = sorted(
        _local_dt(ev.get("occurred_at"), tz)
        for ev in events
        if ev.get("kind") in RESULT_KINDS and _local_dt(ev.get("occurred_at"), tz)
    )
    lab_days = sorted(
        datetime.fromisoformat(d).date()
        for d in {_date_only(x.get("recorded_at")) for x in profile.get("diagnostics") or []}
        if d
    )
    for ev in events:
        if ev.get("kind") not in TEST_KINDS:
            continue
        dt = _local_dt(ev.get("occurred_at"), tz)
        if not dt or (now - dt).total_seconds() < 24 * 3600:
            continue
        has_result = any(r > dt for r in results_after) or any(d > dt.date() for d in lab_days)
        if not has_result:
            what = (ev.get("summary") or "Test").split("\n")[0][:90]
            age = (today - dt.date()).days
            add(
                "warn",
                "result_pending",
                f"No result recorded for: {what} ({_fmt_day(dt.date())}, {age} day{'s' if age != 1 else ''} ago)",
                action="log_result",
                ref=ev.get("id"),
            )

    # 3) Practitioners seen during the stay but not recently
    if stay and stay["active"]:
        start_dt = datetime.fromisoformat(stay["admitted_at"])
        last_seen: dict[str, tuple[datetime, dict[str, Any]]] = {}
        for ev in events:
            if ev.get("kind") not in CONTACT_KINDS or not ev.get("practitioner"):
                continue
            dt = _local_dt(ev.get("occurred_at"), tz)
            if not dt or dt < start_dt:
                continue
            k = _name_key(ev["practitioner"])
            if k not in last_seen or dt > last_seen[k][0]:
                last_seen[k] = (dt, ev)
        for dt, ev in last_seen.values():
            gap = (today - dt.date()).days
            if gap >= 2:
                who = ev["practitioner"] + (f" ({ev['role']})" if ev.get("role") else "")
                add("warn", "not_seen", f"{who} — not seen since {_fmt_day(dt.date())} ({gap} days)", action="log_visit")

    # 4) Plan items past due
    for item in profile.get("care_items") or []:
        due = item.get("due_at")
        if (item.get("status") or "open") == "open" and due and due < today.isoformat():
            add("warn", "overdue", f"Overdue from the plan: {item.get('title')} (due {_fmt_day(datetime.fromisoformat(due).date())})", action="open_plan")

    # 5) Family roles nobody holds
    family = profile.get("family_team") or []
    held = {r for m in family for r in (m.get("roles") or [])}
    for key in KEY_FAMILY_ROLES:
        if key not in held:
            add("warn" if family else "info", "role_unassigned", f"No one is {family_role_label(key).lower()}", action="assign_role", ref=key)

    # Warnings first; within each, standing issues before per-day ones, newest day first.
    order = {"warn": 0, "info": 1}
    items.sort(
        key=lambda i: (
            order.get(i["severity"], 2),
            1 if i.get("day") else 0,
            -datetime.fromisoformat(i["day"]).toordinal() if i.get("day") else 0,
        )
    )
    return {
        "stay": stay,
        "items": items,
        "day_flags": day_flags,
        "counts": {
            "warn": sum(1 for i in items if i["severity"] == "warn"),
            "info": sum(1 for i in items if i["severity"] == "info"),
        },
        "generated_at": now.isoformat(),
        "timezone": tz_name,
    }


def _timedelta(**kw):
    from datetime import timedelta

    return timedelta(**kw)


def _fmt_day(d) -> str:
    return f"{d.strftime('%a %b')} {d.day}"
