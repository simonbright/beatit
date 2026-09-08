"""Extract and compare patient name / DOB from lab report text vs BeatIt profile."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.services.case_manager import get_patient_profile, list_patients

_NAME_STOP = {
    "mr",
    "mrs",
    "ms",
    "miss",
    "dr",
    "patient",
    "name",
    "client",
    "male",
    "female",
    "sex",
    "gender",
    "age",
    "years",
    "yrs",
}

_DOB_LABEL = (
    r"(?:DOB|D\.O\.B\.|Date\s+of\s+Birth|Birth\s*Date|Born(?:\s+on)?|"
    r"Date\s+of\s+birth|Patient\s+DOB)"
)

_NAME_LABEL = (
    r"(?:Patient(?:\s+Name)?|Patient(?:\s*/\s*Client)?\s*Name|Client\s*Name|"
    r"Name\s+of\s+Patient|Patient\s*ID\s*Name|^Name)"
)


def normalize_person_name(name: str | None) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    # "LAST, FIRST" → "FIRST LAST"
    if "," in raw:
        parts = [p.strip() for p in raw.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            raw = f"{parts[1]} {parts[0]}"
    cleaned = re.sub(r"[^A-Za-z\s\-']", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def name_tokens(name: str | None) -> set[str]:
    return {
        t
        for t in normalize_person_name(name).replace("-", " ").split()
        if len(t) > 1 and t not in _NAME_STOP
    }


def names_match(a: str | None, b: str | None) -> bool:
    """True when the shorter name's tokens are all present in the longer name."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return False
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return short.issubset(long)


def parse_dob_to_iso(raw: str | None) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = text.replace(",", " ").strip()
    text = re.sub(r"\s+", " ", text)
    candidates = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y",
        "%d/%m/%y",
        "%m/%d/%y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
    ]
    for fmt in candidates:
        try:
            dt = datetime.strptime(text, fmt)
            # 2-digit years: strptime maps 0–68 → 2000s, 69–99 → 1900s — OK for DOB
            if dt.year < 1900 or dt.year > datetime.now().year:
                continue
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


def _patient_label(patient_id: str) -> str | None:
    for p in list_patients():
        if p.get("id") == patient_id:
            label = str(p.get("label") or "").strip()
            return label or None
    return None


def extract_lab_patient_identity(text: str | None) -> dict[str, Any]:
    """Heuristic extract of patient name + DOB from lab OCR / PDF text."""
    body = str(text or "")
    if not body.strip():
        return {"name": None, "date_of_birth": None, "raw_name": None, "raw_dob": None}

    # Focus on the header — identity almost always appears early
    head = body[:8000]
    # Normalize odd OCR spaces
    sample = head.replace("\u00a0", " ")

    raw_name: str | None = None
    name_patterns = [
        # Same-line only — avoid swallowing the next label (DOB / Sex)
        rf"{_NAME_LABEL}\s*[:\-]\s*([A-Z][A-Za-z'''\-]+(?:[ \t]+[A-Z][A-Za-z'''\-]+){{1,4}})",
        rf"{_NAME_LABEL}\s*[:\-]\s*([A-Z]{{2,}}(?:[ \t]+[A-Z]{{2,}}){{1,4}})",
        rf"{_NAME_LABEL}\s*[:\-]\s*([A-Z][A-Za-z'''\-]+[ \t]*,[ \t]*[A-Z][A-Za-z'''\-]+(?:[ \t]+[A-Z][A-Za-z'''\-]+)?)",
    ]
    junk_tail = {"dob", "sex", "gender", "age", "male", "female", "id", "mrn", "phn"}
    for pat in name_patterns:
        m = re.search(pat, sample, flags=re.IGNORECASE | re.MULTILINE)
        if m:
            cand = re.sub(r"[ \t]+", " ", m.group(1)).strip(" -:")
            tokens = name_tokens(cand)
            if any(t in junk_tail for t in tokens):
                cand = " ".join(w for w in cand.split() if w.lower().strip(",:") not in junk_tail)
                tokens = name_tokens(cand)
            if len(tokens) >= 2:
                raw_name = cand
                break

    raw_dob: str | None = None
    dob_patterns = [
        rf"{_DOB_LABEL}\s*[:\-]?\s*(\d{{4}}-\d{{2}}-\d{{2}})",
        rf"{_DOB_LABEL}\s*[:\-]?\s*(\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}})",
        rf"{_DOB_LABEL}\s*[:\-]?\s*([A-Za-z]{{3,9}}\s+\d{{1,2}},?\s+\d{{4}})",
        rf"{_DOB_LABEL}\s*[:\-]?\s*(\d{{1,2}}\s+[A-Za-z]{{3,9}}\s+\d{{4}})",
    ]
    for pat in dob_patterns:
        m = re.search(pat, sample, flags=re.IGNORECASE)
        if m:
            iso = parse_dob_to_iso(m.group(1))
            if iso:
                raw_dob = m.group(1).strip()
                break

    return {
        "name": normalize_person_name(raw_name).title() if raw_name else None,
        "date_of_birth": parse_dob_to_iso(raw_dob) if raw_dob else None,
        "raw_name": raw_name,
        "raw_dob": raw_dob,
    }


def compare_lab_patient_identity(
    patient_id: str,
    extracted: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare extracted lab identity to the active BeatIt patient profile."""
    label = _patient_label(patient_id)
    profile = get_patient_profile(patient_id) or {}
    profile_dob = str(profile.get("date_of_birth") or "").strip()[:10] or None
    report_name = (extracted or {}).get("name") or (extracted or {}).get("raw_name")
    report_dob = (extracted or {}).get("date_of_birth")

    name_ok: bool | None = None
    dob_ok: bool | None = None
    issues: list[str] = []

    if report_name and label:
        name_ok = names_match(report_name, label)
        if not name_ok:
            issues.append(
                f"Report patient name “{report_name}” does not match active patient “{label}”"
            )
    elif report_name and not label:
        name_ok = None
        issues.append(f"Report lists patient “{report_name}” but active patient has no display name")
    elif label and not report_name:
        name_ok = None

    if report_dob and profile_dob:
        dob_ok = report_dob == profile_dob
        if not dob_ok:
            issues.append(
                f"Report DOB {report_dob} does not match profile DOB {profile_dob}"
            )
    elif report_dob and not profile_dob:
        dob_ok = None
        issues.append(
            f"Report DOB is {report_dob} but this patient’s profile has no date of birth set"
        )
    elif profile_dob and not report_dob:
        dob_ok = None

    mismatch = bool(issues) and (
        name_ok is False or dob_ok is False or (report_name and name_ok is False)
    )
    # Treat explicit name or DOB conflict as a hard mismatch requiring confirmation
    hard_mismatch = name_ok is False or dob_ok is False

    return {
        "patient_id": patient_id,
        "profile_name": label,
        "profile_date_of_birth": profile_dob,
        "report_name": report_name,
        "report_date_of_birth": report_dob,
        "name_match": name_ok,
        "dob_match": dob_ok,
        "mismatch": hard_mismatch,
        "soft_warnings": [] if hard_mismatch else issues,
        "issues": issues if hard_mismatch else [],
        "checked": True,
        "extracted": extracted or {},
    }
