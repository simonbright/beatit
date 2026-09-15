"""Lab panel (type) grouping for Labs filters and PDF export."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.diagnostic_references import CORE_MONITORING_METRICS

# Display order for the Labs type filter (keys match CORE_MONITORING_METRICS.group)
LAB_PANEL_ORDER: list[str] = [
    "Lipids",
    "Glucose",
    "Kidney",
    "Liver",
    "CBC",
    "Electrolytes",
    "Iron & vitamins",
    "Thyroid",
    "Inflammation",
    "Prostate",
    "Hormones",
    "Vitals",
    "Imaging",
    "Other",
]

# User-facing labels (Cholesterol is clearer than Lipids for patients)
LAB_PANEL_LABELS: dict[str, str] = {
    "Lipids": "Cholesterol",
    "Iron & vitamins": "Iron & vitamins",
}


def _norm_name(raw: Any) -> str:
    return " ".join(str(raw or "").lower().split())


def _build_name_to_panel() -> dict[str, str]:
    out: dict[str, str] = {}
    for metric in CORE_MONITORING_METRICS:
        group = str(metric.get("group") or "Other").strip() or "Other"
        name = _norm_name(metric.get("name"))
        if name:
            out[name] = group
        for alias in metric.get("aliases") or []:
            key = _norm_name(alias)
            if key:
                out[key] = group
    return out


_NAME_TO_PANEL = _build_name_to_panel()

_HEURISTICS: list[tuple[tuple[str, ...], str]] = [
    (("ldl", "hdl", "non-hdl", "triglyceride", "triglyc", "cholesterol"), "Lipids"),
    (("hba1c", "a1c", "glucose", "fasting sugar"), "Glucose"),
    (("creatinine", "egfr", "gfr", "bun", "urea"), "Kidney"),
    (("alt", "ast", "bilirubin", "alp", "alkaline", "albumin", "ggt"), "Liver"),
    (
        (
            "hemoglobin",
            "haemoglobin",
            "hematocrit",
            "haematocrit",
            "wbc",
            "rbc",
            "platelet",
            "mcv",
            "mch",
            "rdw",
            "neutrophil",
            "lymphocyte",
            "monocyte",
            "eosinophil",
            "basophil",
        ),
        "CBC",
    ),
    (("sodium", "potassium", "magnesium", "chloride", "bicarbonate", "co2"), "Electrolytes"),
    (
        ("ferritin", "iron", "tibc", "transferrin", "vitamin b12", "b12", "folate", "vitamin d"),
        "Iron & vitamins",
    ),
    (("tsh", "free t4", "free t3", "thyroid"), "Thyroid"),
    (("crp", "esr", "sed rate", "sedimentation"), "Inflammation"),
    (("psa",), "Prostate"),
    (("testosterone", "estradiol", "cortisol"), "Hormones"),
    (("systolic", "diastolic", "blood pressure", "bmi", "weight"), "Vitals"),
    (("calcium score", "agatston", "bi-rads", "birads"), "Imaging"),
]


def lab_panel_for_name(name: Any, *, category: str | None = None) -> str:
    """Return panel group for a diagnostic test name."""
    cat = str(category or "").strip().lower()
    if cat == "vital":
        return "Vitals"
    if cat == "imaging":
        return "Imaging"
    key = _norm_name(name)
    if not key:
        return "Other"
    if key in _NAME_TO_PANEL:
        return _NAME_TO_PANEL[key]
    for aliases, group in _HEURISTICS:
        if any(a in key for a in aliases):
            return group
    return "Other"


def lab_panel_label(panel: str | None) -> str:
    key = str(panel or "Other").strip() or "Other"
    return LAB_PANEL_LABELS.get(key, key)


def panels_present_in_series(series: list[dict[str, Any]] | None) -> list[str]:
    """Ordered unique panels that appear in the given series."""
    found: set[str] = set()
    for item in series or []:
        found.add(
            lab_panel_for_name(item.get("name"), category=item.get("category"))
        )
    ordered = [p for p in LAB_PANEL_ORDER if p in found]
    for p in sorted(found):
        if p not in ordered:
            ordered.append(p)
    return ordered


def resolve_labs_date_bounds(
    *,
    range_key: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    today: date | None = None,
) -> tuple[str | None, str | None]:
    """Resolve inclusive YYYY-MM-DD bounds for Labs date filter."""
    key = str(range_key or "all").strip().lower()
    if key in {"", "all", "everything"}:
        return None, None
    as_of = today or datetime.now(timezone.utc).astimezone().date()
    if key in {"6m", "6mo", "180d", "last_6_months"}:
        return (as_of - timedelta(days=183)).isoformat(), as_of.isoformat()
    if key in {"12m", "1y", "365d", "last_12_months", "last_year"}:
        return (as_of - timedelta(days=365)).isoformat(), as_of.isoformat()
    if key in {"24m", "2y", "730d", "last_24_months", "last_2_years"}:
        return (as_of - timedelta(days=730)).isoformat(), as_of.isoformat()
    # custom / explicit
    start = str(date_from or "").strip()[:10] or None
    end = str(date_to or "").strip()[:10] or None
    if start and len(start) != 10:
        start = None
    if end and len(end) != 10:
        end = None
    if start and end and start > end:
        start, end = end, start
    return start, end


def filter_series_by_date(
    series: list[dict[str, Any]] | None,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Keep readings within [date_from, date_to] inclusive; drop empty series."""
    if not series:
        return []
    if not date_from and not date_to:
        return list(series)
    out: list[dict[str, Any]] = []
    for item in series:
        readings: list[dict[str, Any]] = []
        for row in item.get("readings") or []:
            day = str(row.get("recorded_at") or "")[:10]
            if len(day) != 10:
                continue
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
            readings.append(row)
        if not readings:
            continue
        clone = dict(item)
        clone["readings"] = readings
        clone["point_count"] = len(readings)
        clone["latest"] = readings[-1]
        status = readings[-1].get("status")
        if isinstance(status, str):
            clone["status"] = status
        out.append(clone)
    return out


def filter_series_by_panel(
    series: list[dict[str, Any]] | None,
    panel: str | None,
) -> list[dict[str, Any]]:
    """Keep series whose lab panel matches ``panel`` (or all when panel is all/empty)."""
    key = str(panel or "all").strip()
    if not key or key.lower() in {"all", "*"}:
        return list(series or [])
    # Accept display label "Cholesterol" as Lipids
    if key.lower() in {"cholesterol", "cholesterol / lipids", "lipids / cholesterol"}:
        key = "Lipids"
    out: list[dict[str, Any]] = []
    for item in series or []:
        group = lab_panel_for_name(item.get("name"), category=item.get("category"))
        if group == key:
            out.append(item)
    return out


def filter_labs_series(
    series: list[dict[str, Any]] | None,
    *,
    range_key: str | None = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    panel: str | None = "all",
) -> list[dict[str, Any]]:
    """Apply Labs date range + panel type filters."""
    start, end = resolve_labs_date_bounds(
        range_key=range_key,
        date_from=date_from,
        date_to=date_to,
    )
    clipped = filter_series_by_date(series, date_from=start, date_to=end)
    return filter_series_by_panel(clipped, panel)
