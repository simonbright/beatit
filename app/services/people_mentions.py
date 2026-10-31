"""Find named people in Ask replies and other family-facing text."""

from __future__ import annotations

import re
from typing import Any

from app.services.care_timeline import _name_key, find_team_member

_NAME = r"[A-Z][A-Za-z'’\-]{1,24}(?:\s+[A-Z][A-Za-z'’\-]{1,24}){0,2}"

_TITLE_NAME = re.compile(
    rf"\b(?P<title>Dr\.?|Doctor|Prof\.?|Professor|Nurse|NP|PA)\s+(?P<name>{_NAME})\b"
)
_NAME_CRED = re.compile(rf"\b(?P<name>{_NAME}),?\s+(?P<cred>M\.?D\.?|D\.?O\.?|N\.?P\.?|P\.?A\.?|R\.?N\.?)\b")
_ATTENDING_IS = re.compile(
    rf"\b(?:attending|internist|hospitalist|oncologist)\s+(?:is|was)\s+(?:Dr\.?\s+)?(?P<name>{_NAME})\b",
    re.I,
)
_ROLE_AFTER = re.compile(
    rf"\b(?P<name>{_NAME})\s*,?\s+(?:the\s+)?(?P<role>Internal Medicine(?:\s+attending)?|Hospitalist|"
    r"Oncologist|Oncology|Infectious Disease|Gastroenterology|Cardiology|Resident|Fellow|"
    r"Nurse Practitioner|Attending)\b",
    re.I,
)
_FAMILY_REL = re.compile(
    rf"\b(?P<rel>daughter|son|husband|wife|brother|sister|mother|father|mom|dad)\s+"
    rf"(?P<name>{_NAME})\b"
)

_ROLE_HINTS = (
    ("internal medicine", "Internal medicine"),
    ("internist", "Internal medicine"),
    ("hospitalist", "Hospitalist"),
    ("attending", "Attending"),
    ("oncolog", "Oncology"),
    ("infectious disease", "Infectious disease"),
    ("gastroenterolog", "Gastroenterology"),
    ("cardiolog", "Cardiology"),
    ("resident", "Resident"),
    ("fellow", "Fellow"),
    ("nurse practitioner", "Nurse practitioner"),
    ("nursing", "Nursing"),
)

_BAD_FIRST = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "our",
        "her",
        "his",
        "their",
        "she",
        "he",
        "on",
        "at",
        "in",
        "for",
        "and",
        "or",
    }
)
_SKIP = frozenset(
    {
        "internal medicine",
        "general medicine",
        "bright health",
        "home",
        "timeline",
        "family",
        "ask",
        "labs",
        "hospital",
        "cancer centre",
        "cancer center",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "today",
        "source",
        "document",
        "assessment",
        "the team",
        "care team",
        "point person",
        "nurse station",
        "internal",
        "medicine",
    }
)

_FAMILY_WORDS = (
    "daughter",
    "son",
    "husband",
    "wife",
    "brother",
    "sister",
    "mother",
    "father",
    "mom",
    "dad",
    "family",
)


def _bare_key(name: str) -> str:
    key = _name_key(name)
    for prefix in ("dr ", "doctor ", "prof ", "professor ", "nurse ", "np ", "pa "):
        if key.startswith(prefix):
            key = key[len(prefix) :]
    return key.strip()


def _display_name(title: str, name: str) -> str:
    cleaned = " ".join((name or "").split())
    title = (title or "").strip()
    if title.lower().startswith("dr") and not cleaned.lower().startswith("dr"):
        return f"Dr. {cleaned}"
    return cleaned


def _window(text: str, start: int, end: int, pad: int = 90) -> str:
    return text[max(0, start - pad) : min(len(text), end + pad)]


def _infer_role(window: str, explicit: str = "") -> str:
    if explicit:
        low = explicit.lower()
        if "internal" in low:
            return "Internal medicine"
        return " ".join(explicit.split())[:80]
    low = (window or "").lower()
    for hint, label in _ROLE_HINTS:
        if hint in low:
            return label
    return ""


def _infer_kind(window: str, forced: str = "") -> str:
    if forced:
        return forced
    low = (window or "").lower()
    return "family" if any(word in low for word in _FAMILY_WORDS) else "care"


def _skip_name(name: str, extra: set[str]) -> bool:
    bare = _bare_key(name)
    if not bare or bare in _SKIP or name.lower() in _SKIP:
        return True
    tokens = bare.split()
    if tokens and tokens[0] in _BAD_FIRST:
        return True
    if any(tok in _SKIP for tok in tokens):
        return True
    if bare in extra:
        return True
    if len(bare) < 3:
        return True
    return False


def extract_named_people(
    text: str,
    *,
    skip_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Return unique {name, role, kind} mentions that look like real people."""
    raw = str(text or "")
    extra = {_bare_key(n) for n in (skip_names or []) if _bare_key(n)}
    found: list[dict[str, str]] = []

    def add(name: str, *, title: str = "", role: str = "", kind: str = "", span: tuple[int, int] | None = None):
        display = _display_name(title, name)
        if _skip_name(display, extra):
            return
        start, end = span or (0, 0)
        window = _window(raw, start, end)
        item = {
            "name": display,
            "role": _infer_role(window, role),
            "kind": _infer_kind(window, kind),
        }
        key = _bare_key(display)
        for existing in found:
            if _bare_key(existing["name"]) == key:
                if len(display) > len(existing["name"]):
                    existing["name"] = display
                if item["role"] and not existing["role"]:
                    existing["role"] = item["role"]
                return
            if key in _bare_key(existing["name"]) or _bare_key(existing["name"]) in key:
                if len(display) > len(existing["name"]):
                    existing.update(item)
                elif item["role"] and not existing["role"]:
                    existing["role"] = item["role"]
                return
        found.append(item)

    for match in _TITLE_NAME.finditer(raw):
        add(match.group("name"), title=match.group("title"), span=match.span())
    for match in _NAME_CRED.finditer(raw):
        add(match.group("name"), title="Dr" if "d" in match.group("cred").lower() else "", span=match.span())
    for match in _ATTENDING_IS.finditer(raw):
        add(match.group("name"), title="Dr", role="Attending", kind="care", span=match.span())
    for match in _ROLE_AFTER.finditer(raw):
        if " " not in match.group("name").strip():
            continue
        add(match.group("name"), role=match.group("role"), kind="care", span=match.span())
    for match in _FAMILY_REL.finditer(raw):
        add(match.group("name"), kind="family", span=match.span())

    return found


def people_missing_from_contacts(
    people: list[dict[str, str]],
    *,
    care_team: list[dict[str, Any]] | None = None,
    family_team: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Drop anyone already saved on Family or the professional care team."""
    out: list[dict[str, str]] = []
    for person in people:
        name = person.get("name") or ""
        if find_team_member(care_team, name) or find_team_member(family_team, name):
            continue
        bare = _bare_key(name)
        already = False
        for member in list(care_team or []) + list(family_team or []):
            if _bare_key(member.get("name") or "") == bare:
                already = True
                break
            # Saved "Dr. Chen" should hide a later "Chen"
            member_bare = _bare_key(member.get("name") or "")
            if bare and member_bare and (bare == member_bare or bare.endswith(" " + member_bare) or member_bare.endswith(" " + bare)):
                already = True
                break
        if already:
            continue
        out.append(person)
    return out
