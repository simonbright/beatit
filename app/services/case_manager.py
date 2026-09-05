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

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings

REGISTRY_FILENAME = "registry.json"


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
    return {
        **patient,
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


def _empty_profile() -> dict[str, Any]:
    return {
        "date_of_birth": None,
        "gender": None,
        "measurements": [],
        "diagnostics": [],
        "journal": [],
        "medications": [],
        "food_drinks": _default_food_drinks(),
        "milestones": [],
        "medication_safety": None,
    }


def get_patient_profile(patient_id: str) -> dict[str, Any]:
    path = _profile_path(patient_id)
    if not path.exists():
        return _empty_profile()
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
    if isinstance(diagnostics, list):
        profile["diagnostics"] = sorted(
            diagnostics,
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
    milestones = data.get("milestones") or []
    if isinstance(milestones, list):
        profile["milestones"] = sorted(
            [m for m in milestones if isinstance(m, dict)],
            key=lambda m: str(m.get("date") or ""),
            reverse=True,
        )
    safety = data.get("medication_safety")
    if isinstance(safety, dict):
        profile["medication_safety"] = safety
    return profile


def save_patient_profile(patient_id: str, profile: dict[str, Any]) -> dict[str, Any]:
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
        "milestones": [
            m for m in (profile.get("milestones") or []) if isinstance(m, dict)
        ],
        "medication_safety": profile.get("medication_safety") or None,
    }
    _profile_path(patient_id).write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
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
    {"name": "Creatinine", "unit": "µmol/L", "category": "blood"},
    {"name": "eGFR", "unit": "mL/min/1.73m²", "category": "blood"},
    {"name": "ALT", "unit": "U/L", "category": "blood"},
    {"name": "AST", "unit": "U/L", "category": "blood"},
    {"name": "Bilirubin total", "unit": "µmol/L", "category": "blood"},
    {"name": "Hemoglobin", "unit": "g/L", "category": "blood"},
    {"name": "Platelets", "unit": "xE9/L", "category": "blood"},
    {"name": "CRP", "unit": "mg/L", "category": "blood"},
    {"name": "TSH", "unit": "mIU/L", "category": "blood"},
    {"name": "Vitamin D 25-OH", "unit": "nmol/L", "category": "blood"},
    {"name": "Vitamin B12", "unit": "pmol/L", "category": "blood"},
    {"name": "Ferritin", "unit": "µg/L", "category": "blood"},
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
    if "calcium score" in lower or "agatston" in lower:
        return "imaging"
    if "bp" in lower or "blood pressure" in lower:
        return "vital"
    return "blood"


def add_patient_diagnostic(
    patient_id: str,
    *,
    name: str,
    value: float,
    recorded_at: str,
    unit: str | None = None,
    notes: str | None = None,
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
    if value is None:
        raise ValueError("Diagnostic value is required")
    date_raw = (recorded_at or "").strip()
    try:
        date_iso = datetime.fromisoformat(date_raw[:10]).date().isoformat()
    except ValueError as exc:
        raise ValueError("recorded_at must be YYYY-MM-DD") from exc
    profile = get_patient_profile(patient_id)
    unit_clean = (unit or "").strip() or None
    if not unit_clean:
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
    entry = {
        "id": str(uuid4()),
        "name": cleaned_name,
        "value": float(value),
        "unit": unit_clean,
        "recorded_at": date_iso,
        "category": cat,
        "notes": (notes or "").strip() or None,
        "created_at": _now_iso(),
    }
    doc_id = (source_document_id or "").strip() or None
    if doc_id:
        entry["source_document_id"] = doc_id
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


JOURNAL_KINDS = frozenset({"symptom", "feeling", "medication", "note"})

MEDICATION_CATEGORIES = frozenset({"prescription", "otc", "remedy"})

# Common non-Rx remedies offered as one-tap adds under Medications & remedies.
COMMON_REMEDIES = [
    {"name": "CBD 1 drop", "category": "remedy", "dosage": "1 drop", "frequency": "as needed"},
    {"name": "CBD 2 drops", "category": "remedy", "dosage": "2 drops", "frequency": "as needed"},
    {"name": "Magnesium", "category": "remedy", "dosage": None, "frequency": None},
    {"name": "Melatonin", "category": "remedy", "dosage": None, "frequency": None},
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
    {"label": "Feeling good", "kind": "feeling"},
    {"label": "Better", "kind": "feeling"},
    {"label": "OK / normal", "kind": "feeling"},
    {"label": "Energetic", "kind": "feeling"},
    {"label": "Pain-free", "kind": "feeling"},
    {"label": "Weak", "kind": "feeling"},
    {"label": "Headache", "kind": "symptom"},
    {"label": "Nauseous", "kind": "symptom"},
    {"label": "Dizzy", "kind": "symptom"},
    {"label": "Fatigue", "kind": "symptom"},
    {"label": "Pain", "kind": "symptom"},
    {"label": "Anxiety", "kind": "feeling"},
    {"label": "Took medication", "kind": "medication"},
    {"label": "Ate/Drank", "kind": "note"},
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
    dosage: str | None = None,
    frequency: str | None = None,
    conditions: Any = None,
    notes: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    category: str | None = None,
    show_on_log: bool | None = None,
) -> dict[str, Any] | None:
    from uuid import uuid4

    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return None
    cleaned_name = " ".join((name or "").strip().split())
    if not cleaned_name:
        raise ValueError("Medication name is required")
    start = _normalize_med_date(started_at)
    end = _normalize_med_date(ended_at)
    if start and end and end < start:
        raise ValueError("End date must be on or after the start date")
    cat = _normalize_medication_category(category)
    freq = (frequency or "").strip() or None
    profile = get_patient_profile(patient_id)
    # Reuse existing active med with same name instead of creating duplicates
    existing = next(
        (
            m
            for m in (profile.get("medications") or [])
            if _medication_name_key(m.get("name")) == _medication_name_key(cleaned_name)
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
    from app.services.medication_identity import apply_identity_fields

    apply_identity_fields(entry)
    profile.setdefault("medications", []).append(entry)
    saved = save_patient_profile(patient_id, profile)
    return next((m for m in saved["medications"] if m["id"] == entry["id"]), entry)


def update_patient_medication(
    patient_id: str,
    medication_id: str,
    *,
    name: str | None = None,
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

    if name is not None:
        cleaned = " ".join(name.strip().split())
        if not cleaned:
            raise ValueError("Medication name is required")
        med["name"] = cleaned[:120]
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


def delete_patient_medication(patient_id: str, medication_id: str) -> bool:
    reg = load_registry()
    if not _find_patient(reg, patient_id):
        return False
    profile = get_patient_profile(patient_id)
    before = len(profile.get("medications") or [])
    profile["medications"] = [
        m for m in profile.get("medications") or [] if m.get("id") != medication_id
    ]
    if len(profile["medications"]) == before:
        return False
    save_patient_profile(patient_id, profile)
    return True


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
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in (profile or {}).get("diagnostics") or []:
        name = (row.get("name") or "").strip()
        if not name or row.get("value") is None:
            continue
        key = name.lower()
        group = groups.get(key)
        category = _infer_diagnostic_category(name, row.get("category"))
        if not group:
            group = {
                "key": key,
                "name": name,
                "unit": row.get("unit"),
                "category": category,
                "readings": [],
            }
            groups[key] = group
        if row.get("unit") and not group.get("unit"):
            group["unit"] = row["unit"]
        if category == "blood":
            group["category"] = "blood"
        group["readings"].append(
            {
                "id": row.get("id"),
                "recorded_at": str(row.get("recorded_at") or "")[:10],
                "value": row.get("value"),
                "notes": row.get("notes"),
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
        series.append(
            {
                "key": group["key"],
                "name": group["name"],
                "unit": group.get("unit"),
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


def format_profile_for_prompt(patient_id: str | None, patient_label: str | None = None) -> str:
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
            for m in history[:8]:
                parts = [str(m.get("recorded_at") or "?")]
                if m.get("height_cm") is not None:
                    parts.append(f"{m['height_cm']} cm")
                if m.get("weight_kg") is not None:
                    parts.append(f"{m['weight_kg']} kg")
                hist_lines.append(" / ".join(parts))
            lines.append("Measurement history: " + "; ".join(hist_lines))
    for series in group_diagnostics_for_charts(profile)[:10]:
        latest = series.get("latest") or {}
        unit = f" {series['unit']}" if series.get("unit") else ""
        if latest.get("value") is None:
            continue
        line = f"{series['name']}: {latest['value']}{unit} ({latest.get('recorded_at') or '?'})"
        if series["point_count"] > 1:
            trend = ", ".join(
                f"{r.get('recorded_at')}: {r.get('value')}"
                for r in (series.get("readings") or [])[-5:]
            )
            line += f" · trend {trend}"
        lines.append(line)
    journal_rows = recent_journal_for_prompt(profile)
    if journal_rows:
        lines.append("Recent self-reports (last 14 days):")
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
    meds = profile.get("medications") or []
    active_meds = [m for m in meds if (m.get("status") or "active") == "active"]
    stopped_meds = [m for m in meds if (m.get("status") or "") == "stopped"]
    if active_meds:
        lines.append("Active medications:")
        for m in active_meds[:20]:
            bits = [str(m.get("name") or "")]
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
            bits = [str(m.get("name") or "")]
            if m.get("dosage"):
                bits.append(str(m["dosage"]))
            if m.get("stopped_at"):
                bits.append(f"stopped {m['stopped_at']}")
            lines.append("  · " + " · ".join(b for b in bits if b))
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

def get_active_context() -> dict[str, Any]:
    """Return active patient_id, case_id, and their labels."""
    reg = load_registry()
    patient_id = reg.get("active_patient")
    case_id = reg.get("active_case")
    patient = _find_patient(reg, patient_id) if patient_id else None
    case = _find_case(patient, case_id) if patient and case_id else None
    return {
        "patient_id": patient_id,
        "patient_label": patient["label"] if patient else None,
        "case_id": case_id,
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
    reg["active_patient"] = patient_id
    reg["active_case"] = case_id
    save_registry(reg)
    return True


# ------------------------------------------------------------------
# Active paths — used by config.Settings
# ------------------------------------------------------------------

def active_case_dir() -> Path | None:
    """Return path for the currently active case, or None if nothing is active."""
    reg = load_registry()
    pid = reg.get("active_patient")
    cid = reg.get("active_case")
    if pid and cid:
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
