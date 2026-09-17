"""Parse lab / diagnostic readings from PDF/image text via the LLM."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from app.ingest.pdf import (
    extract_med_list_text,
    is_empty_med_extract,
)
from app.services.case_manager import (
    DIAGNOSTIC_PRESETS,
    _infer_diagnostic_category,
    _normalize_med_date,
    add_patient_diagnostic,
    get_patient_profile,
    group_diagnostics_for_charts,
    group_journal_for_charts,
)
from app.services.lab_patient_identity import (
    compare_lab_patient_identity,
    detect_lab_date_order,
    extract_lab_patient_identity,
    parse_lab_date_to_iso,
    prefer_plausible_lab_iso,
)
from app.services.llm import LLMClient

DIAG_IMPORT_SYSTEM = (
    "You extract laboratory and diagnostic test results from clinical lab reports and photos. "
    "Return ONLY a JSON array. No prose, no markdown fences."
)

DIAG_IMPORT_USER_TEMPLATE = """Extract every quantitative lab / diagnostic reading from the text below.

Return a JSON array. Each object may include:
- name (required string; use common clinical names, e.g. "LDL cholesterol", "HbA1c", "Triglyceride")
- value (required number)
- unit (string or null, e.g. "mmol/L", "U/L", "%")
- recorded_at (YYYY-MM-DD or null) — use the lab **collection / date of service**, not the print, fax, or WhatsApp date
- notes (string or null; e.g. fasting, HI/LO flags)
- category (optional: "blood", "imaging", "vital", or "other")

Rules:
- Prefer collection / specimen / date of service over report print date.
- Numeric dates: US labs (Quest, Labcorp, etc.) use **MM/DD/YYYY**; Canadian labs (LifeLabs, Dynacare) use **DD/MM/YYYY** or day-month-year text. Never swap month and day.
- Collection dates must not be in the future. If ambiguous, choose the interpretation that is not after today.
- If one collection date applies to the whole panel, use it for every row.
- Historical / multi-visit exports (several "Date of Service" or Lab No. sections, or date columns): emit a **separate row for every analyte on every collection date**. Do not collapse history to the latest visit.
- Do not invent values. Skip rows without a numeric result (ignore NEGATIVE / text-only).
- Prefer standard names when clear (LDL → "LDL cholesterol", HDL → "HDL cholesterol", non-HDL → "Non-HDL cholesterol").
- Include ratios and scores (e.g. Cholesterol/HDL ratio, coronary calcium) when present.
- Never emit duplicate rows for the same test on the same collection date (one row per analyte per date).
- If the document is empty or not a lab report, return [].

Known preferred names (use when matching):
{preset_names}

Text:
---
{text}
---
"""

_NAME_ALIASES = {
    # Lipids — longer / more specific keys first in fuzzy pass (sorted by len)
    "non-hdl cholesterol": "Non-HDL cholesterol",
    "non hdl cholesterol": "Non-HDL cholesterol",
    "non-hdl": "Non-HDL cholesterol",
    "non hdl": "Non-HDL cholesterol",
    "nonhdl": "Non-HDL cholesterol",
    "non-hdlc": "Non-HDL cholesterol",
    "ldl cholesterol": "LDL cholesterol",
    "ldl-c": "LDL cholesterol",
    "ldl": "LDL cholesterol",
    "hdl cholesterol": "HDL cholesterol",
    "hdl-c": "HDL cholesterol",
    "hdl": "HDL cholesterol",
    "total cholesterol": "Total cholesterol",
    "cholesterol": "Total cholesterol",
    "triglycerides": "Triglyceride",
    "triglyceride": "Triglyceride",
    "trig": "Triglyceride",
    "cholesterol/hdl ratio": "Cholesterol/HDL ratio",
    "cholesterol/hdl": "Cholesterol/HDL ratio",
    "chol/hdlc ratio": "Cholesterol/HDL ratio",
    "chol/hdlc": "Cholesterol/HDL ratio",
    "chol/hdl": "Cholesterol/HDL ratio",
    "tc/hdl": "Cholesterol/HDL ratio",
    # Glucose / A1c
    "hba1c": "HbA1c",
    "a1c": "HbA1c",
    "hemoglobin a1c": "HbA1c",
    "haemoglobin a1c": "HbA1c",
    "glycohemoglobin": "HbA1c",
    "glycohaemoglobin": "HbA1c",
    "glucose fasting": "Glucose fasting",
    "fasting glucose": "Glucose fasting",
    "glucose, fasting": "Glucose fasting",
    "glucose (fasting)": "Glucose fasting",
    "glucose random": "Glucose random",
    "glucose (random)": "Glucose random",
    "glucose, random": "Glucose random",
    "random glucose": "Glucose random",
    # Kidney / liver
    "creatinine": "Creatinine",
    "creatinine, serum": "Creatinine",
    "serum creatinine": "Creatinine",
    "egfr": "eGFR",
    "estimated gfr": "eGFR",
    "estimated glomerular filtration rate": "eGFR",
    "gfr estimated": "eGFR",
    "glomerular filtration rate (egfr)": "eGFR",
    "alt": "ALT",
    "alanine aminotransferase": "ALT",
    "alanine transaminase": "ALT",
    "alanine aminotransferase (alt)": "ALT",
    "alanine transaminase (alt)": "ALT",
    "sgpt": "ALT",
    "ast": "AST",
    "aspartate aminotransferase": "AST",
    "aspartate transaminase": "AST",
    "aspartate aminotransferase (ast)": "AST",
    "aspartate transaminase (ast)": "AST",
    "sgot": "AST",
    "alkaline phosphatase": "Alkaline Phosphatase",
    "alp": "Alkaline Phosphatase",
    "alk phos": "Alkaline Phosphatase",
    "alkaline phos": "Alkaline Phosphatase",
    "bilirubin total": "Bilirubin total",
    "total bilirubin": "Bilirubin total",
    "bilirubin, total": "Bilirubin total",
    "bilirubin": "Bilirubin total",
    "bilirubin direct": "Bilirubin direct",
    "direct bilirubin": "Bilirubin direct",
    "bilirubin, direct": "Bilirubin direct",
    "bilirubin indirect": "Bilirubin indirect",
    "indirect bilirubin": "Bilirubin indirect",
    "bilirubin, indirect": "Bilirubin indirect",
    "albumin": "Albumin",
    "protein, total": "Total protein",
    "total protein": "Total protein",
    "protein total": "Total protein",
    # CBC
    "hemoglobin": "Hemoglobin",
    "hgb": "Hemoglobin",
    "hb": "Hemoglobin",
    "haemoglobin": "Hemoglobin",
    "platelets": "Platelets",
    "plt": "Platelets",
    "platelet count": "Platelets",
    "wbc": "WBC",
    "white blood cell": "WBC",
    "white blood cells": "WBC",
    "white blood cell count": "WBC",
    "leukocytes": "WBC",
    "rbc": "RBC",
    "red blood cell": "RBC",
    "red blood cells": "RBC",
    "red blood cell count": "RBC",
    "hematocrit": "Hematocrit",
    "hct": "Hematocrit",
    "mcv": "MCV",
    "mch": "MCH",
    "mchc": "MCHC",
    "rdw": "RDW",
    "neutrophils": "Neutrophils",
    "lymphocytes": "Lymphocytes",
    "monocytes": "Monocytes",
    "eosinophils": "Eosinophils",
    "basophils": "Basophils",
    # Iron panel — keep TIBC / saturation distinct from Iron
    "iron": "Iron",
    "iron, total": "Iron",
    "iron total": "Iron",
    "serum iron": "Iron",
    "iron, serum": "Iron",
    "tibc": "TIBC",
    "total iron binding capacity": "TIBC",
    "iron binding capacity": "TIBC",
    "iron-binding capacity": "TIBC",
    "transferrin saturation": "Transferrin Saturation",
    "tsat": "Transferrin Saturation",
    "iron saturation": "Transferrin Saturation",
    "transferrin % saturation": "Transferrin Saturation",
    "% saturation": "Transferrin Saturation",
    "% saturation (iron)": "Transferrin Saturation",
    "ferritin": "Ferritin",
    # Vitamins / hormones / markers
    "tsh": "TSH",
    "thyroid stimulating hormone": "TSH",
    "thyroid-stimulating hormone": "TSH",
    "tsh w/reflex to ft4": "TSH",
    "folate": "Folate",
    "folate, serum": "Folate",
    "serum folate": "Folate",
    "folic acid": "Folate",
    "vitamin d 25-oh": "Vitamin D 25-OH",
    "vitamin d": "Vitamin D 25-OH",
    "vit d": "Vitamin D 25-OH",
    "25-oh vitamin d": "Vitamin D 25-OH",
    "25-hydroxy vitamin d": "Vitamin D 25-OH",
    "25 hydroxy vitamin d": "Vitamin D 25-OH",
    "vitamin d,25-oh,total,ia": "Vitamin D 25-OH",
    "vitamin d, 25-oh, total, ia": "Vitamin D 25-OH",
    "vitamin b12": "Vitamin B12",
    "b12": "Vitamin B12",
    "cobalamin": "Vitamin B12",
    "cyanocobalamin": "Vitamin B12",
    "crp": "CRP",
    "c reactive protein": "CRP",
    "c-reactive protein": "CRP",
    "hs-crp": "CRP",
    "high sensitivity crp": "CRP",
    "total psa": "Total PSA",
    "psa, total": "Total PSA",
    "prostate specific antigen": "Total PSA",
    "testosterone": "Testosterone",
    "ca19-9": "CA19-9",
    "cea": "CEA",
    "coronary calcium": "Coronary calcium score",
    "calcium score": "Coronary calcium score",
    "agatston": "Coronary calcium score",
    "coronary calcium score": "Coronary calcium score",
}

# Exact-only: bare "glucose" → fasting (common CA panel wording). Random must use explicit keys.
_NAME_ALIASES_EXACT_ONLY = {
    "glucose": "Glucose fasting",
    "psa": "Total PSA",
}


def _alias_fuzzy_blocked(lower: str, key: str, alias_name: str) -> bool:
    """Reject unsafe substring alias hits (US/CA naming collisions)."""
    if key == "hdl" and ("non-hdl" in lower or "non hdl" in lower or "nonhdl" in lower):
        return True
    if key in {"hdl", "hdl-c", "hdl cholesterol"} and (
        "chol/hdl" in lower or "cholesterol/hdl" in lower or "tc/hdl" in lower
    ):
        return True
    if alias_name == "Iron" and any(
        t in lower for t in ("binding", "tibc", "saturation", "tsat", "% sat")
    ):
        return True
    if alias_name == "Bilirubin total" and any(
        t in lower for t in ("direct", "indirect", "conjugated", "unconjugated")
    ):
        return True
    if alias_name == "Total PSA" and "free" in lower:
        return True
    if alias_name == "Hemoglobin" and any(
        t in lower for t in ("a1c", "a1 c", "glyco", "glycated")
    ):
        return True
    if alias_name == "Coronary calcium score" and "score" not in lower and "agatston" not in lower:
        return True
    if alias_name == "Albumin" and (
        "globulin" in lower or "a/g" in lower or "a:g" in lower
    ):
        return True
    if alias_name == "Vitamin D 25-OH" and ("1,25" in lower or "1.25" in lower):
        return True
    if alias_name == "LDL cholesterol" and "particle" in lower:
        return True
    if alias_name == "Glucose fasting" and "random" in lower:
        return True
    return False


def normalize_diagnostic_name(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return ""
    lower = cleaned.lower()
    # Normalize Quest-style commas/spaces for exact lookup
    compact = re.sub(r"\s*,\s*", ", ", lower)
    compact = re.sub(r"\s+", " ", compact).strip()

    for candidate in (lower, compact):
        alias = _NAME_ALIASES.get(candidate) or _NAME_ALIASES_EXACT_ONLY.get(candidate)
        if alias:
            return alias
    for preset in DIAGNOSTIC_PRESETS:
        if preset["name"].lower() == lower:
            return preset["name"]

    # Fuzzy: longest alias key first so "non hdl" wins over "hdl"
    for key, alias_name in sorted(_NAME_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if len(key) < 3 or key not in lower:
            continue
        if _alias_fuzzy_blocked(lower, key, alias_name):
            continue
        return alias_name

    for preset in sorted(DIAGNOSTIC_PRESETS, key=lambda p: len(p["name"]), reverse=True):
        pname = preset["name"].lower()
        if len(pname) < 3:
            continue
        if pname in lower or lower in pname:
            # Guard: bare "calcium" must not become coronary calcium score
            if preset["name"] == "Coronary calcium score" and "score" not in lower and "agatston" not in lower:
                continue
            if preset["name"] == "Iron" and any(
                t in lower for t in ("binding", "tibc", "saturation", "tsat")
            ):
                continue
            if preset["name"] == "Albumin" and "globulin" in lower:
                continue
            return preset["name"]
    return cleaned[:120]


def _strip_json_payload(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _clamp_str(value: Any, max_len: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    return text[:max_len]


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        return n if n == n else None
    text = str(value).strip().replace(",", "")
    text = re.sub(r"^[<>]=?\s*", "", text)
    try:
        n = float(text)
    except ValueError:
        return None
    return n if n == n else None


def clamp_proposed_diagnostic(
    raw: Any,
    *,
    date_order: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = normalize_diagnostic_name(str(raw.get("name") or ""))
    if not name:
        return None
    value = _parse_float(raw.get("value"))
    if value is None:
        return None
    recorded_raw = raw.get("recorded_at")
    recorded_at = None
    if recorded_raw not in (None, ""):
        raw_text = str(recorded_raw).strip()
        # Prefer locale-aware lab date parse (handles 09/11/2026 vs DD/MM)
        recorded_at = parse_lab_date_to_iso(raw_text, date_order=date_order)  # type: ignore[arg-type]
        if recorded_at is None:
            try:
                recorded_at = _normalize_med_date(raw_text)
            except ValueError:
                recorded_at = None
        if recorded_at is not None:
            recorded_at = prefer_plausible_lab_iso(
                recorded_at,
                date_order=date_order,  # type: ignore[arg-type]
            )
    unit = _clamp_str(raw.get("unit"), 40)
    if not unit:
        for preset in DIAGNOSTIC_PRESETS:
            if preset["name"].lower() == name.lower() and preset.get("unit"):
                unit = preset["unit"]
                break
    category = raw.get("category")
    if category not in {"blood", "imaging", "vital", "other"}:
        category = _infer_diagnostic_category(name, None)
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "recorded_at": recorded_at,
        "notes": _clamp_str(raw.get("notes"), 500),
        "category": category,
    }


def parse_diagnostics_json(
    raw: str,
    *,
    date_order: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    payload = _strip_json_payload(raw)
    if not payload:
        return [], ["Model returned empty response"]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return [], ["Could not parse diagnostics JSON from model response"]
    if not isinstance(data, list):
        return [], ["Model response was not a JSON array"]
    proposed: list[dict[str, Any]] = []
    skipped = 0
    for item in data:
        clamped = clamp_proposed_diagnostic(item, date_order=date_order)
        if clamped is None:
            skipped += 1
            continue
        proposed.append(clamped)
    if skipped:
        warnings.append(f"Skipped {skipped} invalid row(s)")
    return proposed, warnings


_SERVICE_DATE_RE = re.compile(
    r"(?im)(?:Date\s+of\s+Service|Collected|Collection\s+Date|Specimen\s+Date|Drawn)\s*[:\-]?\s*"
    r"("
    r"[A-Za-z]{3,9}\s+\d{1,2}\s+\d{2,4}"  # Jul 03 2026
    r"|\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4}"  # 03-JUL-2026
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    r")"
)

_LIFELABS_DOS_RE = re.compile(
    r"(?im)Date\s+of\s+Service:\s*([A-Za-z]{3,9}\s+\d{1,2}\s+\d{2,4})"
)

_LIFELABS_SECTION_START_RE = re.compile(
    r"(?im)(?:^|\n)(?:Lab\s+No:\s*\S+|Date\s+of\s+Service:\s*[A-Za-z]{3,9}\s+\d{1,2}\s+\d{2,4})"
)

# (canonical name, line-start pattern, default unit). Longer / more specific first.
_LIFELABS_LINE_TESTS: list[tuple[str, str, str]] = [
    ("Immature Granulocytes", r"Immature\s+Granulocytes", "x E9/L"),
    ("Non-HDL cholesterol", r"Non\s*[- ]?\s*HDL\s+Cholesterol", "mmol/L"),
    ("HDL cholesterol", r"HDL\s+Cholesterol", "mmol/L"),
    ("LDL cholesterol", r"LDL\s+Cholesterol", "mmol/L"),
    ("Cholesterol/HDL ratio", r"Chol(?:esterol)?/HDL(?:\s+Ratio)?", None),
    ("Total cholesterol", r"Cholesterol(?!\s*/)", "mmol/L"),
    ("Triglyceride", r"Triglycerides?", "mmol/L"),
    ("Vitamin D 25-OH", r"25-Hydroxy\s+Vitamin\s+D", "nmol/L"),
    ("Vitamin B12", r"Vitamin\s+B12", "pmol/L"),
    ("TSH", r"(?:Thyroid\s+Stimulating\s+Hormone|TSH)", "mIU/L"),
    ("CRP", r"C\s*Reactive\s+Protein", "mg/L"),
    ("ESR", r"Erythrocyte\s+Sedimentation\s+Rate", "mm/hr"),
    ("eGFR", r"Glomerular\s+Filtration\s+Rate\s*\(eGFR\)", "mL/min/1.73m2"),
    ("ALT", r"Alanine\s+Aminotransferase(?:\s*\(ALT\))?", "U/L"),
    ("AST", r"Aspartate\s+Aminotransferase(?:\s*\(AST\))?", "U/L"),
    ("Alkaline Phosphatase", r"Alkaline\s+Phosphatase", "U/L"),
    ("Bilirubin total", r"Bilirubin\s+Total", "umol/L"),
    ("Glucose fasting", r"Glucose\s+Fasting", "mmol/L"),
    ("Glucose random", r"Glucose\s*\(\s*Random\s*\)", "mmol/L"),
    ("HbA1c", r"(?:Hemoglobin\s+A1[cC]|HbA1[cC])", "%"),
    ("Platelets", r"Platelet(?:\s+Count)?", "x E9/L"),
    ("Neutrophils", r"Neutrophils", "x E9/L"),
    ("Lymphocytes", r"Lymphocytes", "x E9/L"),
    ("Monocytes", r"Monocytes", "x E9/L"),
    ("Eosinophils", r"Eosinophils", "x E9/L"),
    ("Basophils", r"Basophils", "x E9/L"),
    ("Hemoglobin", r"Hemoglobin(?!\s+A1)", "g/L"),
    ("Hematocrit", r"Hematocrit", "L/L"),
    ("Creatinine", r"Creatinine(?!\s*\()", "umol/L"),
    ("Albumin", r"Albumin(?!\s*\()", "g/L"),
    ("Ferritin", r"Ferritin", "ug/L"),
    ("Magnesium", r"Magnesium", "mmol/L"),
    ("Calcium", r"Calcium", "mmol/L"),
    ("Sodium", r"Sodium", "mmol/L"),
    ("Potassium", r"Potassium", "mmol/L"),
    ("Chloride", r"Chloride", "mmol/L"),
    ("MCHC", r"MCHC", "g/L"),
    ("MCV", r"MCV", "fL"),
    ("MCH", r"MCH", "pg"),
    ("RDW", r"RDW", "%"),
    ("WBC", r"WBC", "x E9/L"),
    ("RBC", r"RBC(?!\s+Morphology)", "x E12/L"),
]

_LIFELABS_VALUE_TAIL = (
    r"\s+(?:(?P<flag>HI|LO|HH|LL)\s+)?"
    r"(?P<ineq>[<>]=?)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\b"
    r"(?:\s+(?P<unit>"
    r"x\s*E9/L|x\s*E12/L|U/L|nmol/L|pmol/L|mIU/L|ug/L|µg/L|g/L|mmol/L|mg/L|"
    r"umol/L|µmol/L|mm/hr|mm/h|fL|pg|L/L|%|mL/min(?:/1\.73m2)?"
    r"))?"
)


def _parse_lab_service_date(text: str, *, date_order: str | None = None) -> str | None:
    order = date_order or detect_lab_date_order(text)
    m = _SERVICE_DATE_RE.search(text or "")
    if not m:
        return None
    return parse_lab_date_to_iso(m.group(1).strip(), date_order=order)  # type: ignore[arg-type]


def _parse_lifelabs_dos(text: str) -> str | None:
    m = _LIFELABS_DOS_RE.search(text or "")
    if m:
        return parse_lab_date_to_iso(m.group(1).strip(), date_order="dmy")
    return _parse_lab_service_date(text, date_order="dmy")


def _split_lifelabs_sections(text: str) -> list[str]:
    body = str(text or "")
    if not body.strip():
        return []
    starts = [m.start() for m in _LIFELABS_SECTION_START_RE.finditer(body)]
    if not starts:
        return [body]
    if starts[0] > 0:
        starts = [0] + starts
    sections: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(body)
        chunk = body[start:end].strip()
        if chunk:
            sections.append(chunk)
    return sections


def _parse_lifelabs_section_rows(section: str, service_date: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for display_name, name_pat, default_unit in _LIFELABS_LINE_TESTS:
        pat = re.compile(rf"(?im)^(?:{name_pat}){_LIFELABS_VALUE_TAIL}")
        m = pat.search(section)
        if not m:
            continue
        key = display_name.lower()
        if key in seen:
            continue
        try:
            value = float(m.group("value"))
        except (TypeError, ValueError):
            continue
        unit = (m.group("unit") or default_unit or "").strip() or default_unit
        if unit:
            unit = re.sub(r"\s+", " ", unit)
        flag = (m.group("flag") or "").strip().upper()
        ineq = (m.group("ineq") or "").strip()
        notes_parts: list[str] = []
        if flag in {"LO", "L", "LL"}:
            notes_parts.append("LO")
        elif flag in {"HI", "H", "HH"}:
            notes_parts.append("HI")
        if ineq:
            notes_parts.append(f"{ineq}{m.group('value')}")
        if display_name == "Glucose fasting":
            notes_parts.append("fasting")
        row = clamp_proposed_diagnostic(
            {
                "name": display_name,
                "value": value,
                "unit": unit,
                "recorded_at": service_date,
                "notes": "; ".join(dict.fromkeys(notes_parts)) or None,
                "category": "blood",
            }
        )
        if row:
            seen.add(key)
            found.append(row)
    return found


def heuristic_parse_lifelabs_history(text: str) -> list[dict[str, Any]]:
    """Parse LifeLabs multi-visit PDF exports: one row per analyte per Date of Service."""
    body = str(text or "")
    if not body.strip():
        return []
    dos_hits = _LIFELABS_DOS_RE.findall(body)
    # Prefer sectioned parse when the export spans multiple visits or many pages.
    if len({h.strip() for h in dos_hits}) < 2 and "Lab No:" not in body:
        return []

    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for section in _split_lifelabs_sections(body):
        service_date = _parse_lifelabs_dos(section)
        if not service_date:
            continue
        for row in _parse_lifelabs_section_rows(section, service_date):
            key = diagnostic_identity_key(row.get("name"), row.get("recorded_at"))
            if key and key in seen_keys:
                continue
            if key:
                seen_keys.add(key)
            rows.append(row)
    return rows


# Common LifeLabs / Canadian panel rows: NAME … VALUE [FLAG] [REF] UNIT
_HEURISTIC_LAB_TESTS: list[tuple[str, str, str]] = [
    ("Alanine Transaminase (ALT)", r"ALANINE\s+TRANSAMINASE(?:\s*\(ALT\))?|\bALT\b", "U/L"),
    ("25-Hydroxy Vitamin D", r"25-HYDROXY\s+VITAMIN\s+D|VITAMIN\s+D\b", "nmol/L"),
    ("Vitamin B12", r"VITAMIN\s+B12|\bB12\b", "pmol/L"),
    ("Thyroid Stimulating Hormone", r"THYROID\s+STIMULATING\s+HORMONE|\bTSH\b", "mIU/L"),
    ("Ferritin", r"\bFERRITIN\b", "ug/L"),
    ("Hemoglobin", r"\bHEMOGLOBIN\b|\bHGB\b|\bHB\b", "g/L"),
    ("Creatinine", r"\bCREATININE\b", "umol/L"),
    ("Glucose", r"\bGLUCOSE\b", "mmol/L"),
    ("HDL Cholesterol", r"\bHDL\b", "mmol/L"),
    ("LDL Cholesterol", r"\bLDL\b", "mmol/L"),
    ("Triglycerides", r"\bTRIGLYCERIDES?\b", "mmol/L"),
    ("HbA1c", r"\bHBA1C\b|HEMOGLOBIN\s+A1C", "%"),
]


def heuristic_parse_lab_panel(text: str) -> list[dict[str, Any]]:
    """Regex fallback for clear panel layouts (e.g. LifeLabs) when LLM returns nothing."""
    history = heuristic_parse_lifelabs_history(text)
    if history:
        return history

    body = str(text or "")
    if not body.strip():
        return []
    service_date = _parse_lab_service_date(body)
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for display_name, name_pat, default_unit in _HEURISTIC_LAB_TESTS:
        # Allow OCR to smash columns together: NAME value [LO|HI] [ref] unit
        pat = re.compile(
            rf"(?is)({name_pat})[^\n\d]{{0,40}}?"
            rf"(?P<value>\d{{1,4}}(?:\.\d{{1,3}})?)\s*"
            rf"(?:(?P<flag>LO|HI|H|L)\b)?\s*"
            rf"(?:(?P<ref><\s*\d+(?:\.\d+)?|>\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?)\s*)?"
            rf"(?P<unit>U/L|nmol/L|pmol/L|mIU/L|ug/L|µg/L|g/L|mmol/L|mg/L|umol/L|%|IU/L)?",
        )
        m = pat.search(body)
        if not m:
            continue
        key = display_name.lower()
        if key in seen:
            continue
        try:
            value = float(m.group("value"))
        except (TypeError, ValueError):
            continue
        unit = (m.group("unit") or default_unit or "").strip() or default_unit
        flag = (m.group("flag") or "").strip().upper()
        notes = None
        if flag in {"LO", "L"}:
            notes = "LO"
        elif flag in {"HI", "H"}:
            notes = "HI"
        row = clamp_proposed_diagnostic(
            {
                "name": display_name,
                "value": value,
                "unit": unit,
                "recorded_at": service_date,
                "notes": notes,
                "category": "blood",
            }
        )
        if row:
            seen.add(key)
            found.append(row)
    return found


def _merge_proposed_readings(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union by (name, date); keep primary row when both present."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in list(primary) + list(secondary):
        key = diagnostic_identity_key(row.get("name"), row.get("recorded_at"))
        if key:
            if key in seen:
                continue
            seen.add(key)
        merged.append(row)
    return merged


def diagnostic_identity_key(
    name: str | None,
    recorded_at: str | None,
) -> tuple[str, str] | None:
    """Normalized (name, date) key used to detect duplicate lab readings."""
    norm = normalize_diagnostic_name(str(name or ""))
    date = str(recorded_at or "").strip()[:10]
    if not norm or not date or len(date) < 10:
        return None
    return (norm.lower(), date)


def existing_diagnostic_index(patient_id: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Map normalized (name, date) → existing profile diagnostic rows."""
    profile = get_patient_profile(patient_id)
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in profile.get("diagnostics") or []:
        key = diagnostic_identity_key(row.get("name"), row.get("recorded_at"))
        if not key:
            continue
        index.setdefault(key, []).append(row)
    return index


def is_duplicate_diagnostic(
    candidate: dict[str, Any],
    *,
    existing_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> bool:
    """True when a same normalized name + collection date is already on the profile."""
    key = diagnostic_identity_key(candidate.get("name"), candidate.get("recorded_at"))
    if not key:
        return False
    return bool(existing_index.get(key))


def annotate_proposed_duplicates(
    proposed: list[dict[str, Any]],
    patient_id: str,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Mark proposed rows that already exist; drop within-batch duplicates."""
    existing = existing_diagnostic_index(patient_id)
    seen_in_batch: set[tuple[str, str]] = set()
    annotated: list[dict[str, Any]] = []
    overlap_labels: list[str] = []
    duplicate_count = 0
    within_batch = 0

    for raw in proposed:
        row = dict(raw)
        key = diagnostic_identity_key(row.get("name"), row.get("recorded_at"))
        if key and key in seen_in_batch:
            within_batch += 1
            duplicate_count += 1
            continue
        already = bool(key and is_duplicate_diagnostic(row, existing_index=existing))
        row["already_on_profile"] = already
        if already:
            duplicate_count += 1
            overlap_labels.append(
                f"{row.get('name')} ({row.get('recorded_at') or 'no date'})"
            )
        if key:
            seen_in_batch.add(key)
        annotated.append(row)

    warnings: list[str] = []
    if within_batch:
        warnings.append(
            f"Removed {within_batch} duplicate row(s) within this report (same test + date)"
        )
    if overlap_labels:
        sample = ", ".join(overlap_labels[:5])
        more = f" (+{len(overlap_labels) - 5} more)" if len(overlap_labels) > 5 else ""
        warnings.append(f"Already on profile for same date: {sample}{more}")
    return annotated, warnings, duplicate_count


def soft_overlap_warnings(
    proposed: list[dict[str, Any]],
    patient_id: str,
) -> list[str]:
    _, warnings, _ = annotate_proposed_duplicates(proposed, patient_id)
    return warnings


def _preset_names_for_prompt() -> str:
    return ", ".join(p["name"] for p in DIAGNOSTIC_PRESETS)


def document_original_filename(doc: dict[str, Any] | None) -> str | None:
    """Best available original upload filename for a library document."""
    if not doc:
        return None
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    for key in ("original_filename", "filename", "relative_path"):
        raw = str(meta.get(key) or "").strip()
        if raw:
            return Path(raw).name
    title = str(doc.get("title") or "").strip()
    if title and "." in title and not title.lower().startswith("lab results"):
        return title
    return None


def _lab_import_doc_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": str(doc.get("id") or "").strip() or None,
        "document_title": doc.get("title"),
        "original_filename": document_original_filename(doc),
    }


def _empty_lab_import_result(
    patient_id: str,
    doc: dict[str, Any],
    *,
    skipped_duplicate: int = 0,
    proposed_count: int = 0,
    warnings: list[str] | None = None,
    patient_identity: dict[str, Any] | None = None,
    patient_mismatch: bool = False,
    blocked_for_patient_mismatch: bool = False,
) -> dict[str, Any]:
    profile = get_patient_profile(patient_id)
    return {
        "added": [],
        "added_count": 0,
        "proposed_count": proposed_count,
        "skipped_duplicate": skipped_duplicate,
        "skipped_incomplete": 0,
        "errors": [],
        "warnings": list(warnings or []),
        **_lab_import_doc_fields(doc),
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
        "offer_manual_import": False,
        "already_on_profile": skipped_duplicate > 0,
        "patient_identity": patient_identity,
        "patient_mismatch": patient_mismatch,
        "blocked_for_patient_mismatch": blocked_for_patient_mismatch,
    }


async def _propose_from_text(
    patient_id: str,
    text: str,
    *,
    meta: dict[str, Any] | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    if is_empty_med_extract(text):
        raise ValueError("No readable text found for lab import.")

    date_order = detect_lab_date_order(text)
    if date_order and meta is not None:
        meta = {**(meta or {}), "lab_date_order": date_order}

    heuristic_full = heuristic_parse_lab_panel(text)
    # Re-clamp heuristic rows with locale so US Quest dates stay MM/DD
    if heuristic_full and date_order:
        rescanned: list[dict[str, Any]] = []
        for row in heuristic_full:
            clamped = clamp_proposed_diagnostic(row, date_order=date_order)
            if clamped:
                rescanned.append(clamped)
        if rescanned:
            heuristic_full = rescanned

    heuristic_dates = {
        str(r.get("recorded_at") or "")[:10]
        for r in heuristic_full
        if r.get("recorded_at")
    }
    prefer_heuristic = len(heuristic_dates) >= 2 or len(heuristic_full) >= 20

    clipped = text if len(text) <= 28000 else text[:28000] + "\n…[truncated]"
    if prefer_heuristic and len(text) > 28000:
        warnings.append(
            f"Long multi-visit lab export ({len(text)} chars) — used dated table parser "
            f"for {len(heuristic_full)} reading(s) across {len(heuristic_dates)} date(s)"
        )

    proposed: list[dict[str, Any]] = []
    if prefer_heuristic:
        proposed = list(heuristic_full)
        if meta is not None:
            meta = {
                **(meta or {}),
                "parse_method": "heuristic_lifelabs_history",
                "extraction_method": (meta or {}).get("extraction_method") or "heuristic",
            }
    else:
        client = llm or LLMClient()
        try:
            raw = await client.chat(
                messages=[
                    {"role": "system", "content": DIAG_IMPORT_SYSTEM},
                    {
                        "role": "user",
                        "content": DIAG_IMPORT_USER_TEMPLATE.format(
                            text=clipped,
                            preset_names=_preset_names_for_prompt(),
                        ),
                    },
                ],
                temperature=0.1,
            )
        except Exception as exc:
            raise ValueError(f"Could not parse lab results with LLM: {exc}") from exc

        proposed, parse_warnings = parse_diagnostics_json(raw, date_order=date_order)
        warnings.extend(parse_warnings)
        if heuristic_full:
            before = len(proposed)
            proposed = _merge_proposed_readings(proposed, heuristic_full)
            gained = len(proposed) - before
            if gained > 0:
                warnings.append(
                    f"Merged {gained} additional reading(s) from table parser"
                )
            elif not proposed and heuristic_full:
                proposed = heuristic_full
                warnings.append(
                    f"Used table fallback parser ({len(heuristic_full)} reading(s)) "
                    "after model returned none"
                )
                if meta is not None:
                    meta = {
                        **(meta or {}),
                        "extraction_method": (meta or {}).get("extraction_method")
                        or "heuristic",
                        "parse_method": "heuristic_panel",
                    }
        elif not proposed:
            warnings.append("No lab readings detected in the document")

    # Final pass: fix future ambiguous ISOs (e.g. Quest 09/11/2026 → 2026-11-09)
    for row in proposed:
        fixed = prefer_plausible_lab_iso(
            row.get("recorded_at"),
            date_order=date_order,  # type: ignore[arg-type]
        )
        if fixed:
            row["recorded_at"] = fixed

    proposed, overlap_warnings, _ = annotate_proposed_duplicates(proposed, patient_id)
    warnings.extend(overlap_warnings)
    missing_dates = sum(1 for p in proposed if not p.get("recorded_at"))
    if missing_dates:
        warnings.append(
            f"{missing_dates} reading(s) missing a collection date — set dates before confirming"
        )
    if not proposed:
        warnings.append("No lab readings detected in the document")

    extracted_id = extract_lab_patient_identity(text)
    identity = compare_lab_patient_identity(patient_id, extracted_id)
    if identity.get("soft_warnings"):
        warnings.extend(identity["soft_warnings"])
    if identity.get("issues"):
        warnings.extend(identity["issues"])

    return {
        "proposed": proposed,
        "extraction_meta": meta or {},
        "warnings": warnings,
        "extracted_preview": clipped[:800],
        "duplicate_count": sum(1 for p in proposed if p.get("already_on_profile")),
        "patient_identity": identity,
        "patient_mismatch": bool(identity.get("mismatch")),
    }


async def propose_diagnostics_from_upload(
    patient_id: str,
    content: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    text, meta = await asyncio.to_thread(
        extract_med_list_text,
        content,
        content_type=content_type,
        filename=filename,
    )
    if is_empty_med_extract(text):
        hint = (meta or {}).get("ocr_hint")
        msg = "No readable text found in the upload."
        if hint:
            msg = f"{msg} {hint}"
        raise ValueError(msg)
    meta = dict(meta or {})
    if filename:
        meta.setdefault("original_filename", Path(filename).name)
    return await _propose_from_text(patient_id, text, meta=meta, llm=llm)


async def propose_diagnostics_from_document(
    patient_id: str,
    doc: dict[str, Any],
    *,
    extracted_text: str | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Propose lab readings from an existing library document."""
    text = (extracted_text or "").strip()
    meta: dict[str, Any] = {
        "source": "library_document",
        "document_id": doc.get("id"),
        "title": doc.get("title"),
        "source_type": doc.get("source_type"),
        "original_filename": document_original_filename(doc),
    }
    doc_meta = doc.get("metadata") or {}
    if isinstance(doc_meta, dict):
        meta["extraction_method"] = doc_meta.get("extraction_method")
        meta["extracted_chars"] = doc_meta.get("extracted_chars")
        if not meta.get("original_filename") and doc_meta.get("original_filename"):
            meta["original_filename"] = Path(str(doc_meta["original_filename"])).name

    if is_empty_med_extract(text):
        from app.services.document_paths import resolve_document_file_path

        path = resolve_document_file_path(doc)
        if not path:
            raise ValueError(
                "This document’s PDF is missing on disk. Open Library → Replace file, "
                "upload the PDF again, then Import to Labs."
            )
        content = await asyncio.to_thread(path.read_bytes)
        filename = path.name
        ctype = "application/pdf" if filename.lower().endswith(".pdf") else None
        text, file_meta = await asyncio.to_thread(
            extract_med_list_text,
            content,
            content_type=ctype,
            filename=filename,
        )
        meta.update(file_meta or {})
        if is_empty_med_extract(text):
            hint = meta.get("ocr_hint")
            msg = "No readable text found in the document."
            if hint:
                msg = f"{msg} {hint}"
            raise ValueError(msg)

    from app.ingest.pdf import local_image_ocr_usable_for_labs

    if text and not local_image_ocr_usable_for_labs(text):
        vision = await _vision_reread_lab_image(doc)
        if vision:
            vision_text, vision_meta = vision
            text = vision_text
            meta = {
                **meta,
                **(vision_meta or {}),
                "extraction_method": "vision_ocr",
                "extracted_chars": len(vision_text),
                "parse_method": "vision_reread",
            }

    result = await _propose_from_text(patient_id, text, meta=meta, llm=llm)
    if result.get("proposed"):
        if meta.get("parse_method") == "vision_reread":
            result["vision_transcript"] = text
        return result

    # Stored tesseract can be long but unreadable. Re-read photos with vision and parse again.
    if meta.get("parse_method") == "vision_reread":
        return result
    vision = await _vision_reread_lab_image(doc)
    if not vision:
        return result
    vision_text, vision_meta = vision
    meta = {
        **meta,
        **(vision_meta or {}),
        "extraction_method": "vision_ocr",
        "extracted_chars": len(vision_text),
        "parse_method": "vision_reread",
    }
    retried = await _propose_from_text(patient_id, vision_text, meta=meta, llm=llm)
    retried["vision_transcript"] = vision_text
    if not retried.get("proposed"):
        warnings = list(retried.get("warnings") or [])
        warnings.append(
            "Photo OCR could not be parsed. Vision re-read also found no lab rows — "
            "check the preview, or enter readings manually."
        )
        retried["warnings"] = warnings
    return retried


async def _vision_reread_lab_image(
    doc: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    from app.services.document_paths import resolve_document_file_path
    from app.ingest.pdf import extract_image_text_async

    path = resolve_document_file_path(doc)
    if not path or not path.exists():
        return None
    name = path.name.lower()
    orig = str((doc.get("metadata") or {}).get("original_filename") or "").lower()
    if not name.endswith((".jpg", ".jpeg", ".png", ".webp")) and not orig.endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    ):
        return None
    content = await asyncio.to_thread(path.read_bytes)
    text, file_meta = await extract_image_text_async(content, filename=path.name)
    method = str((file_meta or {}).get("extraction_method") or "")
    if method not in {"vision_ocr", "vision_ocr_thin"}:
        return None
    cleaned = (text or "").strip()
    if not cleaned or is_empty_med_extract(cleaned):
        return None
    return cleaned, file_meta or {}


def _profile_readings_for_document(patient_id: str, doc_id: str | None) -> int:
    if not doc_id:
        return 0
    profile = get_patient_profile(patient_id)
    return sum(
        1
        for d in (profile.get("diagnostics") or [])
        if str(d.get("source_document_id") or "") == doc_id
    )


async def auto_confirm_lab_readings_from_document(
    patient_id: str,
    doc: dict[str, Any],
    *,
    extracted_text: str | None = None,
    llm: LLMClient | None = None,
    acknowledge_patient_mismatch: bool = False,
) -> dict[str, Any]:
    """Propose lab rows and auto-add complete, non-duplicate readings.

    Only rows with name + value + recorded_at are confirmed. Same normalized
    name + collection date already on the profile are skipped (including
    within-batch duplicates). Manual Import to Labs remains the fallback.

    If the report name or DOB conflicts with the active patient and
    ``acknowledge_patient_mismatch`` is false, readings are not written and the
    result includes ``blocked_for_patient_mismatch`` so the UI can ask the user.
    """
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_id = str(doc.get("id") or "").strip() or None

    # Reconstructed chart entries / docs already linked to readings — do not re-parse.
    if meta.get("backfill_from_diagnostics"):
        linked = _profile_readings_for_document(patient_id, doc_id)
        return _empty_lab_import_result(
            patient_id,
            doc,
            skipped_duplicate=max(linked, int(meta.get("reading_count") or 0)),
            proposed_count=max(linked, int(meta.get("reading_count") or 0)),
            warnings=["Library entry was built from existing chart readings — skipped re-import"],
        )
    # Identity check before LLM parse when text is already available (cheap)
    text_for_id = (extracted_text or "").strip()
    identity_preview = None
    if text_for_id:
        identity_preview = compare_lab_patient_identity(
            patient_id, extract_lab_patient_identity(text_for_id)
        )

    proposal = await propose_diagnostics_from_document(
        patient_id,
        doc,
        extracted_text=extracted_text,
        llm=llm,
    )
    proposed = proposal.get("proposed") or []
    identity = proposal.get("patient_identity") or identity_preview
    patient_mismatch = bool(
        (identity and identity.get("mismatch")) or proposal.get("patient_mismatch")
    )

    if patient_mismatch and not acknowledge_patient_mismatch:
        issues = list((identity or {}).get("issues") or [])
        warnings = list(proposal.get("warnings") or [])
        warnings.append(
            "Import blocked — lab report name/DOB does not match the active patient. "
            "Confirm to import anyway, or switch patients first."
        )
        return _empty_lab_import_result(
            patient_id,
            doc,
            proposed_count=len(proposed),
            warnings=warnings,
            patient_identity=identity,
            patient_mismatch=True,
            blocked_for_patient_mismatch=True,
        ) | {
            "proposed": proposed,
            "offer_manual_import": True,
            "mismatch_issues": issues,
        }

    existing = existing_diagnostic_index(patient_id)
    added: list[dict[str, Any]] = []
    skipped_duplicate = 0
    skipped_incomplete = 0
    errors: list[str] = []
    seen_in_batch: set[tuple[str, str]] = set()

    for raw in proposed:
        name = str(raw.get("name") or "").strip()
        recorded_at = str(raw.get("recorded_at") or "").strip()[:10]
        if not name or raw.get("value") is None or not recorded_at:
            skipped_incomplete += 1
            continue
        key = diagnostic_identity_key(name, recorded_at)
        if not key:
            skipped_incomplete += 1
            continue
        if key in seen_in_batch or is_duplicate_diagnostic(
            {"name": name, "recorded_at": recorded_at, "value": raw.get("value")},
            existing_index=existing,
        ):
            skipped_duplicate += 1
            seen_in_batch.add(key)
            continue
        try:
            entry = add_patient_diagnostic(
                patient_id,
                name=name,
                value=float(raw["value"]),
                recorded_at=recorded_at,
                unit=raw.get("unit"),
                notes=raw.get("notes"),
                category=raw.get("category"),
                source_document_id=doc_id,
            )
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if entry:
            added.append(entry)
            seen_in_batch.add(key)
            existing.setdefault(key, []).append(entry)

    profile = get_patient_profile(patient_id)
    warnings = list(proposal.get("warnings") or [])
    if acknowledge_patient_mismatch and patient_mismatch:
        warnings.append("Imported after user confirmed a patient name/DOB mismatch")
    # Drop soft-overlap warning text if we already counted skips in this pass
    if skipped_duplicate:
        warnings = [
            w
            for w in warnings
            if not str(w).startswith("Already on profile for same date:")
        ]
        warnings.append(f"Skipped {skipped_duplicate} duplicate name+date reading(s)")
    if skipped_incomplete:
        warnings.append(
            f"Skipped {skipped_incomplete} incomplete reading(s) (need name, value, date)"
        )

    return {
        "added": added,
        "added_count": len(added),
        "proposed_count": len(proposed),
        "skipped_duplicate": skipped_duplicate,
        "skipped_incomplete": skipped_incomplete,
        "errors": errors,
        "warnings": warnings,
        **_lab_import_doc_fields(doc),
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
        "offer_manual_import": (
            len(added) == 0
            and skipped_incomplete > 0
            and skipped_duplicate < len(proposed)
        )
        or (len(added) == 0 and len(proposed) > 0 and skipped_duplicate == 0),
        "already_on_profile": len(added) == 0
        and skipped_duplicate > 0
        and skipped_incomplete == 0,
        "patient_identity": identity,
        "patient_mismatch": patient_mismatch,
        "blocked_for_patient_mismatch": False,
        "mismatch_issues": list((identity or {}).get("issues") or []) if patient_mismatch else [],
    }
