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
- If one collection date applies to the whole panel, use it for every row.
- Do not invent values. Skip rows without a numeric result.
- Prefer standard names when clear (LDL → "LDL cholesterol", HDL → "HDL cholesterol", non-HDL → "Non-HDL cholesterol").
- Include ratios and scores (e.g. Cholesterol/HDL ratio, coronary calcium) when present.
- If the document is empty or not a lab report, return [].

Known preferred names (use when matching):
{preset_names}

Text:
---
{text}
---
"""

_NAME_ALIASES = {
    "ldl": "LDL cholesterol",
    "ldl-c": "LDL cholesterol",
    "ldl cholesterol": "LDL cholesterol",
    "hdl": "HDL cholesterol",
    "hdl-c": "HDL cholesterol",
    "hdl cholesterol": "HDL cholesterol",
    "non-hdl": "Non-HDL cholesterol",
    "non hdl": "Non-HDL cholesterol",
    "non-hdl cholesterol": "Non-HDL cholesterol",
    "total cholesterol": "Total cholesterol",
    "cholesterol": "Total cholesterol",
    "triglycerides": "Triglyceride",
    "triglyceride": "Triglyceride",
    "trig": "Triglyceride",
    "chol/hdl": "Cholesterol/HDL ratio",
    "cholesterol/hdl": "Cholesterol/HDL ratio",
    "cholesterol/hdl ratio": "Cholesterol/HDL ratio",
    "tc/hdl": "Cholesterol/HDL ratio",
    "hba1c": "HbA1c",
    "a1c": "HbA1c",
    "glucose": "Glucose fasting",
    "fasting glucose": "Glucose fasting",
    "glucose fasting": "Glucose fasting",
    "creatinine": "Creatinine",
    "egfr": "eGFR",
    "alt": "ALT",
    "ast": "AST",
    "bilirubin": "Bilirubin total",
    "total bilirubin": "Bilirubin total",
    "bilirubin total": "Bilirubin total",
    "hemoglobin": "Hemoglobin",
    "hgb": "Hemoglobin",
    "hb": "Hemoglobin",
    "platelets": "Platelets",
    "plt": "Platelets",
    "crp": "CRP",
    "tsh": "TSH",
    "vitamin d": "Vitamin D 25-OH",
    "vit d": "Vitamin D 25-OH",
    "25-oh vitamin d": "Vitamin D 25-OH",
    "vitamin d 25-oh": "Vitamin D 25-OH",
    "vitamin b12": "Vitamin B12",
    "b12": "Vitamin B12",
    "ferritin": "Ferritin",
    "ca19-9": "CA19-9",
    "cea": "CEA",
    "coronary calcium": "Coronary calcium score",
    "calcium score": "Coronary calcium score",
    "agatston": "Coronary calcium score",
    "coronary calcium score": "Coronary calcium score",
}


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


def normalize_diagnostic_name(name: str) -> str:
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return ""
    alias = _NAME_ALIASES.get(cleaned.lower())
    if alias:
        return alias
    for preset in DIAGNOSTIC_PRESETS:
        if preset["name"].lower() == cleaned.lower():
            return preset["name"]
    # Fuzzy contains: "LDL Cholesterol (calculated)" → LDL cholesterol
    lower = cleaned.lower()
    for key, alias_name in _NAME_ALIASES.items():
        if key in lower and len(key) >= 3:
            return alias_name
    for preset in DIAGNOSTIC_PRESETS:
        pname = preset["name"].lower()
        if pname in lower or lower in pname:
            return preset["name"]
    return cleaned[:120]


def clamp_proposed_diagnostic(raw: Any) -> dict[str, Any] | None:
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
        try:
            recorded_at = _normalize_med_date(str(recorded_raw))
        except ValueError:
            recorded_at = None
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


def parse_diagnostics_json(raw: str) -> tuple[list[dict[str, Any]], list[str]]:
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
        clamped = clamp_proposed_diagnostic(item)
        if clamped is None:
            skipped += 1
            continue
        proposed.append(clamped)
    if skipped:
        warnings.append(f"Skipped {skipped} invalid row(s)")
    return proposed, warnings


def soft_overlap_warnings(
    proposed: list[dict[str, Any]],
    patient_id: str,
) -> list[str]:
    profile = get_patient_profile(patient_id)
    existing = {
        (
            str(d.get("name") or "").strip().lower(),
            str(d.get("recorded_at") or "")[:10],
        )
        for d in (profile.get("diagnostics") or [])
    }
    overlaps = [
        f"{p['name']} ({p.get('recorded_at') or 'no date'})"
        for p in proposed
        if (
            p["name"].strip().lower(),
            str(p.get("recorded_at") or "")[:10],
        )
        in existing
    ]
    if not overlaps:
        return []
    sample = ", ".join(overlaps[:5])
    more = f" (+{len(overlaps) - 5} more)" if len(overlaps) > 5 else ""
    return [f"Already on profile for same date: {sample}{more}"]


def _preset_names_for_prompt() -> str:
    return ", ".join(p["name"] for p in DIAGNOSTIC_PRESETS)


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

    clipped = text if len(text) <= 28000 else text[:28000] + "\n…[truncated]"
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

    proposed, parse_warnings = parse_diagnostics_json(raw)
    warnings.extend(parse_warnings)
    warnings.extend(soft_overlap_warnings(proposed, patient_id))
    missing_dates = sum(1 for p in proposed if not p.get("recorded_at"))
    if missing_dates:
        warnings.append(
            f"{missing_dates} reading(s) missing a collection date — set dates before confirming"
        )
    if not proposed:
        warnings.append("No lab readings detected in the document")

    return {
        "proposed": proposed,
        "extraction_meta": meta or {},
        "warnings": warnings,
        "extracted_preview": clipped[:800],
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
    }
    doc_meta = doc.get("metadata") or {}
    if isinstance(doc_meta, dict):
        meta["extraction_method"] = doc_meta.get("extraction_method")
        meta["extracted_chars"] = doc_meta.get("extracted_chars")

    if is_empty_med_extract(text):
        file_path = doc.get("file_path")
        if not file_path or not Path(file_path).is_file():
            raise ValueError(
                "This document has no extractable text. Re-extract / OCR it in Library first."
            )
        content = await asyncio.to_thread(Path(file_path).read_bytes)
        filename = Path(file_path).name
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

    return await _propose_from_text(patient_id, text, meta=meta, llm=llm)


def _existing_name_date_keys(patient_id: str) -> set[tuple[str, str]]:
    profile = get_patient_profile(patient_id)
    return {
        (
            str(d.get("name") or "").strip().lower(),
            str(d.get("recorded_at") or "")[:10],
        )
        for d in (profile.get("diagnostics") or [])
    }


async def auto_confirm_lab_readings_from_document(
    patient_id: str,
    doc: dict[str, Any],
    *,
    extracted_text: str | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Propose lab rows and auto-add complete, non-duplicate readings.

    Only rows with name + value + recorded_at are confirmed. Exact name+date
    duplicates are skipped. Manual Import to Labs remains the fallback.
    """
    proposal = await propose_diagnostics_from_document(
        patient_id,
        doc,
        extracted_text=extracted_text,
        llm=llm,
    )
    proposed = proposal.get("proposed") or []
    existing = _existing_name_date_keys(patient_id)
    doc_id = str(doc.get("id") or "").strip() or None
    added: list[dict[str, Any]] = []
    skipped_duplicate = 0
    skipped_incomplete = 0
    errors: list[str] = []

    for raw in proposed:
        name = str(raw.get("name") or "").strip()
        recorded_at = str(raw.get("recorded_at") or "").strip()[:10]
        if not name or raw.get("value") is None or not recorded_at:
            skipped_incomplete += 1
            continue
        key = (name.lower(), recorded_at)
        if key in existing:
            skipped_duplicate += 1
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
            existing.add(key)

    profile = get_patient_profile(patient_id)
    warnings = list(proposal.get("warnings") or [])
    if skipped_duplicate:
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
        "document_id": doc_id,
        "document_title": doc.get("title"),
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
    }
