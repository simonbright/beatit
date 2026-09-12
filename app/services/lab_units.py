"""Convert common lab analytes between Canadian SI and US conventional units.

Original report values stay in ``value`` / ``unit``. Display systems are stored as:
- ``value_si`` / ``unit_si`` — Canadian SI (mmol/L, g/L, µmol/L, …)
- ``value_us`` / ``unit_us`` — US conventional (mg/dL, g/dL, …)
- ``unit_system_original`` — ``si`` | ``us`` | ``same`` | ``unknown``
"""

from __future__ import annotations

import re
from typing import Any, Literal

UnitSystem = Literal["si", "us", "same", "unknown"]

# Canonical display units per analyte family (SI / US).
_SI_UNITS: dict[str, str] = {
    "ldl cholesterol": "mmol/L",
    "hdl cholesterol": "mmol/L",
    "non-hdl cholesterol": "mmol/L",
    "total cholesterol": "mmol/L",
    "triglyceride": "mmol/L",
    "glucose fasting": "mmol/L",
    "glucose random": "mmol/L",
    "glucose": "mmol/L",
    "creatinine": "µmol/L",
    "bilirubin total": "µmol/L",
    "hemoglobin": "g/L",
    "hematocrit": "L/L",
    "albumin": "g/L",
    "calcium": "mmol/L",
    "magnesium": "mmol/L",
    "vitamin d 25-oh": "nmol/L",
    "vitamin b12": "pmol/L",
    "ferritin": "µg/L",
    "folate": "nmol/L",
    "iron": "µmol/L",
    "tibc": "µmol/L",
    "total psa": "µg/L",
    "testosterone": "nmol/L",
    "mchc": "g/L",
    "wbc": "x E9/L",
    "rbc": "x E12/L",
    "platelets": "x E9/L",
    "neutrophils": "x E9/L",
    "lymphocytes": "x E9/L",
    "monocytes": "x E9/L",
    "eosinophils": "x E9/L",
    "basophils": "x E9/L",
}

_US_UNITS: dict[str, str] = {
    "ldl cholesterol": "mg/dL",
    "hdl cholesterol": "mg/dL",
    "non-hdl cholesterol": "mg/dL",
    "total cholesterol": "mg/dL",
    "triglyceride": "mg/dL",
    "glucose fasting": "mg/dL",
    "glucose random": "mg/dL",
    "glucose": "mg/dL",
    "creatinine": "mg/dL",
    "bilirubin total": "mg/dL",
    "hemoglobin": "g/dL",
    "hematocrit": "%",
    "albumin": "g/dL",
    "calcium": "mg/dL",
    "magnesium": "mg/dL",
    "vitamin d 25-oh": "ng/mL",
    "vitamin b12": "pg/mL",
    "ferritin": "ng/mL",
    "folate": "ng/mL",
    "iron": "µg/dL",
    "tibc": "µg/dL",
    "total psa": "ng/mL",
    "testosterone": "ng/dL",
    "mchc": "g/dL",
    "wbc": "Thousand/uL",
    "rbc": "Million/uL",
    "platelets": "Thousand/uL",
    "neutrophils": "Thousand/uL",
    "lymphocytes": "Thousand/uL",
    "monocytes": "Thousand/uL",
    "eosinophils": "Thousand/uL",
    "basophils": "Thousand/uL",
}

# Names that must never be converted from lipid/glucose factors even if unit looks right.
_BLOCKED_NAMES = {
    "coronary calcium score",
    "calcium score",
    "cac",
    "bmi",
    "weight",
    "height",
}

# Analyte families for conversion rules.
_LIPID = {
    "ldl cholesterol",
    "hdl cholesterol",
    "non-hdl cholesterol",
    "total cholesterol",
}
_TRIG = {"triglyceride", "triglycerides"}
_GLUCOSE = {"glucose fasting", "glucose random", "glucose", "fasting glucose"}
_CREAT = {"creatinine"}
_BILI = {"bilirubin total", "bilirubin"}
_HB = {"hemoglobin", "haemoglobin", "hgb", "hb"}
_HCT = {"hematocrit", "hct"}
_ALB = {"albumin"}
_CA = {"calcium"}
_MG = {"magnesium"}
_VITD = {"vitamin d 25-oh", "vitamin d", "25-oh vitamin d", "25-hydroxy vitamin d"}
_B12 = {"vitamin b12", "b12"}
_FERR = {"ferritin"}
_FOLATE = {"folate", "folate serum", "serum folate", "folic acid"}
_IRON = {"iron", "tibc"}
_PSA = {"total psa", "psa"}
_TESTO = {"testosterone"}
_MCHC = {"mchc"}
_CELL_THOUSAND = {
    "wbc",
    "platelets",
    "neutrophils",
    "lymphocytes",
    "monocytes",
    "eosinophils",
    "basophils",
    "immature granulocytes",
}
_CELL_MILLION = {"rbc"}

# Same in both systems (identity).
_IDENTITY_NAMES = {
    "hba1c",
    "a1c",
    "tsh",
    "cholesterol/hdl ratio",
    "egfr",
    "alt",
    "ast",
    "alkaline phosphatase",
    "crp",
    "c reactive protein",
    "esr",
    "mcv",
    "mch",
    "rdw",
    "sodium",
    "potassium",
    "chloride",
    "systolic bp",
    "diastolic bp",
    "heart rate",
}


def normalize_unit(unit: str | None) -> str:
    raw = str(unit or "").strip()
    if not raw:
        return ""
    u = raw.replace("µ", "u").replace("μ", "u")
    u = re.sub(r"\s+", " ", u)
    u = u.replace("·", "/")
    lower = u.lower()
    aliases = {
        "mg/dl": "mg/dL",
        "mg / dl": "mg/dL",
        "g/dl": "g/dL",
        "g / dl": "g/dL",
        "mmol/l": "mmol/L",
        "mmol / l": "mmol/L",
        "umol/l": "umol/L",
        "u mol/l": "umol/L",
        "nmol/l": "nmol/L",
        "pmol/l": "pmol/L",
        "ug/l": "ug/L",
        "µg/l": "ug/L",
        "ng/ml": "ng/mL",
        "pg/ml": "pg/mL",
        "ug/dl": "ug/dL",
        "µg/dl": "ug/dL",
        "ng/dl": "ng/dL",
        "g/l": "g/L",
        "l/l": "L/L",
        "x e9/l": "x E9/L",
        "x10e9/l": "x E9/L",
        "10*9/l": "x E9/L",
        "10^9/l": "x E9/L",
        "x e12/l": "x E12/L",
        "x10e12/l": "x E12/L",
        "10*12/l": "x E12/L",
        "10^12/l": "x E12/L",
        "thousand/ul": "Thousand/uL",
        "thousands/ul": "Thousand/uL",
        "k/ul": "Thousand/uL",
        "10*3/ul": "Thousand/uL",
        "cells/ul": "cells/uL",
        "cell/ul": "cells/uL",
        "million/ul": "Million/uL",
        "millions/ul": "Million/uL",
        "m/ul": "Million/uL",
        "10*6/ul": "Million/uL",
        "ml/min/1.73m2": "mL/min/1.73m2",
        "ml/min/1.73m²": "mL/min/1.73m2",
        "mm/hr": "mm/hr",
        "mm/h": "mm/hr",
        "%": "%",
        "u/l": "U/L",
        "miu/l": "mIU/L",
        "iu/l": "IU/L",
        "au": "AU",
    }
    return aliases.get(lower, raw)


def _analyte_key(name: str | None) -> str:
    key = re.sub(r"\s+", " ", str(name or "").strip().lower())
    aliases = {
        "ldl": "ldl cholesterol",
        "ldl-c": "ldl cholesterol",
        "hdl": "hdl cholesterol",
        "hdl-c": "hdl cholesterol",
        "non hdl": "non-hdl cholesterol",
        "non-hdl": "non-hdl cholesterol",
        "non hdl cholesterol": "non-hdl cholesterol",
        "cholesterol": "total cholesterol",
        "triglycerides": "triglyceride",
        "trig": "triglyceride",
        "fasting glucose": "glucose fasting",
        "glucose (random)": "glucose random",
        "haemoglobin": "hemoglobin",
        "hgb": "hemoglobin",
        "hb": "hemoglobin",
        "hct": "hematocrit",
        "25-hydroxy vitamin d": "vitamin d 25-oh",
        "25 oh vitamin d": "vitamin d 25-oh",
        "vitamin d": "vitamin d 25-oh",
        "b12": "vitamin b12",
        "psa": "total psa",
        "c reactive protein": "crp",
        "platelet count": "platelets",
        "folate, serum": "folate",
        "serum folate": "folate",
        "folic acid": "folate",
        "thyroid stimulating hormone": "tsh",
        "thyroid-stimulating hormone": "tsh",
    }
    return aliases.get(key, key)


def canonical_lab_display_name(name: str | None) -> str:
    """Merge common report synonyms onto one chart/table label."""
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        return ""
    key = _analyte_key(cleaned)
    display = {
        "tsh": "TSH",
        "folate": "Folate",
        "ldl cholesterol": "LDL cholesterol",
        "hdl cholesterol": "HDL cholesterol",
        "non-hdl cholesterol": "Non-HDL cholesterol",
        "total cholesterol": "Total cholesterol",
        "triglyceride": "Triglyceride",
        "glucose fasting": "Glucose fasting",
        "vitamin d 25-oh": "Vitamin D 25-OH",
        "vitamin b12": "Vitamin B12",
        "total psa": "Total PSA",
        "hemoglobin": "Hemoglobin",
        "hematocrit": "Hematocrit",
        "platelets": "Platelets",
        "bilirubin total": "Bilirubin total",
    }
    return display.get(key, cleaned)


def _round_value(value: float, *, system: str, unit: str) -> float:
    u = normalize_unit(unit)
    if u in {"L/L"}:
        return round(value, 3)
    if u in {"mg/dL", "g/dL", "%", "ng/mL", "pg/mL", "ng/dL"} and abs(value) >= 10:
        return round(value, 1) if abs(value) < 100 else round(value, 0)
    if u in {"mmol/L", "umol/L", "µmol/L", "nmol/L", "pmol/L"}:
        if abs(value) >= 100:
            return round(value, 0)
        if abs(value) >= 10:
            return round(value, 1)
        return round(value, 2)
    if u in {"g/L", "ug/L", "µg/L"}:
        return round(value, 1) if abs(value) < 100 else round(value, 0)
    if system == "us" and u in {"Thousand/uL", "Million/uL"}:
        return round(value, 2)
    return round(value, 3) if abs(value) < 1 else round(value, 2)


def detect_unit_system(name: str | None, unit: str | None) -> UnitSystem:
    key = _analyte_key(name)
    if key in _BLOCKED_NAMES:
        return "unknown"
    if key in _IDENTITY_NAMES:
        return "same"
    u = normalize_unit(unit)
    if not u:
        return "unknown"
    us_markers = {
        "mg/dL",
        "g/dL",
        "ng/mL",
        "pg/mL",
        "ug/dL",
        "ng/dL",
        "Thousand/uL",
        "Million/uL",
        "cells/uL",
    }
    si_markers = {
        "mmol/L",
        "umol/L",
        "nmol/L",
        "pmol/L",
        "g/L",
        "ug/L",
        "L/L",
        "x E9/L",
        "x E12/L",
    }
    if key in _HCT:
        if u == "%":
            return "us"
        if u == "L/L":
            return "si"
    if u in us_markers:
        return "us"
    if u in si_markers:
        return "si"
    if u in {"%", "U/L", "mIU/L", "IU/L", "mm/hr", "AU", "mL/min/1.73m2"}:
        return "same"
    return "unknown"


def _to_si(key: str, value: float, unit: str) -> tuple[float, str] | None:
    u = normalize_unit(unit)
    si_unit = _SI_UNITS.get(key)
    if not si_unit:
        return None
    # Already SI
    if detect_unit_system(key, u) == "si" or u == normalize_unit(si_unit):
        return value, si_unit
    if key in _LIPID and u == "mg/dL":
        return value / 38.67, si_unit
    if key in _TRIG and u == "mg/dL":
        return value / 88.57, si_unit
    if key in _GLUCOSE and u == "mg/dL":
        return value / 18.018, si_unit
    if key in _CREAT and u == "mg/dL":
        return value * 88.4, si_unit
    if key in _BILI and u == "mg/dL":
        return value * 17.1, si_unit
    if key in _HB and u == "g/dL":
        return value * 10.0, si_unit
    if key in _HCT and u == "%":
        return value / 100.0, si_unit
    if key in _ALB and u == "g/dL":
        return value * 10.0, si_unit
    if key in _CA and u == "mg/dL":
        return value / 4.0, si_unit
    if key in _MG and u == "mg/dL":
        return value / 2.43, si_unit
    if key in _VITD and u == "ng/mL":
        return value * 2.496, si_unit
    if key in _B12 and u == "pg/mL":
        return value * 0.738, si_unit
    if key in _FERR and u in {"ng/mL", "ug/L"}:
        return value, si_unit
    if key in _FOLATE and u == "ng/mL":
        return value * 2.266, si_unit
    if key in _MCHC and u == "g/dL":
        return value * 10.0, si_unit
    if key in _IRON and u == "ug/dL":
        return value / 5.587, si_unit
    if key in _PSA and u == "ng/mL":
        return value, si_unit
    if key in _TESTO and u == "ng/dL":
        return value * 0.0347, si_unit
    if key in _CELL_THOUSAND and u == "Thousand/uL":
        return value, si_unit
    if key in _CELL_THOUSAND and u == "cells/uL":
        return value / 1000.0, si_unit
    if key in _CELL_MILLION and u == "Million/uL":
        return value, si_unit
    return None


def _to_us(key: str, value: float, unit: str) -> tuple[float, str] | None:
    u = normalize_unit(unit)
    us_unit = _US_UNITS.get(key)
    if not us_unit:
        return None
    if u == "cells/uL" and key in _CELL_THOUSAND:
        return value / 1000.0, us_unit
    if detect_unit_system(key, u) == "us" or u == normalize_unit(us_unit):
        return value, us_unit
    # Prefer converting from SI canonical
    if key in _LIPID and u == "mmol/L":
        return value * 38.67, us_unit
    if key in _TRIG and u == "mmol/L":
        return value * 88.57, us_unit
    if key in _GLUCOSE and u == "mmol/L":
        return value * 18.018, us_unit
    if key in _CREAT and u in {"umol/L", "µmol/L"}:
        return value / 88.4, us_unit
    if key in _BILI and u in {"umol/L", "µmol/L"}:
        return value / 17.1, us_unit
    if key in _HB and u == "g/L":
        return value / 10.0, us_unit
    if key in _HCT and u == "L/L":
        return value * 100.0, us_unit
    if key in _ALB and u == "g/L":
        return value / 10.0, us_unit
    if key in _CA and u == "mmol/L":
        return value * 4.0, us_unit
    if key in _MG and u == "mmol/L":
        return value * 2.43, us_unit
    if key in _VITD and u == "nmol/L":
        return value / 2.496, us_unit
    if key in _B12 and u == "pmol/L":
        return value / 0.738, us_unit
    if key in _FERR and u in {"ug/L", "µg/L"}:
        return value, us_unit
    if key in _FOLATE and u == "nmol/L":
        return value / 2.266, us_unit
    if key in _MCHC and u == "g/L":
        return value / 10.0, us_unit
    if key in _IRON and u in {"umol/L", "µmol/L"}:
        return value * 5.587, us_unit
    if key in _PSA and u in {"ug/L", "µg/L"}:
        return value, us_unit
    if key in _TESTO and u == "nmol/L":
        return value / 0.0347, us_unit
    if key in _CELL_THOUSAND and u == "x E9/L":
        return value, us_unit
    if key in _CELL_THOUSAND and u == "cells/uL":
        return value / 1000.0, us_unit
    if key in _CELL_MILLION and u == "x E12/L":
        return value, us_unit
    # If we only have US already handled; if SI via mg/dL path failed, try via SI first
    si = _to_si(key, value, u)
    if si:
        return _to_us(key, si[0], si[1])
    return None


def convert_value(
    name: str | None,
    value: float | int | None,
    from_unit: str | None,
    to_system: Literal["si", "us"],
) -> tuple[float, str] | None:
    """Convert a reading to SI or US. Returns (value, unit) or None if unsupported."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    key = _analyte_key(name)
    if key in _BLOCKED_NAMES:
        return None
    unit = normalize_unit(from_unit)
    if key in _IDENTITY_NAMES or detect_unit_system(key, unit) == "same":
        return num, unit or (from_unit or "")
    if to_system == "si":
        out = _to_si(key, num, unit)
    else:
        out = _to_us(key, num, unit)
        if out is None:
            # Original may already be US-incompatible unit string; go via SI
            si = _to_si(key, num, unit)
            if si:
                out = _to_us(key, si[0], si[1])
    if not out:
        return None
    v, u = out
    return _round_value(v, system=to_system, unit=u), u


def convert_reference_band(
    name: str | None,
    ref: dict[str, Any] | None,
    *,
    to_system: Literal["si", "us"],
) -> dict[str, Any] | None:
    """Return a copy of an SI-authored reference band converted for US display."""
    if not ref:
        return None
    if to_system == "si":
        return dict(ref)
    key = _analyte_key(name)
    if key in _IDENTITY_NAMES or key in _BLOCKED_NAMES:
        return dict(ref)
    si_unit = _SI_UNITS.get(key)
    us_unit = _US_UNITS.get(key)
    if not si_unit or not us_unit:
        return dict(ref)

    out = dict(ref)
    for field in ("low", "high"):
        raw = ref.get(field)
        if raw is None:
            continue
        converted = convert_value(name, raw, si_unit, "us")
        if converted:
            out[field] = converted[0]
    # Rewrite simple numeric labels like "Desirable <2.6"
    label = str(ref.get("label") or "")
    if label and si_unit:

        def _repl(m: re.Match[str]) -> str:
            try:
                n = float(m.group(1))
            except ValueError:
                return m.group(0)
            conv = convert_value(name, n, si_unit, "us")
            if not conv:
                return m.group(0)
            v = conv[0]
            text = str(int(v)) if float(v).is_integer() else f"{v:g}"
            return text

        out["label"] = re.sub(r"(\d+(?:\.\d+)?)", _repl, label, count=3)
        if us_unit and us_unit not in out["label"]:
            out["label"] = f"{out['label']} {us_unit}".strip()
    note = str(ref.get("note") or "")
    if note and "mmol" in note.lower() and us_unit:
        out["note"] = note + f" (shown in {us_unit})"
    return out


def enrich_diagnostic_units(row: dict[str, Any]) -> dict[str, Any]:
    """Fill value_si/unit_si and value_us/unit_us from the original reading."""
    out = dict(row)
    raw_name = out.get("name")
    canon = canonical_lab_display_name(raw_name)
    if canon:
        out["name"] = canon
    name = out.get("name")
    try:
        value = float(out.get("value"))
    except (TypeError, ValueError):
        return out
    unit = normalize_unit(out.get("unit")) or (str(out.get("unit") or "").strip() or None)
    if unit:
        out["unit"] = unit

    key = _analyte_key(name)
    system = detect_unit_system(name, unit)
    out["unit_system_original"] = system

    # Force recompute when new conversion rules apply (e.g. cells/uL, folate).
    needs_recompute = (
        "value_si" not in out
        or "value_us" not in out
        or (key in _FOLATE | _MCHC | _CELL_THOUSAND and unit in {"ng/mL", "g/dL", "cells/uL"})
    )
    if not needs_recompute and out.get("value_si") is not None and out.get("value_us") is not None:
        return out

    if key in _BLOCKED_NAMES or system == "unknown":
        # Absolute differentials in cells/uL are convertible for known WBC lines.
        if not (key in _CELL_THOUSAND and normalize_unit(unit) == "cells/uL"):
            out["value_si"] = value
            out["unit_si"] = unit
            out["value_us"] = value
            out["unit_us"] = unit
            return out
        system = "us"
        out["unit_system_original"] = "us"

    if system == "same" or key in _IDENTITY_NAMES:
        out["value_si"] = value
        out["unit_si"] = unit
        out["value_us"] = value
        out["unit_us"] = unit
        out["unit_system_original"] = "same"
        return out

    si = convert_value(name, value, unit, "si")
    us = convert_value(name, value, unit, "us")
    if si:
        out["value_si"], out["unit_si"] = si
    else:
        out["value_si"] = value
        out["unit_si"] = unit
    if us:
        out["value_us"], out["unit_us"] = us
    else:
        out["value_us"] = value
        out["unit_us"] = unit
    return out


def diagnostic_needs_unit_enrichment(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("value") is None:
        return False
    name = str(row.get("name") or "")
    unit = normalize_unit(row.get("unit"))
    key = _analyte_key(name)
    if canonical_lab_display_name(name) != name:
        return True
    if key in _FOLATE | _MCHC and unit in {"ng/mL", "g/dL"}:
        return True
    if key in _CELL_THOUSAND and unit == "cells/uL":
        return True
    return (
        "value_si" not in row
        or "value_us" not in row
        or "unit_si" not in row
        or "unit_us" not in row
        or "unit_system_original" not in row
    )


def enrich_diagnostics_list(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Enrich all diagnostic rows. Returns (rows, changed)."""
    changed = False
    out: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        if diagnostic_needs_unit_enrichment(raw):
            enriched = enrich_diagnostic_units(raw)
            changed = True
            out.append(enriched)
        else:
            out.append(raw)
    return out, changed


def format_dual_unit_text(
    row: dict[str, Any],
    *,
    prefer: Literal["si", "us", "original"] = "si",
) -> str:
    """Human-readable value with optional alternate system in parentheses."""
    original_v = row.get("value")
    original_u = row.get("unit") or ""
    si_v = row.get("value_si", original_v)
    si_u = row.get("unit_si") or original_u
    us_v = row.get("value_us", original_v)
    us_u = row.get("unit_us") or original_u

    if prefer == "us":
        primary = f"{us_v} {us_u}".strip()
    elif prefer == "original":
        primary = f"{original_v} {original_u}".strip()
    else:
        primary = f"{si_v} {si_u}".strip()

    reported = f"{original_v} {original_u}".strip()
    if prefer != "original" and reported and reported != primary:
        return f"{primary} ({reported} as reported)"
    return primary


def project_series_for_unit_system(
    series: list[dict[str, Any]],
    system: Literal["si", "us"] = "si",
) -> list[dict[str, Any]]:
    """Rewrite chart series so ``value``/``unit``/``reference`` match SI or US display."""
    out: list[dict[str, Any]] = []
    for raw in series or []:
        item = dict(raw)
        use_us = system == "us"
        unit = item.get("unit_us" if use_us else "unit_si") or item.get("unit")
        item["unit"] = unit
        item["display_unit_system"] = system
        ref = item.get("reference_us" if use_us else "reference_si") or item.get("reference")
        if ref:
            item["reference"] = ref
        readings = []
        for r in item.get("readings") or []:
            rr = dict(r)
            if use_us:
                rr["value"] = rr.get("value_us", rr.get("value"))
                rr["unit"] = rr.get("unit_us") or unit
            else:
                rr["value"] = rr.get("value_si", rr.get("value"))
                rr["unit"] = rr.get("unit_si") or unit
            readings.append(rr)
        item["readings"] = readings
        if readings:
            item["latest"] = readings[-1]
        elif item.get("latest"):
            latest = dict(item["latest"])
            if use_us:
                latest["value"] = latest.get("value_us", latest.get("value"))
                latest["unit"] = latest.get("unit_us") or unit
            else:
                latest["value"] = latest.get("value_si", latest.get("value"))
                latest["unit"] = latest.get("unit_si") or unit
            item["latest"] = latest
        out.append(item)
    return out
