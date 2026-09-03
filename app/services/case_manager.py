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
from datetime import datetime, timezone
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

def _patients_dir() -> Path:
    return settings.data_dir / "patients"


def _case_dir(patient_id: str, case_id: str) -> Path:
    return _patients_dir() / patient_id / "cases" / case_id


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
    return reg["patients"]


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

    patient_dir = _patients_dir() / patient_id
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
