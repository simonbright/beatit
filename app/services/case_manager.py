"""Patient / case registry.

Hierarchy
---------
data/
  registry.json          <- patients list + active patient/case
  patients/
    <patient-slug>/
      cases/
        <case-slug>/
          beatit.db
          documents/
          extracted/
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings

REGISTRY_FILENAME = "registry.json"
# (mtime_ns, size, profile) — skip lab enrichment on every poll / page-load.
_PROFILE_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(label: str) -> str:
    slug = label.strip().lower()
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in slug)
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or "default"


# ------------------------------------------------------------------
# Registry I/O
# ------------------------------------------------------------------

def _registry_path() -> Path:
    return settings.data_dir / REGISTRY_FILENAME


def _default_registry() -> dict[str, Any]:
    return {
        "active_patient": None,
        "active_case": None,
        "patients": [],
    }


def load_registry() -> dict[str, Any]:
    path = _registry_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return _default_registry()


def save_registry(reg: dict[str, Any]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, indent=2), encoding="utf-8")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

PHOTO_FILENAMES = ("photo.jpg", "photo.jpeg", "photo.png", "photo.webp")
PHOTO_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def _patients_dir() -> Path:
    return settings.data_dir / "patients"


def _patient_dir(patient_id: str) -> Path:
    return _patients_dir() / patient_id


def _case_dir(patient_id: str, case_id: str) -> Path:
    return _patients_dir() / patient_id / "cases" / case_id


def find_patient_photo(patient_id: str) -> Path | None:
    d = _patient_dir(patient_id)
    if not d.exists():
        return None
    for name in PHOTO_FILENAMES:
        path = d / name
        if path.exists():
            return path
    return None


def save_patient_photo(patient_id: str, data: bytes, content_type: str, filename: str = "") -> Path:
    ext = PHOTO_EXT_BY_TYPE.get((content_type or "").lower())
    if not ext:
        suffix = Path(filename or "").suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            ext = ".jpg"
        elif suffix in {".png", ".webp"}:
            ext = suffix
    if not ext:
        raise ValueError("Photo must be JPEG, PNG, or WebP")
    d = _patient_dir(patient_id)
    d.mkdir(parents=True, exist_ok=True)
    for name in PHOTO_FILENAMES:
        old = d / name
        if old.exists():
            old.unlink()
    path = d / f"photo{ext}"
    path.write_bytes(data)
    return path


def _photo_url(patient_id: str) -> str | None:
    path = find_patient_photo(patient_id)
    if not path:
        return None
    mtime = int(path.stat().st_mtime)
    return f"/api/patients/{patient_id}/photo?t={mtime}"


def _serialize_patient(patient: dict) -> dict[str, Any]:
    pid = patient["id"]
    profile = get_patient_profile(pid)
    public = {key: value for key, value in patient.items() if key != "owner"}
    return {
        **public,
        "has_photo": find_patient_photo(pid) is not None,
        "photo_url": _photo_url(pid),
        "date_of_birth": profile.get("date_of_birth"),
        "gender": profile.get("gender"),
        "latest_measurement": latest_measurement(profile),
    }


def _profile_path(patient_id: str) -> Path:
    return _patient_dir(patient_id) / "profile.json"


def _default_food_drinks() -> list[dict[str, Any]]:
    from uuid import uuid4

    now = _now_iso()
    return [
        {"id": str(uuid4()), "label": label, "created_at": now}
        for label in DEFAULT_FOOD_DRINKS
    ]


CARE_ITEM_KINDS = frozenset(
    {"exam", "diagnostic", "appointment", "call", "send", "follow_up", "other"}
)
CARE_ITEM_STATUSES = frozenset({"open", "done", "cancelled"})


def _empty_profile() -> dict[str, Any]:
    return {
        "date_of_birth": None,
        "gender": None,
        "measurements": [],
        "diagnostics": [],
        "journal": [],
        "medications": [],
        "food_drinks": _default_food_drinks(),
        "care_items": [],
        "doctor_questions": [],
        "care_events": [],
        "care_team": [],
        "family_team": [],
        "family_role_history": [],
        "log_custom_tiles": [],
        "log_tile_order": [],
        "log_tile_hidden": [],
        "milestones": [],
        "medication_safety": None,
    }


def _profile_cache_get(patient_id: str, path: Path) -> dict[str, Any] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    cached = _PROFILE_CACHE.get(patient_id)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return copy.deepcopy(cached[2])
    return None


def _profile_cache_put(patient_id: str, path: Path, profile: dict[str, Any]) -> None:
    try:
        st = path.stat()
    except OSError:
        return
    _PROFILE_CACHE[patient_id] = (st.st_mtime_ns, st.st_size, profile)


def _profile_cache_clear(patient_id: str | None = None) -> None:
    if patient_id is None:
        _PROFILE_CACHE.clear()
    else:
        _PROFILE_CACHE.pop(patient_id, None)


def get_patient_profile(patient_id: str) -> dict[str, Any]:
    path = _profile_path(patient_id)
    if not path.exists():
        _profile_cache_clear(patient_id)
        return _empty_profile()
    hit = _profile_cache_get(patient_id, path)
    if hit is not None:
        return hit
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_profile()
    profile = _empty_profile()
    profile["date_of_birth"] = data.get("date_of_birth") or None
    profile["gender"] = data.get("gender") or None
    measurements = data.get("measurements") or []
    if isinstance(measurements, list):
        profile["measurements"] = sorted(
            measurements,
            key=lambda m: str(m.get("recorded_at") or ""),
            reverse=True,
        )
    diagnostics = data.get("diagnostics") or []
    units_changed = False
    dates_changed = False
    if isinstance(diagnostics, list):
        from app.services.lab_units import enrich_diagnostics_list
        from app.services.lab_patient_identity import prefer_plausible_lab_iso

        cleaned = [d for d in diagnostics if isinstance(d, dict)]
        fixed_dates: list[dict[str, Any]] = []
        for row in cleaned:
            item = dict(row)
            raw_date = str(item.get("recorded_at") or "").strip()[:10]
            if raw_date:
                corrected = prefer_plausible_lab_iso(raw_date)
                if corrected and corrected != raw_date:
                    item["recorded_at"] = corrected
                    dates_changed = True
            fixed_dates.append(item)
        enriched, units_changed = enrich_diagnostics_list(fixed_dates)
        profile["diagnostics"] = sorted(
            enriched,
            key=lambda d: str(d.get("recorded_at") or ""),
            reverse=True,
        )
    journal = data.get("journal") or []
    if isinstance(journal, list):
        profile["journal"] = sorted(
            journal,
            key=lambda j: str(j.get("recorded_at") or j.get("created_at") or ""),
            reverse=True,
        )
    medications = data.get("medications") or []
    if isinstance(medications, list):
        from app.services.medication_identity import annotate_medications

        normalized_meds = []
        for raw in medications:
            if not isinstance(raw, dict):
                continue
            med = dict(raw)
            med["category"] = _normalize_medication_category(med.get("category"))
            if "show_on_log" not in med:
                # Legacy rows without the flag are off Log until explicitly enabled
                med["show_on_log"] = False
            else:
                med["show_on_log"] = _coerce_show_on_log(med.get("show_on_log"))
            official = " ".join(str(med.get("official_name") or "").split())
            med["official_name"] = official[:120] if official else None
            preferred = " ".join(str(med.get("name") or "").split())
            if preferred:
                med["name"] = preferred[:120]
            normalized_meds.append(med)
        profile["medications"] = annotate_medications(
            _sort_medications(_dedupe_medications(normalized_meds))
        )
    if "food_drinks" not in data:
        profile["food_drinks"] = _default_food_drinks()
    else:
        food_drinks = data.get("food_drinks")
        cleaned_foods: list[dict[str, Any]] = []
        seen: set[str] = set()
        if isinstance(food_drinks, list):
            from uuid import uuid4

            for raw in food_drinks:
                if isinstance(raw, str):
                    label = _normalize_food_drink_label(raw)
                    item: dict[str, Any] = {"id": str(uuid4()), "label": label}
                elif isinstance(raw, dict):
                    label = _normalize_food_drink_label(
                        str(raw.get("label") or raw.get("name") or "")
                    )
                    item = {
                        "id": raw.get("id") or str(uuid4()),
                        "label": label,
                        "created_at": raw.get("created_at"),
                    }
                else:
                    continue
                if not label:
                    continue
                key = label.lower()
                if key in seen:
                    continue
                seen.add(key)
                cleaned_foods.append(item)
        profile["food_drinks"] = cleaned_foods
    profile["log_custom_tiles"] = _normalize_log_custom_tiles(data.get("log_custom_tiles"))
    order_raw = data.get("log_tile_order")
    if isinstance(order_raw, list):
        profile["log_tile_order"] = [
            str(k).strip() for k in order_raw if isinstance(k, str) and str(k).strip()
        ]
    else:
        profile["log_tile_order"] = []
    hidden_raw = data.get("log_tile_hidden")
    if isinstance(hidden_raw, list):
        profile["log_tile_hidden"] = [
            str(k).strip() for k in hidden_raw if isinstance(k, str) and str(k).strip()
        ][:80]
    else:
        profile["log_tile_hidden"] = []
    milestones = data.get("milestones") or []
    if isinstance(milestones, list):
        profile["milestones"] = sorted(
            [m for m in milestones if isinstance(m, dict)],
            key=lambda m: str(m.get("date") or ""),
            reverse=True,
        )
    profile["care_items"] = _normalize_care_items(data.get("care_items"))
    profile["doctor_questions"] = _normalize_doctor_questions(data.get("doctor_questions"))
    profile["care_events"] = _normalize_care_events(data.get("care_events"))
    profile["care_team"] = _normalize_team(data.get("care_team"))
    profile["family_team"] = _normalize_family(data.get("family_team"))
    profile["family_role_history"] = _normalize_role_history(data.get("family_role_history"))
    safety = data.get("medication_safety")
    if isinstance(safety, dict):
        profile["medication_safety"] = safety
    tiles_cleaned = False
    for tile in list(profile.get("log_custom_tiles") or []):
        if not isinstance(tile, dict):
            continue
        label = str(tile.get("label") or "")
        if custom_log_tile_is_medication_duplicate(label, profile):
            if _remove_custom_log_tiles_matching_label(profile, label):
                tiles_cleaned = True
    if units_changed or dates_changed or tiles_cleaned:
        try:
            # Write without reloading — reload would re-enter this path.
            save_patient_profile(patient_id, profile, reload=False)
        except OSError:
            pass
    _profile_cache_put(patient_id, path, profile)
    return copy.deepcopy(profile)


def get_patient_log_bootstrap(patient_id: str) -> dict[str, Any]:
    """Fast path for Home Log tiles: skip diagnostics enrichment and chart series.

    Returns a slim profile with tile config, recent journal entries, and enough
    medication fields to hide duplicate med tiles. Intended for first paint.
    """
    path = _profile_path(patient_id)
    profile = {
        "date_of_birth": None,
        "gender": None,
        "measurements": [],
        "diagnostics": [],
        "journal": [],
        "medications": [],
        "food_drinks": [],
        "care_items": [],
        "doctor_questions": [],
        "care_events": [],
        "care_team": [],
        "family_team": [],
        "family_role_history": [],
        "log_custom_tiles": [],
        "log_tile_order": [],
        "log_tile_hidden": [],
        "milestones": [],
        "medication_safety": None,
    }
    if not path.exists():
        return profile
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return profile

    profile["date_of_birth"] = data.get("date_of_birth") or None
    profile["gender"] = data.get("gender") or None
    profile["log_custom_tiles"] = _normalize_log_custom_tiles(data.get("log_custom_tiles"))
    order_raw = data.get("log_tile_order")
    if isinstance(order_raw, list):
        profile["log_tile_order"] = [
            str(k).strip() for k in order_raw if isinstance(k, str) and str(k).strip()
        ]
    hidden_raw = data.get("log_tile_hidden")
    if isinstance(hidden_raw, list):
        profile["log_tile_hidden"] = [
            str(k).strip() for k in hidden_raw if isinstance(k, str) and str(k).strip()
        ][:80]

    meds_out: list[dict[str, Any]] = []
    for raw in data.get("medications") or []:
        if not isinstance(raw, dict):
            continue
        meds_out.append(
            {
                "id": raw.get("id"),
                "name": raw.get("name"),
                "official_name": raw.get("official_name"),
                "status": raw.get("status") or "active",
                "ended_at": raw.get("ended_at"),
                "category": _normalize_medication_category(raw.get("category")),
                "show_on_log": (
                    _coerce_show_on_log(raw.get("show_on_log"))
                    if "show_on_log" in raw
                    else False
                ),
            }
        )
    profile["medications"] = meds_out

    journal = data.get("journal") or []
    if isinstance(journal, list):
        cleaned = [j for j in journal if isinstance(j, dict)]
        cleaned.sort(
            key=lambda j: str(j.get("recorded_at") or j.get("created_at") or ""),
            reverse=True,
        )
        # Enough for Home Log recent list without shipping the entire history.
        profile["journal"] = cleaned[:250]

    return profile


def get_patient_journal_entries(patient_id: str) -> list[dict[str, Any]]:
    """Journal only — skip diagnostics enrichment so Log observations stay cheap."""
    path = _profile_path(patient_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    journal = data.get("journal") or []
    if not isinstance(journal, list):
        return []
    return [j for j in journal if isinstance(j, dict)]


def save_patient_profile(
    patient_id: str,
    profile: dict[str, Any],
    *,
    reload: bool = True,
) -> dict[str, Any]:
    d = _patient_dir(patient_id)
    d.mkdir(parents=True, exist_ok=True)
    meds_out: list[dict[str, Any]] = []
    for raw in profile.get("medications") or []:
        if not isinstance(raw, dict):
            continue
        med = dict(raw)
        med["category"] = _normalize_medication_category(med.get("category"))
        med["show_on_log"] = _coerce_show_on_log(med.get("show_on_log"))
        # Persist identity snapshot when present
        for key in ("identity_status", "identity_match", "identity_score"):
            if key in med and med[key] is None:
                med.pop(key, None)
        meds_out.append(med)
    meds_out = _dedupe_medications(meds_out)
    cleaned = {
        "date_of_birth": profile.get("date_of_birth") or None,
        "gender": profile.get("gender") or None,
        "measurements": profile.get("measurements") or [],
        "diagnostics": profile.get("diagnostics") or [],
        "journal": profile.get("journal") or [],
        "medications": meds_out,
        "food_drinks": [
            {
                "id": str(f.get("id") or ""),
                "label": _normalize_food_drink_label(str(f.get("label") or "")),
                "created_at": f.get("created_at"),
            }
            for f in (profile.get("food_drinks") or [])
            if isinstance(f, dict) and _normalize_food_drink_label(str(f.get("label") or ""))
        ],
        "log_custom_tiles": _normalize_log_custom_tiles(profile.get("log_custom_tiles")),
        "log_tile_order": [
            str(k).strip()
            for k in (profile.get("log_tile_order") or [])
            if isinstance(k, str) and str(k).strip()
        ],
        "log_tile_hidden": [
            str(k).strip()
            for k in (profile.get("log_tile_hidden") or [])
            if isinstance(k, str) and str(k).strip()
        ][:80],
        "milestones": [
            m for m in (profile.get("milestones") or []) if isinstance(m, dict)
        ],
        "care_items": _normalize_care_items(profile.get("care_items")),
        "doctor_questions": _normalize_doctor_questions(profile.get("doctor_questions")),
        "care_events": _normalize_care_events(profile.get("care_events")),
        "care_team": _normalize_team(profile.get("care_team")),
        "family_team": _normalize_family(profile.get("family_team")),
        "family_role_history": _normalize_role_history(profile.get("family_role_history")),
        "medication_safety": profile.get("medication_safety") or None,
    }
    _profile_path(patient_id).write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    _profile_cache_clear(patient_id)
    if not reload:
        return cleaned
    return get_patient_profile(patient_id)


def scrub_all_patient_profiles() -> dict[str, Any]:
    """Normalize show_on_log opt-in and dedupe medications for every patient."""
    reg = load_registry()
    fixed = 0
    for patient in reg.get("patients") or []:
        pid = patient.get("id")
        if not pid:
            continue
        path = _profile_path(pid)
        if not path.exists():
            continue
        before = json.loads(path.read_text(encoding="utf-8"))
        before_meds = before.get("medications") or []
        profile = get_patient_profile(pid)
        after_meds = profile.get("medications") or []
        # Persist cleaned profile when med list changed or flags were missing
        needs_write = len(before_meds) != len(after_meds)
        if not needs_write:
            for raw in before_meds:
                if isinstance(raw, dict) and "show_on_log" not in raw:
                    needs_write = True
                    break
        if needs_write:
            save_patient_profile(pid, profile)
            fixed += 1
    return {"patients_scrubbed": fixed}


def update_patient_demographics(
    patient_id: str,
    *,
    date_of_birth: str | None = None,
    gender: str | None = None,
) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    if date_of_birth is not None:
        profile["date_of_birth"] = date_of_birth.strip() or None
    if gender is not None:
        profile["gender"] = gender.strip() or None
    return save_patient_profile(patient_id, profile)


def add_patient_measurement(
    patient_id: str,
    *,
    recorded_at: str,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    notes: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    if height_cm is None and weight_kg is None:
        raise ValueError("Provide height and/or weight")
    profile = get_patient_profile(patient_id)
    entry = {
        "id": str(uuid4()),
        "recorded_at": recorded_at.strip(),
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "notes": (notes or "").strip() or None,
        "created_at": _now_iso(),
    }
    profile.setdefault("measurements", []).append(entry)
    saved = save_patient_profile(patient_id, profile)
    return next((m for m in saved["measurements"] if m["id"] == entry["id"]), entry)


def delete_patient_measurement(patient_id: str, measurement_id: str) -> bool:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return False
    profile = get_patient_profile(patient_id)
    before = len(profile.get("measurements") or [])
    profile["measurements"] = [
        m for m in profile.get("measurements") or [] if m.get("id") != measurement_id
    ]
    if len(profile["measurements"]) == before:
        return False
    save_patient_profile(patient_id, profile)
    return True


DIAGNOSTIC_PRESETS = [
    # Blood / labs (primary for charting)
    {"name": "LDL cholesterol", "unit": "mmol/L", "category": "blood"},
    {"name": "Non-HDL cholesterol", "unit": "mmol/L", "category": "blood"},
    {"name": "Total cholesterol", "unit": "mmol/L", "category": "blood"},
    {"name": "HDL cholesterol", "unit": "mmol/L", "category": "blood"},
    {"name": "Triglyceride", "unit": "mmol/L", "category": "blood"},
    {"name": "Cholesterol/HDL ratio", "unit": "", "category": "blood"},
    {"name": "HbA1c", "unit": "%", "category": "blood"},
    {"name": "Glucose fasting", "unit": "mmol/L", "category": "blood"},
    {"name": "Glucose random", "unit": "mmol/L", "category": "blood"},
    {"name": "Creatinine", "unit": "µmol/L", "category": "blood"},
    {"name": "eGFR", "unit": "mL/min/1.73m²", "category": "blood"},
    {"name": "ALT", "unit": "U/L", "category": "blood"},
    {"name": "AST", "unit": "U/L", "category": "blood"},
    {"name": "Bilirubin total", "unit": "µmol/L", "category": "blood"},
    {"name": "Bilirubin direct", "unit": "µmol/L", "category": "blood"},
    {"name": "Bilirubin indirect", "unit": "µmol/L", "category": "blood"},
    {"name": "Alkaline Phosphatase", "unit": "U/L", "category": "blood"},
    {"name": "Albumin", "unit": "g/L", "category": "blood"},
    {"name": "Total protein", "unit": "g/L", "category": "blood"},
    {"name": "Hemoglobin", "unit": "g/L", "category": "blood"},
    {"name": "Hematocrit", "unit": "L/L", "category": "blood"},
    {"name": "WBC", "unit": "x E9/L", "category": "blood"},
    {"name": "RBC", "unit": "x E12/L", "category": "blood"},
    {"name": "Platelets", "unit": "x E9/L", "category": "blood"},
    {"name": "MCV", "unit": "fL", "category": "blood"},
    {"name": "Neutrophils", "unit": "x E9/L", "category": "blood"},
    {"name": "Lymphocytes", "unit": "x E9/L", "category": "blood"},
    {"name": "Sodium", "unit": "mmol/L", "category": "blood"},
    {"name": "Potassium", "unit": "mmol/L", "category": "blood"},
    {"name": "Magnesium", "unit": "mmol/L", "category": "blood"},
    {"name": "CRP", "unit": "mg/L", "category": "blood"},
    {"name": "TSH", "unit": "mIU/L", "category": "blood"},
    {"name": "Vitamin D 25-OH", "unit": "nmol/L", "category": "blood"},
    {"name": "Vitamin B12", "unit": "pmol/L", "category": "blood"},
    {"name": "Folate", "unit": "nmol/L", "category": "blood"},
    {"name": "Ferritin", "unit": "µg/L", "category": "blood"},
    {"name": "Iron", "unit": "µmol/L", "category": "blood"},
    {"name": "TIBC", "unit": "µmol/L", "category": "blood"},
    {"name": "Transferrin Saturation", "unit": "", "category": "blood"},
    {"name": "Total PSA", "unit": "µg/L", "category": "blood"},
    {"name": "Testosterone", "unit": "nmol/L", "category": "blood"},
    {"name": "CA19-9", "unit": "U/mL", "category": "blood"},
    {"name": "CEA", "unit": "ng/mL", "category": "blood"},
    # Imaging / other
    {"name": "Coronary calcium score", "unit": "AU", "category": "imaging"},
    {"name": "Systolic BP", "unit": "mmHg", "category": "vital"},
    {"name": "Diastolic BP", "unit": "mmHg", "category": "vital"},
]


def _infer_diagnostic_category(name: str, explicit: str | None = None) -> str:
    if explicit in {"blood", "imaging", "vital", "other"}:
        return explicit
    lower = (name or "").lower()
    for preset in DIAGNOSTIC_PRESETS:
        if preset["name"].lower() == lower:
            return preset.get("category") or "blood"
    imaging_bits = (
        "calcium score",
        "agatston",
        "ultrasound",
        "sonograph",
        "sonogram",
        "mammogram",
        "mammography",
        "mri",
        "magnetic resonance",
        "ct scan",
        "ct ",
        "computed tomography",
        "x-ray",
        "xray",
        "radiograph",
        "pet ",
        "nuclear medicine",
        "bone density",
        "dexa",
        "dxa",
        "echo",
        "echocardiogram",
        "holter",
        "stress test",
        "biopsy",
        "pathology",
        "histology",
        "cytology",
    )
    if any(bit in lower for bit in imaging_bits):
        return "imaging"
    if "bp" in lower or "blood pressure" in lower:
        return "vital"
    return "blood"


def add_patient_diagnostic(
    patient_id: str,
    *,
    name: str,
    value: float | None = None,
    recorded_at: str,
    unit: str | None = None,
    notes: str | None = None,
    result: str | None = None,
    category: str | None = None,
    source_document_id: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise ValueError("Diagnostic name is required")
    result_clean = " ".join((result or "").strip().split())[:2000] or None
    notes_clean = (notes or "").strip() or None
    numeric: float | None = None
    if value is not None and value != "":
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Diagnostic value must be a number") from exc
    if numeric is None and not result_clean and not notes_clean:
        raise ValueError("Diagnostic value or result text is required")
    date_raw = (recorded_at or "").strip()
    try:
        date_iso = datetime.fromisoformat(date_raw[:10]).date().isoformat()
    except ValueError as exc:
        raise ValueError("recorded_at must be YYYY-MM-DD") from exc
    profile = get_patient_profile(patient_id)
    unit_clean = (unit or "").strip() or None
    if numeric is not None and not unit_clean:
        for existing in profile.get("diagnostics") or []:
            if str(existing.get("name") or "").strip().lower() == cleaned_name.lower():
                if existing.get("unit"):
                    unit_clean = existing["unit"]
                    break
        if not unit_clean:
            for preset in DIAGNOSTIC_PRESETS:
                if preset["name"].lower() == cleaned_name.lower():
                    unit_clean = preset["unit"] or None
                    break
    cat = _infer_diagnostic_category(cleaned_name, category)
    doc_id = (source_document_id or "").strip() or None
    entry = {
        "id": str(uuid4()),
        "name": cleaned_name,
        "value": numeric,
        "unit": unit_clean,
        "recorded_at": date_iso,
        "category": cat,
        "notes": notes_clean,
        "result": result_clean,
        "source": "library" if doc_id else "manual",
        "created_at": _now_iso(),
    }
    if doc_id:
        entry["source_document_id"] = doc_id
    from app.services.lab_units import enrich_diagnostic_units

    entry = enrich_diagnostic_units(entry)
    profile.setdefault("diagnostics", []).append(entry)
    saved = save_patient_profile(patient_id, profile)
    return next((d for d in saved["diagnostics"] if d["id"] == entry["id"]), entry)


def delete_patient_diagnostic(patient_id: str, diagnostic_id: str) -> bool:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return False
    profile = get_patient_profile(patient_id)
    before = len(profile.get("diagnostics") or [])
    profile["diagnostics"] = [
        d for d in profile.get("diagnostics") or [] if d.get("id") != diagnostic_id
    ]
    if len(profile["diagnostics"]) == before:
        return False
    save_patient_profile(patient_id, profile)
    return True


def clear_patient_diagnostics(patient_id: str) -> dict[str, Any] | None:
    """Remove every lab/diagnostic reading for a patient. Returns updated profile."""
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    removed = len(profile.get("diagnostics") or [])
    profile["diagnostics"] = []
    saved = save_patient_profile(patient_id, profile)
    saved["_cleared_diagnostics_count"] = removed
    return saved


JOURNAL_KINDS = frozenset({"symptom", "feeling", "medication", "note"})

MEDICATION_CATEGORIES = frozenset({"prescription", "otc", "remedy"})

# Common non-Rx remedies offered as one-tap adds under Medications & remedies.
COMMON_REMEDIES = [
    {"name": "CBD 1 drop", "category": "remedy", "dosage": "1 drop", "frequency": "as needed"},
    {"name": "CBD 2 drops", "category": "remedy", "dosage": "2 drops", "frequency": "as needed"},
    {"name": "Magnesium", "category": "remedy", "dosage": None, "frequency": None},
    {"name": "Melatonin", "category": "remedy", "dosage": None, "frequency": None},
    {"name": "MoM (Milk of Magnesia)", "category": "otc", "dosage": None, "frequency": "as needed"},
    {"name": "Ibuprofen", "category": "otc", "dosage": None, "frequency": "as needed"},
    {"name": "Acetaminophen", "category": "otc", "dosage": None, "frequency": "as needed"},
    {"name": "Vitamin D", "category": "remedy", "dosage": None, "frequency": None},
]

DEFAULT_FOOD_DRINKS = ["Water"]


def _normalize_food_drink_label(label: str) -> str:
    return " ".join((label or "").strip().split())[:80]


def _normalize_medication_category(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    if value in {"rx", "prescription", "prescribed"}:
        return "prescription"
    if value in {"otc", "over-the-counter", "over the counter"}:
        return "otc"
    if value in {"remedy", "supplement", "natural", "herbal", "wellness"}:
        return "remedy"
    return "prescription"


def _is_as_needed_frequency(frequency: str | None) -> bool:
    f = (frequency or "").strip().lower()
    if not f:
        return False
    markers = (
        "as needed",
        "as required",
        "when needed",
        "if needed",
        "prn",
        "p.r.n",
        "on demand",
        "when required",
    )
    return any(m in f for m in markers)


def _is_daily_scheduled_frequency(frequency: str | None) -> bool:
    """True for blank or clearly daily/scheduled dosing (not PRN)."""
    f = (frequency or "").strip().lower()
    if not f:
        return True
    if _is_as_needed_frequency(f):
        return False
    markers = (
        "daily",
        "every day",
        "each day",
        "once a day",
        "twice a day",
        "three times a day",
        "four times a day",
        "times a day",
        "times daily",
        "per day",
        "/day",
        "qd",
        "q.d",
        "qday",
        "bid",
        "b.i.d",
        "tid",
        "t.i.d",
        "qid",
        "q.i.d",
        "qam",
        "qpm",
        "qhs",
        "bedtime",
        "nightly",
        "every morning",
        "every night",
        "every evening",
        "morning and night",
        "mane",
        "nocte",
    )
    return any(m in f for m in markers)


def default_show_on_log(category: str | None, frequency: str | None) -> bool:
    """Opt-in only: Log chips require an explicit Show on Log choice."""
    return False


def _coerce_show_on_log(raw: Any, *, category: str | None = None, frequency: str | None = None) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return False


def _medication_name_key(name: Any) -> str:
    return " ".join(str(name or "").strip().lower().split())


def _dedupe_medications(meds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate active meds with the same name (keep newest). Stopped rows kept once each name."""
    active_best: dict[str, dict[str, Any]] = {}
    stopped_best: dict[str, dict[str, Any]] = {}
    for med in meds:
        if not isinstance(med, dict):
            continue
        key = _medication_name_key(med.get("name"))
        if not key:
            continue
        status = str(med.get("status") or "active").lower()
        stamp = str(med.get("updated_at") or med.get("created_at") or "")
        bucket = stopped_best if status == "stopped" else active_best
        prev = bucket.get(key)
        if not prev:
            bucket[key] = med
            continue
        prev_stamp = str(prev.get("updated_at") or prev.get("created_at") or "")
        if stamp >= prev_stamp:
            # Prefer explicit show_on_log True if either has it
            if prev.get("show_on_log") and not med.get("show_on_log"):
                med = dict(med)
                med["show_on_log"] = True
            bucket[key] = med
        elif med.get("show_on_log") and not prev.get("show_on_log"):
            kept = dict(prev)
            kept["show_on_log"] = True
            bucket[key] = kept
    return list(active_best.values()) + list(stopped_best.values())

JOURNAL_PRESETS = [
    # Positive first — so a day can read headache → med → better
    {"label": "Feel Fine", "kind": "feeling"},
    {"label": "Feeling good", "kind": "feeling"},
    {"label": "Better", "kind": "feeling"},
    {"label": "OK / normal", "kind": "feeling"},
    {"label": "Energetic", "kind": "feeling"},
    {"label": "Pain-free", "kind": "feeling"},
    {"label": "Feel Weak", "kind": "feeling"},
    {"label": "Weak", "kind": "feeling"},
    {"label": "Feel Nauseous", "kind": "symptom"},
    {"label": "Vomiting", "kind": "symptom"},
    {"label": "Back Pain", "kind": "symptom"},
    {"label": "Leg Pain", "kind": "symptom"},
    {"label": "Esophageal Spasm", "kind": "symptom"},
    {"label": "Headache", "kind": "symptom"},
    {"label": "Nauseous", "kind": "symptom"},
    {"label": "Dizzy", "kind": "symptom"},
    {"label": "Fatigue", "kind": "symptom"},
    {"label": "Pain", "kind": "symptom"},
    {"label": "Anxiety", "kind": "feeling"},
    {"label": "Took medication", "kind": "medication"},
    {"label": "Ate/Drank", "kind": "note"},
    {"label": "Walked", "kind": "note"},
    {"label": "Weights", "kind": "note"},
    {"label": "Bathroom #2", "kind": "note"},
    {"label": "Took shower", "kind": "note"},
    {"label": "Slept", "kind": "note"},
    {"label": "Note", "kind": "note"},
]


def _normalize_journal_label(label: str) -> str:
    cleaned = " ".join((label or "").strip().split())
    return cleaned[:80]


def _parse_journal_datetime(raw: str | None) -> str:
    """Return timezone-aware ISO datetime; default to now (UTC)."""
    text = (raw or "").strip()
    if not text:
        return _now_iso()
    try:
        # Support date-only, local datetime without tz, and full ISO
        if len(text) == 10:
            dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError as exc:
        raise ValueError("recorded_at must be an ISO date or datetime") from exc


def add_patient_journal_entry(
    patient_id: str,
    *,
    kind: str,
    label: str,
    text: str | None = None,
    severity: int | None = None,
    recorded_at: str | None = None,
    case_id: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    kind_clean = (kind or "").strip().lower()
    if kind_clean not in JOURNAL_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(JOURNAL_KINDS))}")
    label_clean = _normalize_journal_label(label)
    if not label_clean:
        raise ValueError("label is required")
    severity_val: int | None = None
    if severity is not None:
        try:
            severity_val = int(severity)
        except (TypeError, ValueError) as exc:
            raise ValueError("severity must be an integer 1–5") from exc
        if severity_val < 1 or severity_val > 5:
            raise ValueError("severity must be 1–5")
    # Self-reports are person-level (not case-scoped). Ignore any case_id.
    entry = {
        "id": str(uuid4()),
        "recorded_at": _parse_journal_datetime(recorded_at),
        "kind": kind_clean,
        "label": label_clean,
        "text": (text or "").strip() or None,
        "severity": severity_val,
        "case_id": None,
        "created_at": _now_iso(),
    }
    profile = get_patient_profile(patient_id)
    profile.setdefault("journal", []).append(entry)
    saved = save_patient_profile(patient_id, profile)
    return next((j for j in saved["journal"] if j["id"] == entry["id"]), entry)


def delete_patient_journal_entry(patient_id: str, entry_id: str) -> bool:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return False
    profile = get_patient_profile(patient_id)
    before = len(profile.get("journal") or [])
    profile["journal"] = [
        j for j in profile.get("journal") or [] if j.get("id") != entry_id
    ]
    if len(profile["journal"]) == before:
        return False
    save_patient_profile(patient_id, profile)
    return True


def update_patient_journal_entry(
    patient_id: str,
    entry_id: str,
    *,
    kind: str | None = None,
    label: str | None = None,
    text: str | None = None,
    severity: int | None = None,
    clear_severity: bool = False,
    recorded_at: str | None = None,
) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    journal = list(profile.get("journal") or [])
    idx = next((i for i, j in enumerate(journal) if j.get("id") == entry_id), None)
    if idx is None:
        return None
    entry = dict(journal[idx])
    if kind is not None:
        kind_clean = (kind or "").strip().lower()
        if kind_clean not in JOURNAL_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(sorted(JOURNAL_KINDS))}")
        entry["kind"] = kind_clean
    if label is not None:
        label_clean = _normalize_journal_label(label)
        if not label_clean:
            raise ValueError("label is required")
        entry["label"] = label_clean
    if text is not None:
        entry["text"] = (text or "").strip() or None
    if clear_severity:
        entry["severity"] = None
    elif severity is not None:
        try:
            severity_val = int(severity)
        except (TypeError, ValueError) as exc:
            raise ValueError("severity must be an integer 1–5") from exc
        if severity_val < 1 or severity_val > 5:
            raise ValueError("severity must be 1–5")
        entry["severity"] = severity_val
    if recorded_at is not None:
        entry["recorded_at"] = _parse_journal_datetime(recorded_at)
    entry["updated_at"] = _now_iso()
    journal[idx] = entry
    profile["journal"] = journal
    saved = save_patient_profile(patient_id, profile)
    return next((j for j in saved.get("journal") or [] if j.get("id") == entry_id), entry)


def add_patient_milestone(
    patient_id: str,
    *,
    label: str,
    date: str | None,
    kind: str | None = None,
    notes: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    from app.services.patient_milestones import _date_only

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned = " ".join((label or "").strip().split())
    if not cleaned:
        raise ValueError("Milestone label is required")
    when = _date_only(date)
    if not when:
        raise ValueError("Milestone date is required (YYYY-MM-DD)")
    kind_clean = " ".join((kind or "lifestyle").strip().lower().split())[:40] or "lifestyle"
    entry = {
        "id": str(uuid4()),
        "label": cleaned[:120],
        "date": when,
        "kind": kind_clean,
        "notes": (" ".join((notes or "").strip().split())[:200] or None),
        "created_at": _now_iso(),
    }
    profile = get_patient_profile(patient_id)
    rows = list(profile.get("milestones") or [])
    rows.append(entry)
    profile["milestones"] = rows
    save_patient_profile(patient_id, profile)
    return entry


def update_patient_milestone(
    patient_id: str,
    milestone_id: str,
    *,
    label: str | None = None,
    date: str | None = None,
    kind: str | None = None,
    notes: str | None = None,
) -> dict[str, Any] | None:
    from app.services.patient_milestones import _date_only

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    rows = list(profile.get("milestones") or [])
    target = None
    for row in rows:
        if str(row.get("id")) == str(milestone_id):
            target = row
            break
    if not target:
        return None
    if label is not None:
        cleaned = " ".join(label.strip().split())
        if not cleaned:
            raise ValueError("Milestone label is required")
        target["label"] = cleaned[:120]
    if date is not None:
        when = _date_only(date)
        if not when:
            raise ValueError("Milestone date must be YYYY-MM-DD")
        target["date"] = when
    if kind is not None:
        target["kind"] = " ".join(kind.strip().lower().split())[:40] or "lifestyle"
    if notes is not None:
        target["notes"] = (" ".join(notes.strip().split())[:200] or None)
    profile["milestones"] = rows
    save_patient_profile(patient_id, profile)
    return target


def delete_patient_milestone(patient_id: str, milestone_id: str) -> bool:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return False
    profile = get_patient_profile(patient_id)
    before = list(profile.get("milestones") or [])
    rows = [r for r in before if str(r.get("id")) != str(milestone_id)]
    if len(rows) == len(before):
        return False
    profile["milestones"] = rows
    save_patient_profile(patient_id, profile)
    return True


def _sort_medications(medications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(m: dict[str, Any]) -> tuple:
        active = 0 if (m.get("status") or "active") == "active" else 1
        return (active, str(m.get("name") or "").lower())

    return sorted(medications, key=sort_key)


def _normalize_conditions(raw: Any) -> list[str]:
    if raw is None:
        return []
    items: list[str] = []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
        items = [p for p in parts if p]
    elif isinstance(raw, list):
        for item in raw:
            text = str(item or "").strip()
            if text:
                items.append(text)
    # Dedupe case-insensitively, keep first casing
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item[:80])
    return out[:20]


def _normalize_med_date(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date().isoformat()
    except ValueError as exc:
        raise ValueError("started_at / stopped_at must be YYYY-MM-DD") from exc


def add_patient_medication(
    patient_id: str,
    *,
    name: str,
    official_name: str | None = None,
    dosage: str | None = None,
    frequency: str | None = None,
    conditions: Any = None,
    notes: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    category: str | None = None,
    show_on_log: bool | None = None,
    source_document_id: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned_official = " ".join((official_name or "").strip().split()) or None
    cleaned_name = " ".join((name or "").strip().split()) or cleaned_official or ""
    if not cleaned_name:
        raise ValueError("Medication name is required")
    if cleaned_official and cleaned_official.casefold() == cleaned_name.casefold():
        cleaned_official = None
    start = _normalize_med_date(started_at)
    end = _normalize_med_date(ended_at)
    if start and end and end < start:
        raise ValueError("End date must be on or after the start date")
    cat = _normalize_medication_category(category)
    freq = (frequency or "").strip() or None
    profile = get_patient_profile(patient_id)
    # Reuse existing active med with same preferred or official name
    existing = next(
        (
            m
            for m in (profile.get("medications") or [])
            if (
                _medication_name_key(m.get("name")) == _medication_name_key(cleaned_name)
                or (
                    cleaned_official
                    and _medication_name_key(m.get("official_name") or m.get("name"))
                    == _medication_name_key(cleaned_official)
                )
            )
            and str(m.get("status") or "active") != "stopped"
        ),
        None,
    )
    if existing:
        if show_on_log is True and not existing.get("show_on_log"):
            updated = update_patient_medication(
                patient_id, existing["id"], show_on_log=True
            )
            return updated or existing
        return existing
    now = _now_iso()
    entry = {
        "id": str(uuid4()),
        "name": cleaned_name[:120],
        "official_name": cleaned_official[:120] if cleaned_official else None,
        "dosage": (dosage or "").strip() or None,
        "frequency": freq,
        "conditions": _normalize_conditions(conditions),
        "notes": (notes or "").strip() or None,
        "category": cat,
        "show_on_log": bool(show_on_log) if show_on_log is not None else False,
        "status": "stopped" if end else "active",
        "started_at": start,
        "stopped_at": end,
        "created_at": now,
        "updated_at": now,
        "dosage_history": [],
    }
    doc_id = (source_document_id or "").strip() or None
    if doc_id:
        entry["source_document_id"] = doc_id[:120]
    from app.services.medication_identity import apply_identity_fields

    apply_identity_fields(entry)
    profile.setdefault("medications", []).append(entry)
    _remove_custom_log_tiles_matching_label(profile, entry.get("name") or "")
    if entry.get("official_name"):
        _remove_custom_log_tiles_matching_label(profile, entry.get("official_name") or "")
    saved = save_patient_profile(patient_id, profile)
    return next((m for m in saved["medications"] if m["id"] == entry["id"]), entry)


def _sync_log_tiles_for_medication_rename(
    profile: dict[str, Any],
    *,
    old_name: str,
    new_name: str,
) -> bool:
    """Remove Home Log tiles that duplicated the medication name (legacy cleanup)."""
    changed = _remove_custom_log_tiles_matching_label(profile, old_name)
    if _remove_custom_log_tiles_matching_label(profile, new_name):
        changed = True
    return changed


def update_patient_medication(
    patient_id: str,
    medication_id: str,
    *,
    name: str | None = None,
    official_name: str | None = ...,  # type: ignore[assignment]
    dosage: str | None = ...,  # type: ignore[assignment]
    frequency: str | None = ...,  # type: ignore[assignment]
    conditions: Any = ...,
    notes: str | None = ...,  # type: ignore[assignment]
    started_at: str | None = ...,  # type: ignore[assignment]
    ended_at: str | None = ...,  # type: ignore[assignment]
    category: str | None = ...,  # type: ignore[assignment]
    show_on_log: bool | None = ...,  # type: ignore[assignment]
    history_note: str | None = None,
    effective_at: str | None = None,
) -> dict[str, Any] | None:
    """Update a medication. Dosage/frequency changes append to dosage_history.

    Use ellipsis (...) as sentinel for “field not provided”.
    Setting ended_at stops the medication; clearing it reopens as active.
    ``effective_at`` is the clinical date of a dose/frequency change (YYYY-MM-DD);
    defaults to today when omitted.
    """
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    meds = profile.get("medications") or []
    idx = next((i for i, m in enumerate(meds) if m.get("id") == medication_id), None)
    if idx is None:
        return None
    med = dict(meds[idx])
    old_dosage = med.get("dosage")
    old_frequency = med.get("frequency")
    old_name = str(med.get("name") or "").strip()

    if name is not None:
        cleaned = " ".join(name.strip().split())
        if not cleaned:
            raise ValueError("Medication name is required")
        med["name"] = cleaned[:120]
    if official_name is not ...:
        cleaned_official = " ".join((official_name or "").strip().split()) or None
        preferred = str(med.get("name") or "").strip()
        if cleaned_official and preferred and cleaned_official.casefold() == preferred.casefold():
            cleaned_official = None
        med["official_name"] = cleaned_official[:120] if cleaned_official else None
    if dosage is not ...:
        med["dosage"] = (dosage or "").strip() or None
    if frequency is not ...:
        med["frequency"] = (frequency or "").strip() or None
    if conditions is not ...:
        med["conditions"] = _normalize_conditions(conditions)
    if notes is not ...:
        med["notes"] = (notes or "").strip() or None
    if category is not ...:
        med["category"] = _normalize_medication_category(category)
    if show_on_log is not ...:
        med["show_on_log"] = bool(show_on_log)
    elif "show_on_log" not in med:
        med["show_on_log"] = default_show_on_log(med.get("category"), med.get("frequency"))
    if started_at is not ...:
        med["started_at"] = _normalize_med_date(started_at)
    if ended_at is not ...:
        end = _normalize_med_date(ended_at)
        med["stopped_at"] = end
        med["status"] = "stopped" if end else "active"

    start = med.get("started_at")
    end = med.get("stopped_at")
    if start and end and str(end) < str(start):
        raise ValueError("End date must be on or after the start date")

    dosage_changed = dosage is not ... and med.get("dosage") != old_dosage
    frequency_changed = frequency is not ... and med.get("frequency") != old_frequency
    if dosage_changed or frequency_changed:
        effective = _normalize_med_date(effective_at) or datetime.now().date().isoformat()
        history = list(med.get("dosage_history") or [])
        history.append(
            {
                "dosage": old_dosage,
                "frequency": old_frequency,
                "changed_at": _now_iso(),
                "effective_at": effective,
                "note": (history_note or "").strip() or None,
            }
        )
        med["dosage_history"] = history
    med["updated_at"] = _now_iso()
    from app.services.medication_identity import apply_identity_fields

    apply_identity_fields(med)
    meds[idx] = med
    profile["medications"] = meds
    # Medications belong under Meds / Show on Log — drop duplicate Home Log tiles.
    _remove_custom_log_tiles_matching_label(profile, old_name)
    _remove_custom_log_tiles_matching_label(profile, str(med.get("name") or ""))
    if med.get("official_name"):
        _remove_custom_log_tiles_matching_label(profile, str(med.get("official_name") or ""))
    saved = save_patient_profile(patient_id, profile)
    return next((m for m in saved["medications"] if m["id"] == medication_id), med)


def stop_patient_medication(
    patient_id: str,
    medication_id: str,
    *,
    stopped_at: str | None = None,
) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    meds = profile.get("medications") or []
    idx = next((i for i, m in enumerate(meds) if m.get("id") == medication_id), None)
    if idx is None:
        return None
    med = dict(meds[idx])
    med["status"] = "stopped"
    med["stopped_at"] = _normalize_med_date(stopped_at) or datetime.now(timezone.utc).date().isoformat()
    med["updated_at"] = _now_iso()
    meds[idx] = med
    profile["medications"] = meds
    saved = save_patient_profile(patient_id, profile)
    return next((m for m in saved["medications"] if m["id"] == medication_id), med)


def delete_patient_medication(patient_id: str, medication_id: str) -> dict[str, Any] | None:
    """Remove a medication. Returns the deleted medication dict, or None if not found."""
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    meds = list(profile.get("medications") or [])
    removed = next((m for m in meds if m.get("id") == medication_id), None)
    if not removed:
        return None
    profile["medications"] = [m for m in meds if m.get("id") != medication_id]
    save_patient_profile(patient_id, profile)
    return removed


def add_patient_food_drink(patient_id: str, label: str) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned = _normalize_food_drink_label(label)
    if not cleaned:
        raise ValueError("Food or drink name is required")
    profile = get_patient_profile(patient_id)
    foods = list(profile.get("food_drinks") or [])
    existing = next(
        (f for f in foods if str(f.get("label") or "").lower() == cleaned.lower()),
        None,
    )
    if existing:
        return existing
    entry = {"id": str(uuid4()), "label": cleaned, "created_at": _now_iso()}
    foods.append(entry)
    profile["food_drinks"] = foods
    saved = save_patient_profile(patient_id, profile)
    return next((f for f in saved.get("food_drinks") or [] if f.get("id") == entry["id"]), entry)


def update_patient_food_drink(
    patient_id: str,
    food_id: str,
    *,
    label: str,
) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned = _normalize_food_drink_label(label)
    if not cleaned:
        raise ValueError("Food or drink name is required")
    profile = get_patient_profile(patient_id)
    foods = list(profile.get("food_drinks") or [])
    idx = next((i for i, f in enumerate(foods) if f.get("id") == food_id), None)
    if idx is None:
        return None
    dup = next(
        (
            f
            for i, f in enumerate(foods)
            if i != idx and str(f.get("label") or "").lower() == cleaned.lower()
        ),
        None,
    )
    if dup:
        raise ValueError("That item is already on the list")
    item = dict(foods[idx])
    item["label"] = cleaned
    foods[idx] = item
    profile["food_drinks"] = foods
    saved = save_patient_profile(patient_id, profile)
    return next((f for f in saved.get("food_drinks") or [] if f.get("id") == food_id), item)


def delete_patient_food_drink(patient_id: str, food_id: str) -> bool:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return False
    profile = get_patient_profile(patient_id)
    before = len(profile.get("food_drinks") or [])
    profile["food_drinks"] = [
        f for f in profile.get("food_drinks") or [] if f.get("id") != food_id
    ]
    if len(profile["food_drinks"]) == before:
        return False
    save_patient_profile(patient_id, profile)
    return True


def _normalize_care_item_kind(kind: str | None) -> str:
    cleaned = str(kind or "other").strip().lower().replace("-", "_").replace(" ", "_")
    if cleaned == "followup":
        cleaned = "follow_up"
    return cleaned if cleaned in CARE_ITEM_KINDS else "other"


def _normalize_care_item_status(status: str | None) -> str:
    cleaned = str(status or "open").strip().lower()
    return cleaned if cleaned in CARE_ITEM_STATUSES else "open"


def _normalize_care_item_date(value: str | None) -> str | None:
    raw = str(value or "").strip()[:10]
    if not raw:
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    return raw


def _normalize_care_item_activity(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        note = " ".join(str(row.get("note") or "").split())[:500]
        if not note:
            continue
        kind = str(row.get("kind") or "note").strip().lower()
        if kind not in {"note", "sent", "follow_up", "done", "call"}:
            kind = "note"
        out.append(
            {
                "at": str(row.get("at") or _now_iso()),
                "note": note,
                "kind": kind,
            }
        )
    return out[-40:]


def _normalize_care_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = " ".join(str(raw.get("title") or "").split())[:160]
    if not title:
        return None
    item_id = str(raw.get("id") or "").strip()
    if not item_id:
        from uuid import uuid4

        item_id = str(uuid4())
    status = _normalize_care_item_status(raw.get("status"))
    return {
        "id": item_id,
        "title": title,
        "kind": _normalize_care_item_kind(raw.get("kind")),
        "due_at": _normalize_care_item_date(raw.get("due_at")),
        "notes": " ".join(str(raw.get("notes") or "").split())[:2000],
        "contact": " ".join(str(raw.get("contact") or "").split())[:120],
        "status": status,
        "created_at": str(raw.get("created_at") or _now_iso()),
        "updated_at": str(raw.get("updated_at") or raw.get("created_at") or _now_iso()),
        "completed_at": str(raw.get("completed_at") or "") or None,
        "activity": _normalize_care_item_activity(raw.get("activity")),
    }


def _normalize_care_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        item = _normalize_care_item(row)
        if item:
            items.append(item)

    def sort_key(item: dict[str, Any]) -> tuple:
        status = item.get("status") or "open"
        due = item.get("due_at") or "9999-99-99"
        open_rank = 0 if status == "open" else 1 if status == "done" else 2
        return (open_rank, due, item.get("created_at") or "")

    return sorted(items, key=sort_key)


def add_patient_care_item(
    patient_id: str,
    *,
    title: str,
    kind: str | None = None,
    due_at: str | None = None,
    notes: str | None = None,
    contact: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned_title = " ".join((title or "").split())[:160]
    if not cleaned_title:
        raise ValueError("Title is required")
    now = _now_iso()
    entry = _normalize_care_item(
        {
            "id": str(uuid4()),
            "title": cleaned_title,
            "kind": kind,
            "due_at": due_at,
            "notes": notes,
            "contact": contact,
            "status": "open",
            "created_at": now,
            "updated_at": now,
            "activity": [],
        }
    )
    if not entry:
        raise ValueError("Title is required")
    profile = get_patient_profile(patient_id)
    items = list(profile.get("care_items") or [])
    items.append(entry)
    profile["care_items"] = items
    saved = save_patient_profile(patient_id, profile)
    return next((i for i in saved.get("care_items") or [] if i.get("id") == entry["id"]), entry)


def update_patient_care_item(
    patient_id: str,
    item_id: str,
    *,
    title: str | None = None,
    kind: str | None = None,
    due_at: Any = ...,
    notes: str | None = None,
    contact: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    items = list(profile.get("care_items") or [])
    idx = next((i for i, row in enumerate(items) if row.get("id") == item_id), None)
    if idx is None:
        return None
    item = dict(items[idx])
    if title is not None:
        cleaned = " ".join(title.split())[:160]
        if not cleaned:
            raise ValueError("Title is required")
        item["title"] = cleaned
    if kind is not None:
        item["kind"] = _normalize_care_item_kind(kind)
    if due_at is not ...:
        item["due_at"] = _normalize_care_item_date(due_at if due_at is not None else "")
    if notes is not None:
        item["notes"] = " ".join(notes.split())[:2000]
    if contact is not None:
        item["contact"] = " ".join(contact.split())[:120]
    if status is not None:
        next_status = _normalize_care_item_status(status)
        prev = item.get("status") or "open"
        item["status"] = next_status
        if next_status == "done" and prev != "done":
            item["completed_at"] = _now_iso()
            activity = list(item.get("activity") or [])
            activity.append({"at": _now_iso(), "note": "Marked done", "kind": "done"})
            item["activity"] = activity[-40:]
        elif next_status == "open":
            item["completed_at"] = None
    item["updated_at"] = _now_iso()
    items[idx] = _normalize_care_item(item) or item
    profile["care_items"] = items
    saved = save_patient_profile(patient_id, profile)
    return next((i for i in saved.get("care_items") or [] if i.get("id") == item_id), items[idx])


def add_patient_care_item_activity(
    patient_id: str,
    item_id: str,
    *,
    note: str,
    kind: str | None = None,
    follow_up_at: str | None = None,
) -> dict[str, Any] | None:
    cleaned_note = " ".join((note or "").split())[:500]
    if not cleaned_note:
        raise ValueError("Note is required")
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    items = list(profile.get("care_items") or [])
    idx = next((i for i, row in enumerate(items) if row.get("id") == item_id), None)
    if idx is None:
        return None
    item = dict(items[idx])
    activity_kind = str(kind or "note").strip().lower()
    if activity_kind not in {"note", "sent", "follow_up", "done", "call"}:
        activity_kind = "note"
    activity = list(item.get("activity") or [])
    activity.append({"at": _now_iso(), "note": cleaned_note, "kind": activity_kind})
    item["activity"] = activity[-40:]
    follow = _normalize_care_item_date(follow_up_at)
    if follow:
        item["due_at"] = follow
        if (item.get("status") or "open") == "done":
            item["status"] = "open"
            item["completed_at"] = None
    item["updated_at"] = _now_iso()
    items[idx] = _normalize_care_item(item) or item
    profile["care_items"] = items
    saved = save_patient_profile(patient_id, profile)
    return next((i for i in saved.get("care_items") or [] if i.get("id") == item_id), items[idx])


def delete_patient_care_item(patient_id: str, item_id: str) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    items = list(profile.get("care_items") or [])
    removed = next((row for row in items if row.get("id") == item_id), None)
    if not removed:
        return None
    profile["care_items"] = [row for row in items if row.get("id") != item_id]
    save_patient_profile(patient_id, profile)
    return removed


DOCTOR_QUESTION_STATUSES = frozenset({"open", "asked", "answered"})


def _normalize_doctor_question_status(raw: Any) -> str:
    value = str(raw or "open").strip().lower()
    if value in {"to_ask", "todo", "ask"}:
        return "open"
    if value in DOCTOR_QUESTION_STATUSES:
        return value
    return "open"


def _doctor_name_key(name: str | None) -> str:
    return " ".join(str(name or "").split()).lower()


def _normalize_doctor_question(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    question = " ".join(str(raw.get("question") or raw.get("text") or "").split())[:500]
    if not question:
        return None
    doctor = " ".join(str(raw.get("doctor") or raw.get("practitioner") or "").split())[:120]
    role = " ".join(str(raw.get("role") or "").split())[:80]
    notes = " ".join(str(raw.get("notes") or "").split())[:2000]
    answer = " ".join(str(raw.get("answer") or "").split())[:2000]
    status = _normalize_doctor_question_status(raw.get("status"))
    return {
        "id": str(raw.get("id") or ""),
        "question": question,
        "doctor": doctor or None,
        "role": role or None,
        "notes": notes or None,
        "answer": answer or None,
        "status": status,
        "recorded_by": " ".join(str(raw.get("recorded_by") or "").split())[:120] or None,
        "created_at": str(raw.get("created_at") or _now_iso()),
        "updated_at": str(raw.get("updated_at") or raw.get("created_at") or _now_iso()),
        "asked_at": str(raw.get("asked_at") or "") or None,
        "answered_at": str(raw.get("answered_at") or "") or None,
    }


def _normalize_doctor_questions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for row in raw:
        item = _normalize_doctor_question(row)
        if item and item.get("id"):
            items.append(item)

    def sort_key(item: dict[str, Any]) -> tuple:
        status = item.get("status") or "open"
        rank = 0 if status == "open" else 1 if status == "asked" else 2
        doctor = _doctor_name_key(item.get("doctor")) or "zzz"
        return (rank, doctor, item.get("created_at") or "")

    return sorted(items, key=sort_key)


def list_patient_doctor_questions(patient_id: str) -> list[dict[str, Any]]:
    return list(get_patient_profile(patient_id).get("doctor_questions") or [])


def add_patient_doctor_question(
    patient_id: str,
    *,
    question: str,
    doctor: str | None = None,
    role: str | None = None,
    notes: str | None = None,
    recorded_by: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    from app.services.care_timeline import find_team_member

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    now = _now_iso()
    entry = _normalize_doctor_question(
        {
            "id": str(uuid4()),
            "question": question,
            "doctor": doctor,
            "role": role,
            "notes": notes,
            "status": "open",
            "recorded_by": recorded_by,
            "created_at": now,
            "updated_at": now,
        }
    )
    if not entry:
        raise ValueError("Enter the question")
    profile = get_patient_profile(patient_id)
    if entry.get("doctor") and not entry.get("role"):
        member = find_team_member(profile.get("care_team"), entry["doctor"])
        if member and member.get("role"):
            entry["role"] = member["role"]
    questions = list(profile.get("doctor_questions") or [])
    questions.append(entry)
    profile["doctor_questions"] = questions
    saved = save_patient_profile(patient_id, profile)
    return next((q for q in saved.get("doctor_questions") or [] if q.get("id") == entry["id"]), entry)


def update_patient_doctor_question(
    patient_id: str,
    question_id: str,
    *,
    question: str | None = None,
    doctor: Any = ...,
    role: Any = ...,
    notes: Any = ...,
    answer: Any = ...,
    status: str | None = None,
) -> dict[str, Any] | None:
    from app.services.care_timeline import find_team_member

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    items = list(profile.get("doctor_questions") or [])
    idx = next((i for i, row in enumerate(items) if row.get("id") == question_id), None)
    if idx is None:
        return None
    item = dict(items[idx])
    if question is not None:
        cleaned = " ".join(question.split())[:500]
        if not cleaned:
            raise ValueError("Enter the question")
        item["question"] = cleaned
    if doctor is not ...:
        item["doctor"] = " ".join(str(doctor or "").split())[:120] or None
    if role is not ...:
        item["role"] = " ".join(str(role or "").split())[:80] or None
    if notes is not ...:
        item["notes"] = " ".join(str(notes or "").split())[:2000] or None
    if answer is not ...:
        item["answer"] = " ".join(str(answer or "").split())[:2000] or None
    if status is not None:
        next_status = _normalize_doctor_question_status(status)
        prev = item.get("status") or "open"
        item["status"] = next_status
        if next_status == "asked" and prev != "asked":
            item["asked_at"] = _now_iso()
        if next_status == "answered":
            if prev != "answered":
                item["answered_at"] = _now_iso()
            if prev == "open":
                item["asked_at"] = item.get("asked_at") or _now_iso()
        if next_status == "open":
            item["asked_at"] = None
            item["answered_at"] = None
    if item.get("doctor") and not item.get("role"):
        member = find_team_member(profile.get("care_team"), item["doctor"])
        if member and member.get("role"):
            item["role"] = member["role"]
    item["updated_at"] = _now_iso()
    items[idx] = _normalize_doctor_question(item) or item
    profile["doctor_questions"] = items
    saved = save_patient_profile(patient_id, profile)
    return next((q for q in saved.get("doctor_questions") or [] if q.get("id") == question_id), items[idx])


def delete_patient_doctor_question(patient_id: str, question_id: str) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    items = list(profile.get("doctor_questions") or [])
    removed = next((row for row in items if row.get("id") == question_id), None)
    if not removed:
        return None
    profile["doctor_questions"] = [row for row in items if row.get("id") != question_id]
    save_patient_profile(patient_id, profile)
    return removed


def _normalize_care_events(raw: Any) -> list[dict[str, Any]]:
    from app.services.care_timeline import normalize_care_events

    return normalize_care_events(raw)


def _normalize_team(raw: Any) -> list[dict[str, Any]]:
    from app.services.care_timeline import normalize_team_members

    return normalize_team_members(raw)


def _ensure_team_member(
    profile: dict[str, Any],
    name: str | None,
    role: str | None,
    environment: str | None,
) -> None:
    """Add a practitioner to the directory the first time they are recorded."""
    from app.services.care_timeline import find_team_member, normalize_team_member

    cleaned = " ".join(str(name or "").split())
    if not cleaned:
        return
    team = list(profile.get("care_team") or [])
    existing = find_team_member(team, cleaned)
    if existing:
        changed = False
        if role and not existing.get("role"):
            existing["role"] = role
            changed = True
        env = " ".join(str(environment or "").split())
        if env and env.lower() not in {e.lower() for e in existing.get("environments") or []}:
            existing.setdefault("environments", []).append(env)
            changed = True
        if changed:
            existing["updated_at"] = _now_iso()
        profile["care_team"] = team
        return
    member = normalize_team_member(
        {
            "name": cleaned,
            "role": role,
            "environments": [environment] if environment else [],
            "notes": "Added from a timeline entry",
        }
    )
    if member:
        team.append(member)
        profile["care_team"] = team


def add_patient_team_member(
    patient_id: str,
    *,
    name: str,
    role: str | None = None,
    environments: list[str] | str | None = None,
    phone: str | None = None,
    email: str | None = None,
    contact_other: str | None = None,
    preferred_contact: str | None = None,
    notes: str | None = None,
) -> dict[str, Any] | None:
    from app.services.care_timeline import find_team_member, normalize_team_member

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    member = normalize_team_member(
        {
            "name": name,
            "role": role,
            "environments": environments,
            "phone": phone,
            "email": email,
            "contact_other": contact_other,
            "preferred_contact": preferred_contact,
            "notes": notes,
        }
    )
    if not member:
        raise ValueError("Name is required")
    profile = get_patient_profile(patient_id)
    team = list(profile.get("care_team") or [])
    if find_team_member(team, member["name"]):
        raise ValueError(f"{member['name']} is already on the care team")
    team.append(member)
    profile["care_team"] = team
    save_patient_profile(patient_id, profile, reload=False)
    return member


def update_patient_team_member(
    patient_id: str,
    member_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    """Edit a member. A rename also updates their past timeline entries."""
    from app.services.care_timeline import _name_key, normalize_team_member

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    team = list(profile.get("care_team") or [])
    idx = next((i for i, m in enumerate(team) if m.get("id") == member_id), None)
    if idx is None:
        return None
    member = dict(team[idx])
    old_name = member.get("name")
    for key in (
        "name",
        "role",
        "environments",
        "phone",
        "email",
        "contact_other",
        "preferred_contact",
        "notes",
    ):
        if key in fields and fields[key] is not None:
            member[key] = fields[key]
    member["updated_at"] = _now_iso()
    normalized = normalize_team_member(member)
    if not normalized:
        raise ValueError("Name is required")
    new_key = _name_key(normalized["name"])
    if any(i != idx and _name_key(m.get("name")) == new_key for i, m in enumerate(team)):
        raise ValueError(f"{normalized['name']} is already on the care team")
    team[idx] = normalized
    profile["care_team"] = team
    if _name_key(old_name) != new_key:
        for ev in profile.get("care_events") or []:
            if _name_key(ev.get("practitioner")) == _name_key(old_name):
                ev["practitioner"] = normalized["name"]
    save_patient_profile(patient_id, profile, reload=False)
    return normalized


def delete_patient_team_member(patient_id: str, member_id: str) -> dict[str, Any] | None:
    """Remove from the directory only; past timeline entries keep the name."""
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    team = list(profile.get("care_team") or [])
    removed = next((m for m in team if m.get("id") == member_id), None)
    if not removed:
        return None
    profile["care_team"] = [m for m in team if m.get("id") != member_id]
    save_patient_profile(patient_id, profile, reload=False)
    return removed


def _normalize_family(raw: Any) -> list[dict[str, Any]]:
    from app.services.care_timeline import normalize_family_members

    return normalize_family_members(raw)


def _normalize_role_history(raw: Any) -> list[dict[str, Any]]:
    from app.services.care_timeline import normalize_role_history

    return normalize_role_history(raw)


def _apply_family_roles(
    profile: dict[str, Any],
    member: dict[str, Any],
    new_roles: list[str],
    by: str | None,
) -> None:
    """Set a member's roles, stamping since-dates and logging each change."""
    from app.services.care_timeline import family_role_key

    now = _now_iso()
    old = list(member.get("roles") or [])
    cleaned: list[str] = []
    for r in new_roles or []:
        key = family_role_key(r)
        if key and key not in cleaned:
            cleaned.append(key)
    since = dict(member.get("role_since") or {})
    history = list(profile.get("family_role_history") or [])
    for r in cleaned:
        if r not in old:
            since[r] = now
            history.append({"at": now, "role": r, "person": member["name"], "action": "assigned", "by": by})
    for r in old:
        if r not in cleaned:
            since.pop(r, None)
            history.append({"at": now, "role": r, "person": member["name"], "action": "unassigned", "by": by})
    member["roles"] = cleaned
    member["role_since"] = {k: v for k, v in since.items() if k in cleaned}
    profile["family_role_history"] = history


def add_patient_family_member(
    patient_id: str,
    *,
    by: str | None = None,
    **fields: Any,
) -> dict[str, Any] | None:
    from app.services.care_timeline import _name_key, normalize_family_member

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    roles = fields.pop("roles", None) or []
    member = normalize_family_member({**fields, "roles": []})
    if not member:
        raise ValueError("Name is required")
    profile = get_patient_profile(patient_id)
    family = list(profile.get("family_team") or [])
    if any(_name_key(m["name"]) == _name_key(member["name"]) for m in family):
        raise ValueError(f"{member['name']} is already on the family team")
    _apply_family_roles(profile, member, roles, by)
    family.append(member)
    profile["family_team"] = family
    save_patient_profile(patient_id, profile, reload=False)
    return member


def update_patient_family_member(
    patient_id: str,
    member_id: str,
    *,
    by: str | None = None,
    **fields: Any,
) -> dict[str, Any] | None:
    from app.services.care_timeline import _name_key, normalize_family_member

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    family = list(profile.get("family_team") or [])
    idx = next((i for i, m in enumerate(family) if m.get("id") == member_id), None)
    if idx is None:
        return None
    member = dict(family[idx])
    for key in ("name", "relationship", "phone", "email", "preferred_contact", "availability"):
        if key in fields and fields[key] is not None:
            member[key] = fields[key]
    member["updated_at"] = _now_iso()
    normalized = normalize_family_member(member)
    if not normalized:
        raise ValueError("Name is required")
    if any(i != idx and _name_key(m["name"]) == _name_key(normalized["name"]) for i, m in enumerate(family)):
        raise ValueError(f"{normalized['name']} is already on the family team")
    if "roles" in fields and fields["roles"] is not None:
        _apply_family_roles(profile, normalized, list(fields["roles"]), by)
    family[idx] = normalized
    profile["family_team"] = family
    save_patient_profile(patient_id, profile, reload=False)
    return normalized


def set_patient_family_role(
    patient_id: str,
    role: str,
    member_ids: list[str],
    *,
    by: str | None = None,
) -> list[dict[str, Any]] | None:
    """Assign one role to exactly these family members (from the role board)."""
    from app.services.care_timeline import family_role_key

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    key = family_role_key(role)
    if not key:
        raise ValueError("Role is required")
    wanted = set(member_ids or [])
    profile = get_patient_profile(patient_id)
    family = list(profile.get("family_team") or [])
    for i, m in enumerate(family):
        member = dict(m)
        roles = list(member.get("roles") or [])
        has = key in roles
        if m["id"] in wanted and not has:
            _apply_family_roles(profile, member, [*roles, key], by)
        elif m["id"] not in wanted and has:
            _apply_family_roles(profile, member, [r for r in roles if r != key], by)
        family[i] = member
    profile["family_team"] = family
    save_patient_profile(patient_id, profile, reload=False)
    return family


def delete_patient_family_member(
    patient_id: str, member_id: str, *, by: str | None = None
) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    family = list(profile.get("family_team") or [])
    removed = next((m for m in family if m.get("id") == member_id), None)
    if not removed:
        return None
    _apply_family_roles(profile, dict(removed), [], by)
    profile["family_team"] = [m for m in family if m.get("id") != member_id]
    save_patient_profile(patient_id, profile, reload=False)
    return removed


def add_patient_care_event(
    patient_id: str,
    *,
    kind: str | None,
    occurred_at: str | None,
    practitioner: str | None = None,
    role: str | None = None,
    location: str | None = None,
    summary: str | None = None,
    source_document_id: str | None = None,
    source_document_title: str | None = None,
    source_documents: list[dict[str, Any]] | None = None,
    summary_source: str | None = None,
    recorded_by: str | None = None,
    source: str = "manual",
) -> dict[str, Any] | None:
    """Record a fact on the family timeline (visit, test, transfer, note…)."""
    from uuid import uuid4

    from app.services.care_timeline import normalize_care_event, parse_event_datetime

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    if not (summary or "").strip() and not (practitioner or "").strip():
        raise ValueError("Add who was in or what happened")
    profile = get_patient_profile(patient_id)
    if practitioner and not (role or "").strip():
        from app.services.care_timeline import find_team_member

        member = find_team_member(profile.get("care_team"), practitioner)
        if member and member.get("role"):
            role = member["role"]
    entry = normalize_care_event(
        {
            "id": str(uuid4()),
            "kind": kind,
            "occurred_at": parse_event_datetime(occurred_at),
            "practitioner": practitioner,
            "role": role,
            "location": location,
            "summary": summary,
            "source_document_id": source_document_id,
            "source_document_title": source_document_title,
            "source_documents": source_documents or [],
            "summary_source": summary_source,
            "recorded_by": recorded_by,
            "source": source or "manual",
            "created_at": _now_iso(),
        }
    )
    if not entry:
        raise ValueError("Add who was in or what happened")
    events = list(profile.get("care_events") or [])
    events.append(entry)
    profile["care_events"] = events
    _ensure_team_member(profile, entry.get("practitioner"), entry.get("role"), entry.get("location"))
    save_patient_profile(patient_id, profile, reload=False)
    return entry


def update_patient_care_event(
    patient_id: str,
    event_id: str,
    *,
    updated_by: str | None = None,
    **fields: Any,
) -> dict[str, Any] | None:
    from app.services.care_timeline import normalize_care_event, parse_event_datetime

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    events = list(profile.get("care_events") or [])
    idx = next((i for i, e in enumerate(events) if e.get("id") == event_id), None)
    if idx is None:
        return None
    entry = dict(events[idx])
    allowed = {
        "kind",
        "occurred_at",
        "practitioner",
        "role",
        "location",
        "summary",
        "source_document_id",
        "source_document_title",
        "source_documents",
        "summary_source",
    }
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "occurred_at":
            value = parse_event_datetime(value)
        entry[key] = value
    if not str(entry.get("summary") or "").strip() and not str(entry.get("practitioner") or "").strip():
        raise ValueError("Add who was in or what happened")
    entry["updated_at"] = _now_iso()
    entry["updated_by"] = updated_by
    normalized = normalize_care_event(entry)
    if not normalized:
        raise ValueError("Add who was in or what happened")
    events[idx] = normalized
    profile["care_events"] = events
    _ensure_team_member(
        profile, normalized.get("practitioner"), normalized.get("role"), normalized.get("location")
    )
    save_patient_profile(patient_id, profile, reload=False)
    return normalized


def delete_patient_care_event(patient_id: str, event_id: str) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    events = list(profile.get("care_events") or [])
    removed = next((e for e in events if e.get("id") == event_id), None)
    if not removed:
        return None
    profile["care_events"] = [e for e in events if e.get("id") != event_id]
    save_patient_profile(patient_id, profile, reload=False)
    return removed


def import_patient_care_events(
    patient_id: str,
    incoming: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Append imported events (e.g. WhatsApp), skipping ones already stored."""
    from uuid import uuid4

    from app.services.care_timeline import normalize_care_event

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    profile = get_patient_profile(patient_id)
    events = list(profile.get("care_events") or [])
    from app.services.whatsapp_import import whatsapp_dedupe_keys

    seen: set[str] = set()
    for existing in events:
        if existing.get("source_ref"):
            seen.add(existing["source_ref"])
        if existing.get("source") == "whatsapp":
            seen |= whatsapp_dedupe_keys(
                existing.get("recorded_by") or "",
                existing.get("occurred_at") or "",
                existing.get("summary") or "",
                source_ref=existing.get("source_ref"),
            )
    added = 0
    skipped = 0
    now = _now_iso()
    for raw in incoming or []:
        incoming_keys = whatsapp_dedupe_keys(
            raw.get("recorded_by") or "",
            raw.get("occurred_at") or "",
            raw.get("summary") or "",
            source_ref=raw.get("source_ref"),
        )
        if incoming_keys & seen:
            skipped += 1
            continue
        entry = normalize_care_event({**raw, "id": str(uuid4()), "created_at": now})
        if not entry:
            skipped += 1
            continue
        events.append(entry)
        seen |= incoming_keys
        added += 1
    profile["care_events"] = events
    save_patient_profile(patient_id, profile, reload=False)
    return {"added": added, "skipped": skipped, "total": len(events)}


def _normalize_log_tile_label(label: str) -> str:
    cleaned = " ".join((label or "").strip().split())
    return cleaned[:80]


def medication_home_log_labels(profile: dict[str, Any] | None = None) -> set[str]:
    """Labels that belong under Meds (Show on Log), not as Home Log tiles."""
    labels: set[str] = set()
    for remedy in COMMON_REMEDIES:
        name = _normalize_log_tile_label(str(remedy.get("name") or ""))
        if name:
            labels.add(name.lower())
    for med in (profile or {}).get("medications") or []:
        if not isinstance(med, dict):
            continue
        if (med.get("status") or "active") == "stopped" or med.get("ended_at"):
            continue
        for raw in (med.get("name"), med.get("official_name")):
            name = _normalize_log_tile_label(str(raw or ""))
            if name:
                labels.add(name.lower())
    return labels


def custom_log_tile_is_medication_duplicate(
    label: str,
    profile: dict[str, Any] | None = None,
) -> bool:
    cleaned = _normalize_log_tile_label(label)
    if not cleaned:
        return False
    return cleaned.lower() in medication_home_log_labels(profile)


def _remove_custom_log_tiles_matching_label(
    profile: dict[str, Any],
    label: str,
) -> bool:
    """Drop Home Log custom tiles that duplicate a medication/remedy name."""
    needle = _normalize_log_tile_label(label).lower()
    if not needle:
        return False
    tiles = list(profile.get("log_custom_tiles") or [])
    keep: list[dict[str, Any]] = []
    removed_ids: list[str] = []
    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        tile_label = _normalize_log_tile_label(str(tile.get("label") or ""))
        if tile_label.lower() == needle:
            removed_ids.append(str(tile.get("id") or ""))
            continue
        keep.append(tile)
    if not removed_ids:
        return False
    profile["log_custom_tiles"] = keep
    drop_keys = {f"custom:{tid}" for tid in removed_ids if tid}
    profile["log_tile_order"] = [
        k for k in (profile.get("log_tile_order") or []) if k not in drop_keys
    ]
    profile["log_tile_hidden"] = [
        k for k in (profile.get("log_tile_hidden") or []) if k not in drop_keys
    ]
    return True


def _normalize_log_custom_tiles(raw: Any) -> list[dict[str, Any]]:
    from uuid import uuid4

    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = _normalize_log_tile_label(str(item.get("label") or ""))
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        tile_id = str(item.get("id") or uuid4())
        scale = bool(item.get("scale"))
        kind = str(item.get("kind") or ("symptom" if scale else "note")).strip().lower()
        if kind not in {"feeling", "symptom", "note", "medication"}:
            kind = "symptom" if scale else "note"
        out.append(
            {
                "id": tile_id,
                "label": label,
                "scale": scale,
                "kind": kind,
                "created_at": item.get("created_at"),
            }
        )
    return out


def add_patient_log_tile(
    patient_id: str,
    *,
    label: str,
    scale: bool = False,
    kind: str | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned = _normalize_log_tile_label(label)
    if not cleaned:
        raise ValueError("Option name is required")
    profile = get_patient_profile(patient_id)
    if custom_log_tile_is_medication_duplicate(cleaned, profile):
        raise ValueError(
            "That name is a medication or remedy. Add it under Medications and turn on Show on Log — it will appear under Meds, not as its own Home Log tile."
        )
    tiles = list(profile.get("log_custom_tiles") or [])
    existing = next(
        (t for t in tiles if str(t.get("label") or "").lower() == cleaned.lower()),
        None,
    )
    if existing:
        return existing
    use_scale = bool(scale)
    use_kind = (kind or ("symptom" if use_scale else "note")).strip().lower()
    if use_kind not in {"feeling", "symptom", "note", "medication"}:
        use_kind = "symptom" if use_scale else "note"
    entry = {
        "id": str(uuid4()),
        "label": cleaned,
        "scale": use_scale,
        "kind": use_kind,
        "created_at": _now_iso(),
    }
    tiles.append(entry)
    profile["log_custom_tiles"] = tiles
    order = list(profile.get("log_tile_order") or [])
    custom_key = f"custom:{entry['id']}"
    if custom_key not in order:
        order.append(custom_key)
    profile["log_tile_order"] = order
    saved = save_patient_profile(patient_id, profile)
    return next(
        (t for t in saved.get("log_custom_tiles") or [] if t.get("id") == entry["id"]),
        entry,
    )


def delete_patient_log_tile(patient_id: str, tile_id: str) -> bool:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return False
    profile = get_patient_profile(patient_id)
    tiles = list(profile.get("log_custom_tiles") or [])
    before = len(tiles)
    profile["log_custom_tiles"] = [t for t in tiles if t.get("id") != tile_id]
    if len(profile["log_custom_tiles"]) == before:
        return False
    custom_key = f"custom:{tile_id}"
    profile["log_tile_order"] = [
        k for k in (profile.get("log_tile_order") or []) if k != custom_key
    ]
    profile["log_tile_hidden"] = [
        k for k in (profile.get("log_tile_hidden") or []) if k != custom_key
    ]
    save_patient_profile(patient_id, profile)
    return True


def rename_patient_log_tile(
    patient_id: str,
    tile_id: str,
    *,
    label: str,
) -> dict[str, Any] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned = _normalize_log_tile_label(label)
    if not cleaned:
        raise ValueError("Option name is required")
    profile = get_patient_profile(patient_id)
    tiles = list(profile.get("log_custom_tiles") or [])
    idx = next((i for i, t in enumerate(tiles) if str(t.get("id")) == str(tile_id)), None)
    if idx is None:
        return None
    clash = next(
        (
            t
            for t in tiles
            if str(t.get("id")) != str(tile_id)
            and str(t.get("label") or "").lower() == cleaned.lower()
        ),
        None,
    )
    if clash:
        raise ValueError("That option name already exists")
    tile = dict(tiles[idx])
    tile["label"] = cleaned
    tiles[idx] = tile
    profile["log_custom_tiles"] = tiles
    saved = save_patient_profile(patient_id, profile)
    return next(
        (t for t in saved.get("log_custom_tiles") or [] if str(t.get("id")) == str(tile_id)),
        tile,
    )


def set_patient_log_tile_order(patient_id: str, order: list[str]) -> list[str] | None:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned = [str(k).strip() for k in (order or []) if isinstance(k, str) and str(k).strip()]
    profile = get_patient_profile(patient_id)
    profile["log_tile_order"] = cleaned
    saved = save_patient_profile(patient_id, profile)
    return list(saved.get("log_tile_order") or [])


def set_patient_log_tile_hidden(patient_id: str, key: str, hidden: bool) -> list[str] | None:
    """Hide a Home Log tile from display, or show it again. Does not delete it."""
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned = str(key or "").strip()
    if not cleaned or len(cleaned) > 80:
        raise ValueError("Tile is required")
    profile = get_patient_profile(patient_id)
    keys = [
        str(k).strip()
        for k in (profile.get("log_tile_hidden") or [])
        if isinstance(k, str) and str(k).strip() and str(k).strip() != cleaned
    ]
    if hidden:
        keys.append(cleaned)
    profile["log_tile_hidden"] = keys[:80]
    saved = save_patient_profile(patient_id, profile)
    return list(saved.get("log_tile_hidden") or [])


def group_journal_for_charts(
    profile: dict[str, Any] | None,
    *,
    max_labels: int = 8,
) -> list[dict[str, Any]]:
    """Group journal entries by label for frequency / severity charts."""
    from collections import defaultdict

    groups: dict[str, dict[str, Any]] = {}
    for row in (profile or {}).get("journal") or []:
        if not isinstance(row, dict):
            continue
        label = _normalize_journal_label(str(row.get("label") or ""))
        if not label:
            continue
        key = label.lower()
        group = groups.get(key)
        if not group:
            group = {
                "key": key,
                "label": label,
                "kind": row.get("kind") or "note",
                "entries": [],
            }
            groups[key] = group
        # Prefer symptom/feeling kind when mixed
        kind = str(row.get("kind") or "note")
        if kind in {"symptom", "feeling"} and group.get("kind") not in {"symptom", "feeling"}:
            group["kind"] = kind
        day = str(row.get("recorded_at") or "")[:10]
        severity = row.get("severity")
        try:
            sev = int(severity) if severity is not None else None
        except (TypeError, ValueError):
            sev = None
        group["entries"].append(
            {
                "id": row.get("id"),
                "recorded_at": row.get("recorded_at"),
                "day": day,
                "severity": sev,
                "kind": kind,
                "text": row.get("text"),
            }
        )

    series: list[dict[str, Any]] = []
    for group in groups.values():
        entries = sorted(
            group["entries"],
            key=lambda e: str(e.get("recorded_at") or ""),
        )
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in entries:
            if e.get("day"):
                by_day[e["day"]].append(e)
        readings: list[dict[str, Any]] = []
        for day in sorted(by_day.keys()):
            day_rows = by_day[day]
            severities = [r["severity"] for r in day_rows if r.get("severity") is not None]
            if severities:
                value = sum(severities) / len(severities)
                metric = "severity_avg"
            else:
                value = float(len(day_rows))
                metric = "count"
            readings.append(
                {
                    "recorded_at": day,
                    "value": round(value, 2) if metric == "severity_avg" else value,
                    "count": len(day_rows),
                    "metric": metric,
                }
            )
        latest = readings[-1] if readings else None
        series.append(
            {
                "key": group["key"],
                "name": group["label"],
                "label": group["label"],
                "kind": group["kind"],
                "unit": "sev" if any(r.get("metric") == "severity_avg" for r in readings) else "count",
                "readings": readings,
                "latest": latest,
                "point_count": len(readings),
                "entry_count": len(entries),
                "latest_entry": entries[-1] if entries else None,
            }
        )

    series.sort(
        key=lambda s: (
            0 if s.get("kind") in {"symptom", "feeling"} else 1,
            -int(s.get("entry_count") or 0),
            str(s.get("name") or "").lower(),
        )
    )
    return series[:max_labels]


def recent_journal_for_prompt(
    profile: dict[str, Any] | None,
    *,
    days: int = 14,
    limit: int = 20,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for row in (profile or {}).get("journal") or []:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("recorded_at") or "")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if dt < cutoff:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def _dedupe_readings_by_date(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one reading per calendar day (last value that day wins)."""
    by_date: dict[str, dict[str, Any]] = {}
    for row in sorted(readings, key=lambda r: str(r.get("recorded_at") or "")):
        day = str(row.get("recorded_at") or "")[:10]
        if not day:
            continue
        by_date[day] = row
    return [by_date[k] for k in sorted(by_date)]


def group_diagnostics_for_charts(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Group diagnostic readings by name (case-insensitive) for charting.

    Blood-test series with multiple dated points are sorted first.
    Same-day duplicates are collapsed (last reading wins) so chart axes stay clean.
    Each reading includes original plus SI/US display values when available.
    """
    from app.services.lab_units import enrich_diagnostic_units

    groups: dict[str, dict[str, Any]] = {}
    for row in (profile or {}).get("diagnostics") or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        has_value = row.get("value") is not None
        has_text = bool(str(row.get("result") or "").strip() or str(row.get("notes") or "").strip())
        if not has_value and not has_text:
            continue
        enriched = enrich_diagnostic_units(row)
        name = (enriched.get("name") or name).strip()
        if not name:
            continue
        key = name.lower()
        group = groups.get(key)
        category = _infer_diagnostic_category(name, enriched.get("category"))
        if not group:
            group = {
                "key": key,
                "name": name,
                "unit": enriched.get("unit"),
                "unit_si": enriched.get("unit_si") or enriched.get("unit"),
                "unit_us": enriched.get("unit_us") or enriched.get("unit"),
                "category": category,
                "readings": [],
            }
            groups[key] = group
        if enriched.get("unit") and not group.get("unit"):
            group["unit"] = enriched["unit"]
        if enriched.get("unit_si"):
            group["unit_si"] = enriched["unit_si"]
        if enriched.get("unit_us"):
            group["unit_us"] = enriched["unit_us"]
        if category == "blood":
            group["category"] = "blood"
        elif category == "imaging" and group.get("category") not in {"blood", "vital"}:
            group["category"] = "imaging"
        # Prefer canonical display name (e.g. TSH over long form)
        if enriched.get("name") and len(str(enriched["name"])) <= len(str(group["name"])):
            group["name"] = enriched["name"]
        group["readings"].append(
            {
                "id": enriched.get("id"),
                "recorded_at": str(enriched.get("recorded_at") or "")[:10],
                "value": enriched.get("value"),
                "unit": enriched.get("unit"),
                "value_si": enriched.get("value_si", enriched.get("value")),
                "unit_si": enriched.get("unit_si") or enriched.get("unit"),
                "value_us": enriched.get("value_us", enriched.get("value")),
                "unit_us": enriched.get("unit_us") or enriched.get("unit"),
                "unit_system_original": enriched.get("unit_system_original"),
                "notes": enriched.get("notes"),
                "result": enriched.get("result"),
                "source": enriched.get("source")
                or ("library" if enriched.get("source_document_id") else "manual"),
                "source_document_id": enriched.get("source_document_id"),
            }
        )
    series: list[dict[str, Any]] = []
    for group in groups.values():
        raw_readings = sorted(
            group["readings"],
            key=lambda r: str(r.get("recorded_at") or ""),
        )
        readings = _dedupe_readings_by_date(raw_readings)
        latest = readings[-1] if readings else None
        # Default series unit stays SI for status/reference attachment
        series.append(
            {
                "key": group["key"],
                "name": group["name"],
                "unit": group.get("unit_si") or group.get("unit"),
                "unit_si": group.get("unit_si") or group.get("unit"),
                "unit_us": group.get("unit_us") or group.get("unit"),
                "unit_original": group.get("unit"),
                "category": group.get("category") or "blood",
                "readings": readings,
                "latest": latest,
                "point_count": len(readings),
                "raw_count": len(raw_readings),
            }
        )

    def sort_key(s: dict[str, Any]) -> tuple:
        cat = s.get("category") or "other"
        cat_rank = 0 if cat == "blood" else 1 if cat == "vital" else 2 if cat == "imaging" else 3
        # Prefer multi-point blood trends
        multi = 0 if s["point_count"] > 1 else 1
        return (cat_rank, multi, -s["point_count"], s["name"].lower())

    series.sort(key=sort_key)

    from app.services.diagnostic_references import attach_references_to_series

    return attach_references_to_series(
        series,
        date_of_birth=(profile or {}).get("date_of_birth"),
        gender=(profile or {}).get("gender"),
    )


def diagnostic_gaps_for_profile(
    profile: dict[str, Any] | None,
    series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Core-panel gaps: never recorded or older than suggested review window."""
    from app.services.diagnostic_references import compute_diagnostic_gaps

    rows = series if series is not None else group_diagnostics_for_charts(profile)
    return compute_diagnostic_gaps(
        series=rows,
        gender=(profile or {}).get("gender"),
    )


def latest_measurement(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    measurements = (profile or {}).get("measurements") or []
    return measurements[0] if measurements else None


def age_years_from_dob(date_of_birth: str | None, on_date: str | None = None) -> int | None:
    if not date_of_birth:
        return None
    try:
        dob = datetime.fromisoformat(date_of_birth[:10]).date()
        if on_date:
            today = datetime.fromisoformat(on_date[:10]).date()
        else:
            today = datetime.now(timezone.utc).date()
    except ValueError:
        return None
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(0, years)


def format_profile_for_prompt(
    patient_id: str | None,
    patient_label: str | None = None,
    *,
    rich: bool = True,
) -> str:
    """Format demographics, labs, logs, and meds for LLM prompts.

    ``rich=True`` (default for analysis) includes longer lab trends, journal
    history, symptom series, log pattern observations, and milestones so
    assessments and asks can use accumulated data.
    """
    if not patient_id:
        return ""
    profile = get_patient_profile(patient_id)
    lines: list[str] = []
    if patient_label:
        lines.append(f"Name: {patient_label}")
    dob = profile.get("date_of_birth")
    age = age_years_from_dob(dob)
    if dob:
        age_bit = f" (age {age})" if age is not None else ""
        lines.append(f"Date of birth: {dob}{age_bit}")
    elif age is not None:
        lines.append(f"Age: {age}")
    if profile.get("gender"):
        lines.append(f"Gender: {profile['gender']}")
    latest = latest_measurement(profile)
    if latest:
        bits = [f"as of {latest.get('recorded_at')}"]
        if latest.get("height_cm") is not None:
            bits.append(f"height {latest['height_cm']} cm")
        if latest.get("weight_kg") is not None:
            bits.append(f"weight {latest['weight_kg']} kg")
            if latest.get("height_cm"):
                try:
                    h_m = float(latest["height_cm"]) / 100.0
                    bmi = float(latest["weight_kg"]) / (h_m * h_m)
                    bits.append(f"BMI {bmi:.1f}")
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        lines.append("Latest measurements: " + ", ".join(bits))
        history = profile.get("measurements") or []
        if len(history) > 1:
            hist_lines = []
            for m in history[: (12 if rich else 8)]:
                parts = [str(m.get("recorded_at") or "?")]
                if m.get("height_cm") is not None:
                    parts.append(f"{m['height_cm']} cm")
                if m.get("weight_kg") is not None:
                    parts.append(f"{m['weight_kg']} kg")
                hist_lines.append(" / ".join(parts))
            lines.append("Measurement history: " + "; ".join(hist_lines))

    diag_series = group_diagnostics_for_charts(profile)
    diag_limit = 30 if rich else 10
    trend_n = 8 if rich else 5
    if diag_series:
        lines.append(
            "Lab / diagnostic readings (most recent first within each metric; cite as Patient profile). "
            "When US and Canadian units differ, both are shown:"
        )
    from app.services.lab_units import format_dual_unit_text

    for series in diag_series[:diag_limit]:
        latest_row = series.get("latest") or {}
        result_text = str(
            latest_row.get("result") or latest_row.get("notes") or ""
        ).strip()
        has_num = latest_row.get("value") is not None or latest_row.get("value_si") is not None
        if not has_num and not result_text:
            continue
        if has_num:
            dual = format_dual_unit_text(
                {
                    "name": series.get("name"),
                    "value": latest_row.get("value"),
                    "unit": latest_row.get("unit") or series.get("unit_original") or series.get("unit"),
                    "value_si": latest_row.get("value_si", latest_row.get("value")),
                    "unit_si": latest_row.get("unit_si") or series.get("unit_si") or series.get("unit"),
                    "value_us": latest_row.get("value_us", latest_row.get("value")),
                    "unit_us": latest_row.get("unit_us") or series.get("unit_us") or series.get("unit"),
                    "unit_system_original": latest_row.get("unit_system_original"),
                },
                prefer="si",
            )
            line = f"{series['name']}: {dual} ({latest_row.get('recorded_at') or '?'})"
        else:
            clipped = result_text if len(result_text) <= 220 else f"{result_text[:217]}…"
            line = f"{series['name']}: {clipped} ({latest_row.get('recorded_at') or '?'})"
        if series["point_count"] > 1 and has_num:
            trend_bits = []
            for r in (series.get("readings") or [])[-trend_n:]:
                bit = format_dual_unit_text(
                    {
                        "name": series.get("name"),
                        "value": r.get("value"),
                        "unit": r.get("unit") or series.get("unit"),
                        "value_si": r.get("value_si", r.get("value")),
                        "unit_si": r.get("unit_si") or series.get("unit_si"),
                        "value_us": r.get("value_us", r.get("value")),
                        "unit_us": r.get("unit_us") or series.get("unit_us"),
                        "unit_system_original": r.get("unit_system_original"),
                    },
                    prefer="si",
                )
                trend_bits.append(f"{r.get('recorded_at')}: {bit}")
            line += f" · history {', '.join(trend_bits)}"
        lines.append(line)

    journal_days = 90 if rich else 14
    journal_limit = 80 if rich else 20
    journal_rows = recent_journal_for_prompt(
        profile, days=journal_days, limit=journal_limit
    )
    if journal_rows:
        lines.append(
            f"Self-reports / logs (last {journal_days} days; cite as Patient profile):"
        )
        for row in journal_rows:
            bits = [
                str(row.get("recorded_at") or "?")[:16],
                str(row.get("kind") or "note"),
                str(row.get("label") or ""),
            ]
            if row.get("severity") is not None:
                bits.append(f"severity {row['severity']}/5")
            if row.get("text"):
                bits.append(str(row["text"])[:120])
            lines.append("  · " + " · ".join(b for b in bits if b))

    if rich:
        for series in group_journal_for_charts(profile)[:12]:
            readings = series.get("readings") or []
            if len(readings) < 2:
                continue
            trend = ", ".join(
                f"{r.get('recorded_at')}: {r.get('value')}"
                for r in readings[-8:]
            )
            unit = series.get("unit") or ""
            lines.append(
                f"Log trend · {series.get('label') or series.get('name')}"
                f"{f' ({unit})' if unit else ''}: {trend}"
            )

        from app.services.log_observations import build_log_observations

        journal_all = profile.get("journal") or []
        for days, label in ((7, "last 7 days"), (20, "last 20 days")):
            observations = build_log_observations(journal_all, days, max_items=5)
            if not observations:
                continue
            lines.append(
                f"Log pattern observations ({label}; cite as Patient profile):"
            )
            for obs in observations:
                text = str(obs.get("text") or "").strip()
                if text:
                    lines.append(f"  · {text}")

    meds = profile.get("medications") or []
    active_meds = [m for m in meds if (m.get("status") or "active") == "active"]
    stopped_meds = [m for m in meds if (m.get("status") or "") == "stopped"]
    from app.services.medication_identity import medication_analysis_label

    if active_meds:
        lines.append("Active medications:")
        for m in active_meds[:20]:
            bits = [medication_analysis_label(m)]
            if m.get("dosage"):
                bits.append(str(m["dosage"]))
            if m.get("frequency"):
                bits.append(str(m["frequency"]))
            if m.get("conditions"):
                bits.append("for " + ", ".join(str(c) for c in m["conditions"]))
            if m.get("started_at"):
                bits.append(f"since {m['started_at']}")
            if m.get("stopped_at"):
                bits.append(f"ended {m['stopped_at']}")
            hist = m.get("dosage_history") or []
            if hist:
                last = hist[-1]
                bits.append(
                    f"prior {last.get('dosage') or '?'} {last.get('frequency') or ''}".strip()
                    + f" until {str(last.get('changed_at') or '')[:10]}"
                )
            lines.append("  · " + " · ".join(b for b in bits if b))
    if stopped_meds:
        lines.append("Stopped medications (recent):")
        for m in stopped_meds[:8]:
            bits = [medication_analysis_label(m)]
            if m.get("dosage"):
                bits.append(str(m["dosage"]))
            if m.get("stopped_at"):
                bits.append(f"stopped {m['stopped_at']}")
            lines.append("  · " + " · ".join(b for b in bits if b))

    milestones = profile.get("milestones") or []
    if rich and milestones:
        lines.append("Timeline milestones:")
        for m in milestones[:15]:
            if not isinstance(m, dict):
                continue
            label = str(m.get("label") or m.get("kind") or "milestone").strip()
            when = str(m.get("recorded_at") or m.get("date") or "?")[:10]
            lines.append(f"  · {when}: {label}")

    if not lines:
        return ""
    return "\n".join(f"- {line}" for line in lines)


def _find_patient(reg: dict, patient_id: str) -> dict | None:
    for p in reg["patients"]:
        if p["id"] == patient_id:
            return p
    return None


def _find_case(patient: dict, case_id: str) -> dict | None:
    for c in patient.get("cases", []):
        if c["id"] == case_id:
            return c
    return None


# ------------------------------------------------------------------
# Patient CRUD
# ------------------------------------------------------------------

def list_patients() -> list[dict[str, Any]]:
    reg = load_registry()
    return [_serialize_patient(p) for p in reg["patients"]]


def create_patient(label: str) -> dict[str, Any]:
    reg = load_registry()
    slug = _slugify(label)

    existing_ids = {p["id"] for p in reg["patients"]}
    base = slug
    counter = 2
    while slug in existing_ids:
        slug = f"{base}-{counter}"
        counter += 1

    patient = {
        "id": slug,
        "label": label.strip(),
        "cases": [],
        "created_at": _now_iso(),
    }
    reg["patients"].append(patient)

    if reg["active_patient"] is None:
        reg["active_patient"] = slug

    save_registry(reg)
    return patient


def delete_patient(patient_id: str) -> bool:
    reg = load_registry()
    patient = _find_patient(reg, patient_id)
    if not patient:
        return False

    patient_dir = _patient_dir(patient_id)
    if patient_dir.exists():
        shutil.rmtree(patient_dir)

    reg["patients"] = [p for p in reg["patients"] if p["id"] != patient_id]

    if reg["active_patient"] == patient_id:
        reg["active_patient"] = reg["patients"][0]["id"] if reg["patients"] else None
        reg["active_case"] = None

    save_registry(reg)
    return True


# ------------------------------------------------------------------
# Case CRUD
# ------------------------------------------------------------------

def list_cases(patient_id: str) -> list[dict[str, Any]]:
    reg = load_registry()
    patient = _find_patient(reg, patient_id)
    if not patient:
        return []
    return patient.get("cases", [])


def _name_from_email(email: str) -> str:
    local = (email or "").split("@", 1)[0]
    local = local.replace(".", " ").replace("_", " ").replace("-", " ")
    parts = [part for part in local.split() if part]
    if not parts:
        return (email or "").strip() or "New profile"
    return " ".join(part[:1].upper() + part[1:] for part in parts)


def find_owned_patient(username: str) -> dict[str, Any] | None:
    """Patient profile that belongs to this sign-in, if one has been created."""
    needle = (username or "").strip().lower()
    if not needle:
        return None
    reg = load_registry()
    for patient in reg.get("patients") or []:
        if str(patient.get("owner") or "").strip().lower() == needle:
            return patient
    return None


def ensure_owned_patient(username: str) -> dict[str, Any]:
    """Return this sign-in's own profile, creating it and a default case if needed."""
    email = (username or "").strip()
    patient = find_owned_patient(email)
    if patient is None:
        created = create_patient(_name_from_email(email))
        reg = load_registry()
        patient = _find_patient(reg, created["id"])
        if patient is None:
            raise ValueError("Could not create a profile")
        patient["owner"] = email
        save_registry(reg)
    if not patient.get("cases"):
        created_case = create_case(patient["id"], "Ongoing Health")
        if created_case is None:
            raise ValueError("Could not create the Ongoing Health case")
        reg = load_registry()
        patient = _find_patient(reg, patient["id"]) or patient
    return patient


def create_case(patient_id: str, label: str) -> dict[str, Any] | None:
    reg = load_registry()
    patient = _find_patient(reg, patient_id)
    if not patient:
        return None

    slug = _slugify(label)
    existing_ids = {c["id"] for c in patient.get("cases", [])}
    base = slug
    counter = 2
    while slug in existing_ids:
        slug = f"{base}-{counter}"
        counter += 1

    case = {
        "id": slug,
        "label": label.strip(),
        "created_at": _now_iso(),
    }
    patient.setdefault("cases", []).append(case)

    case_dir = _case_dir(patient_id, slug)
    (case_dir / "documents").mkdir(parents=True, exist_ok=True)
    (case_dir / "extracted").mkdir(parents=True, exist_ok=True)

    if reg["active_patient"] == patient_id and reg.get("active_case") is None:
        reg["active_case"] = slug

    save_registry(reg)
    return case


def rename_case(patient_id: str, case_id: str, label: str) -> dict[str, Any] | None:
    """Update a case display label. Keeps case id / directory unchanged."""
    cleaned = (label or "").strip()
    if not cleaned:
        raise ValueError("Case label is required")

    reg = load_registry()
    patient = _find_patient(reg, patient_id)
    if not patient:
        return None
    case = _find_case(patient, case_id)
    if not case:
        return None

    case["label"] = cleaned
    save_registry(reg)
    return case


def delete_case(patient_id: str, case_id: str) -> bool:
    reg = load_registry()
    patient = _find_patient(reg, patient_id)
    if not patient:
        return False

    case = _find_case(patient, case_id)
    if not case:
        return False

    case_dir = _case_dir(patient_id, case_id)
    if case_dir.exists():
        shutil.rmtree(case_dir)

    patient["cases"] = [c for c in patient["cases"] if c["id"] != case_id]

    if reg["active_patient"] == patient_id and reg["active_case"] == case_id:
        reg["active_case"] = patient["cases"][0]["id"] if patient["cases"] else None

    save_registry(reg)
    return True


# ------------------------------------------------------------------
# Activation
# ------------------------------------------------------------------

def _selection_ids(reg: dict[str, Any]) -> tuple[str | None, str | None]:
    """Active patient/case for this request.

    Signed-in users each have their own selection. Local mode (auth off) keeps
    using the shared registry default.
    """
    from app.services.user_context import (
        current_actor,
        get_saved_selection,
        pinned_scope,
        save_selection,
    )

    pinned = pinned_scope()
    if pinned:
        return pinned
    actor = current_actor()
    if not actor:
        return reg.get("active_patient"), reg.get("active_case")
    saved = get_saved_selection(actor)
    if saved:
        return saved
    pid = reg.get("active_patient")
    cid = reg.get("active_case")
    if pid and cid:
        from app.services.profile_access import can_access_patient

        if can_access_patient(actor, pid):
            save_selection(actor, pid, cid)
            return pid, cid
    return None, None


def get_active_context() -> dict[str, Any]:
    """Return active patient_id, case_id, and their labels for this session."""
    reg = load_registry()
    patient_id, case_id = _selection_ids(reg)
    patient = _find_patient(reg, patient_id) if patient_id else None
    case = _find_case(patient, case_id) if patient and case_id else None
    if patient and case_id and not case:
        cases = patient.get("cases") or []
        case = cases[0] if cases else None
        case_id = case["id"] if case else None
    return {
        "patient_id": patient["id"] if patient else None,
        "patient_label": patient["label"] if patient else None,
        "case_id": case["id"] if case else None,
        "case_label": case["label"] if case else None,
        "has_photo": find_patient_photo(patient_id) is not None if patient_id else False,
        "photo_url": _photo_url(patient_id) if patient_id else None,
        "cases": patient.get("cases", []) if patient else [],
        "profile": get_patient_profile(patient_id) if patient_id else _empty_profile(),
    }


def activate_patient_case(patient_id: str, case_id: str) -> bool:
    reg = load_registry()
    patient = _find_patient(reg, patient_id)
    if not patient:
        return False
    case = _find_case(patient, case_id)
    if not case:
        return False
    from app.services.user_context import current_actor, save_selection

    actor = current_actor()
    if actor:
        save_selection(actor, patient_id, case_id)
        return True
    reg["active_patient"] = patient_id
    reg["active_case"] = case_id
    save_registry(reg)
    return True


# ------------------------------------------------------------------
# Active paths — used by config.Settings
# ------------------------------------------------------------------

def active_case_dir() -> Path | None:
    """Return path for this session's active case, or None if nothing is active."""
    reg = load_registry()
    pid, cid = _selection_ids(reg)
    if pid and cid and _find_case(_find_patient(reg, pid), cid):
        return _case_dir(pid, cid)
    return None


# ------------------------------------------------------------------
# Cross-case access (documents from sibling cases of same patient)
# ------------------------------------------------------------------

def sibling_case_dirs(patient_id: str, exclude_case_id: str) -> list[dict[str, Any]]:
    """Return list of {id, label, dir} for other cases of the same patient."""
    reg = load_registry()
    patient = _find_patient(reg, patient_id)
    if not patient:
        return []
    result = []
    for c in patient.get("cases", []):
        if c["id"] != exclude_case_id:
            result.append({
                "id": c["id"],
                "label": c["label"],
                "dir": str(_case_dir(patient_id, c["id"])),
            })
    return result


# ------------------------------------------------------------------
# Legacy migration
# ------------------------------------------------------------------

def migrate_legacy_if_needed() -> None:
    """If data/ has a flat beatit.db, move everything into patients/default/cases/default/."""
    legacy_db = settings.data_dir / "beatit.db"
    registry = _registry_path()

    if registry.exists() or not legacy_db.exists():
        return

    patient_id = "susan-brajtman"
    case_id = "pancreatic-cancer"
    case_dir = _case_dir(patient_id, case_id)
    case_dir.mkdir(parents=True, exist_ok=True)

    legacy_db.rename(case_dir / "beatit.db")

    legacy_docs = settings.data_dir / "documents"
    if legacy_docs.exists():
        legacy_docs.rename(case_dir / "documents")

    legacy_ext = settings.data_dir / "extracted"
    if legacy_ext.exists():
        legacy_ext.rename(case_dir / "extracted")

    reg = _default_registry()
    reg["active_patient"] = patient_id
    reg["active_case"] = case_id
    reg["patients"] = [
        {
            "id": patient_id,
            "label": "Susan Brajtman",
            "cases": [{"id": case_id, "label": "Pancreatic Cancer", "created_at": _now_iso()}],
            "created_at": _now_iso(),
        }
    ]
    save_registry(reg)
