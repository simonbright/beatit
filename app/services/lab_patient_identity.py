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


def _plausible_dob_year(year: int) -> bool:
    return 1900 <= year <= datetime.now().year


def _expand_two_digit_year(year: int) -> int:
    """Match datetime.strptime %y: 0–68 → 2000s, 69–99 → 1900s."""
    if year >= 100:
        return year
    return 2000 + year if year <= 68 else 1900 + year


def parse_dob_candidates(raw: str | None) -> list[str]:
    """Return plausible ISO DOB values for a raw date string.

    Ambiguous numeric dates (both parts ≤ 12) yield *both* DMY and MDY
    interpretations so Canada (DD/MM) vs USA (MM/DD) labs can match the
    profile when either reading is correct.
    """
    text = str(raw or "").strip()
    if not text:
        return []
    text = text.replace(",", " ").strip()
    text = re.sub(r"\s+", " ", text)

    unambiguous = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
    ]
    for fmt in unambiguous:
        try:
            dt = datetime.strptime(text, fmt)
            if _plausible_dob_year(dt.year):
                return [dt.date().isoformat()]
        except ValueError:
            continue

    m = re.fullmatch(r"(\d{1,2})([/-])(\d{1,2})\2(\d{2,4})", text)
    if m:
        a, _sep, b, y_raw = m.groups()
        day_or_month_a = int(a)
        day_or_month_b = int(b)
        year = _expand_two_digit_year(int(y_raw))
        if not _plausible_dob_year(year):
            return []
        out: list[str] = []
        seen: set[str] = set()

        def add(month: int, day: int) -> None:
            try:
                iso = datetime(year, month, day).date().isoformat()
            except ValueError:
                return
            if iso not in seen:
                seen.add(iso)
                out.append(iso)

        # DD/MM (Canada / most of world)
        if 1 <= day_or_month_a <= 31 and 1 <= day_or_month_b <= 12:
            add(day_or_month_b, day_or_month_a)
        # MM/DD (USA)
        if 1 <= day_or_month_a <= 12 and 1 <= day_or_month_b <= 31:
            add(day_or_month_a, day_or_month_b)
        return out

    # Last resort: single best parse from remaining formats
    single = parse_dob_to_iso_legacy(text)
    return [single] if single else []


def parse_dob_to_iso_legacy(text: str) -> str | None:
    candidates = [
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
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in candidates:
        try:
            dt = datetime.strptime(text, fmt)
            if not _plausible_dob_year(dt.year):
                continue
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


def parse_dob_to_iso(raw: str | None) -> str | None:
    """Best-effort single ISO DOB. Prefer unambiguous; else first candidate."""
    cands = parse_dob_candidates(raw)
    return cands[0] if cands else None


def dobs_match(report_dob: str | None, profile_dob: str | None, *, raw_dob: str | None = None) -> bool:
    """True when profile DOB matches the report ISO or any Canada/USA reading of raw_dob."""
    profile = str(profile_dob or "").strip()[:10] or None
    if not profile:
        return False
    candidates: list[str] = []
    if raw_dob:
        candidates.extend(parse_dob_candidates(raw_dob))
    report = str(report_dob or "").strip()[:10] or None
    if report and report not in candidates:
        # Also expand report ISO via raw-less path (already ISO → one cand)
        candidates.extend(parse_dob_candidates(report))
    if report and report not in candidates:
        candidates.append(report)
    return profile in candidates


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
        "date_of_birth_candidates": parse_dob_candidates(raw_dob) if raw_dob else [],
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
    raw_dob = (extracted or {}).get("raw_dob")

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

    dob_candidates = parse_dob_candidates(raw_dob) if raw_dob else []
    if report_dob and report_dob not in dob_candidates:
        dob_candidates = [*dob_candidates, str(report_dob)[:10]]

    if (report_dob or raw_dob) and profile_dob:
        dob_ok = dobs_match(report_dob, profile_dob, raw_dob=raw_dob)
        if not dob_ok:
            shown = report_dob or (raw_dob or "?")
            alt = ""
            if len(dob_candidates) > 1:
                alt = f" (also read as {', '.join(c for c in dob_candidates if c != shown)})"
            issues.append(
                f"Report DOB {shown}{alt} does not match profile DOB {profile_dob}"
            )
    elif (report_dob or raw_dob) and not profile_dob:
        dob_ok = None
        shown = report_dob or raw_dob
        issues.append(
            f"Report DOB is {shown} but this patient’s profile has no date of birth set"
        )
    elif profile_dob and not report_dob and not raw_dob:
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
        "report_dob_candidates": dob_candidates,
        "name_match": name_ok,
        "dob_match": dob_ok,
        "mismatch": hard_mismatch,
        "soft_warnings": [] if hard_mismatch else issues,
        "issues": issues if hard_mismatch else [],
        "checked": True,
        "extracted": extracted or {},
    }
