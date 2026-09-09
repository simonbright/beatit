"""Parse a medications list from extracted PDF/image text via the LLM."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from app.ingest.pdf import (
    _image_data_uri_mime,
    extract_med_list_text_async,
    is_empty_med_extract,
    sniff_med_import_kind,
    validate_med_import_upload,
)
from app.services.case_manager import (
    _normalize_conditions,
    _normalize_med_date,
    get_active_context,
    get_patient_profile,
    list_cases,
    _case_dir,
)
from app.services.llm import LLMClient

MED_IMPORT_SYSTEM = (
    "You extract prescription medications from clinical documents and photos of med lists. "
    "Return ONLY a JSON array. No prose, no markdown fences."
)

MED_IMPORT_USER_TEMPLATE = """Extract every medication from the text below.

Return a JSON array. Each object may include:
- name (required string)
- dosage (string or null, e.g. "20 mg")
- frequency (string or null, e.g. "once daily")
- conditions (array of short strings, or null)
- notes (string or null)
- started_at (YYYY-MM-DD or null)
- ended_at (YYYY-MM-DD or null; when the medication was stopped or the course ended)

Rules:
- Only medications the patient takes or is prescribed. Skip vitamins only if clearly not listed as meds.
- Do not invent medications. If unsure of a field, use null.
- Prefer brand or generic name as written.
- If the document is empty or not a med list, return [].

Text:
---
{text}
---
"""

MED_IMPORT_VISION_SYSTEM = (
    "You read medication lists, prescription bottles, blister packs, and pharmacy labels "
    "from photos. Return ONLY a JSON array of medications. No prose, no markdown fences."
)

MED_IMPORT_VISION_USER = (
    "Extract every medication visible in this photo. "
    "Return a JSON array. Each object may include: "
    "name (required), dosage, frequency, conditions (array or null), notes, "
    "started_at (YYYY-MM-DD or null), ended_at (YYYY-MM-DD or null). "
    "Do not invent medications. If nothing readable, return []."
)


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


def clamp_proposed_medication(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _clamp_str(raw.get("name"), 120)
    if not name:
        return None
    started_raw = raw.get("started_at")
    started_at = None
    if started_raw not in (None, ""):
        try:
            started_at = _normalize_med_date(str(started_raw))
        except ValueError:
            started_at = None
    ended_raw = raw.get("ended_at")
    if ended_raw in (None, ""):
        ended_raw = raw.get("stopped_at")
    ended_at = None
    if ended_raw not in (None, ""):
        try:
            ended_at = _normalize_med_date(str(ended_raw))
        except ValueError:
            ended_at = None
    if started_at and ended_at and ended_at < started_at:
        ended_at = None
    conditions = _normalize_conditions(raw.get("conditions"))
    return {
        "name": name,
        "dosage": _clamp_str(raw.get("dosage"), 80),
        "frequency": _clamp_str(raw.get("frequency"), 80),
        "conditions": conditions,
        "notes": _clamp_str(raw.get("notes"), 500),
        "started_at": started_at,
        "ended_at": ended_at,
    }


def parse_medications_json(raw: str) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    payload = _strip_json_payload(raw)
    if not payload:
        return [], ["Model returned empty response"]
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return [], ["Could not parse medications JSON from model response"]
    if not isinstance(data, list):
        return [], ["Model response was not a JSON array"]
    proposed: list[dict[str, Any]] = []
    skipped = 0
    for item in data:
        clamped = clamp_proposed_medication(item)
        if clamped is None:
            skipped += 1
            continue
        proposed.append(clamped)
    if skipped:
        warnings.append(f"Skipped {skipped} invalid row(s)")
    return proposed, warnings


def soft_dedupe_warnings(
    proposed: list[dict[str, Any]],
    patient_id: str,
) -> list[str]:
    profile = get_patient_profile(patient_id)
    active_names = {
        str(m.get("name") or "").strip().lower()
        for m in (profile.get("medications") or [])
        if (m.get("status") or "active") == "active"
    }
    overlaps = [
        p["name"]
        for p in proposed
        if p["name"].strip().lower() in active_names
    ]
    if not overlaps:
        return []
    sample = ", ".join(overlaps[:5])
    more = f" (+{len(overlaps) - 5} more)" if len(overlaps) > 5 else ""
    return [f"Already on active list: {sample}{more}"]


async def _propose_from_image_vision(
    content: bytes,
    *,
    filename: str | None,
    llm: LLMClient | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Ask a vision model to read medications directly from the photo."""
    from app.config import settings
    from app.ingest.pdf import _vision_ocr_model
    from app.services.openrouter_client import OpenRouterClient

    meta: dict[str, Any] = {
        "source_kind": "image",
        "extraction_method": "vision_med_parse",
        "page_count": 1,
    }
    if not settings.openrouter_api_key:
        return [], ["Vision API not configured"], meta
    if len(content) > 12 * 1024 * 1024:
        return [], ["Image too large for vision parse"], meta

    model = _vision_ocr_model()
    client = OpenRouterClient(model=model)
    meta["vision_model"] = model
    mime = _image_data_uri_mime(content, filename)
    b64 = base64.b64encode(content).decode("ascii")
    try:
        raw = await client.chat(
            messages=[
                {"role": "system", "content": MED_IMPORT_VISION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": MED_IMPORT_VISION_USER},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                },
            ],
            temperature=0.1,
        )
    except Exception as exc:
        meta["vision_error"] = str(exc)
        return [], [f"Vision parse failed: {exc}"], meta

    proposed, warnings = parse_medications_json(raw)
    meta["extracted_chars"] = len(raw or "")
    meta["vision_med_count"] = len(proposed)
    return proposed, warnings, meta


async def _propose_from_extracted_text(
    text: str,
    *,
    llm: LLMClient,
) -> tuple[list[dict[str, Any]], list[str]]:
    clipped = text if len(text) <= 24000 else text[:24000] + "\n…[truncated]"
    try:
        raw = await llm.chat(
            messages=[
                {"role": "system", "content": MED_IMPORT_SYSTEM},
                {
                    "role": "user",
                    "content": MED_IMPORT_USER_TEMPLATE.format(text=clipped),
                },
            ],
            temperature=0.1,
        )
    except Exception as exc:
        raise ValueError(f"Could not parse medications with LLM: {exc}") from exc
    return parse_medications_json(raw)


def _store_for_patient(patient_id: str):
    """Open DocumentStore for the patient's active case (or first case)."""
    from app.storage.database import Database
    from app.storage.documents import DocumentStore

    ctx = get_active_context()
    case_id = None
    if ctx.get("patient_id") == patient_id and ctx.get("case_id"):
        case_id = ctx["case_id"]
    if not case_id:
        cases = list_cases(patient_id)
        if not cases:
            return None
        case_id = cases[0]["id"]
    db_path = _case_dir(patient_id, case_id) / "beatit.db"
    if not db_path.exists():
        return None
    db = Database(db_path=db_path)
    return DocumentStore(db)


async def _persist_med_import_document(
    patient_id: str,
    content: bytes,
    *,
    filename: str | None,
    content_type: str | None,
    extracted_text: str,
    extraction_meta: dict[str, Any],
) -> dict[str, Any] | None:
    store = _store_for_patient(patient_id)
    if store is None:
        return None
    kind = sniff_med_import_kind(
        content, content_type=content_type, filename=filename
    )
    title = (filename or "").strip() or (
        "Medication list photo" if kind == "image" else "Medication list"
    )
    if title.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf")):
        display = title.rsplit(".", 1)[0].strip() or title
    else:
        display = title
    meta = dict(extraction_meta or {})
    meta["original_filename"] = filename or title
    meta["source_purpose"] = "medication_import"
    meta["clinical_report_kind"] = "other_report"
    meta["clinical_report_kind_label"] = "Medication list"
    meta["clinical_report_method"] = "med_import"
    meta["clinical_report_confidence"] = 0.9
    source_type = "pdf" if kind == "pdf" else "image"
    doc = await store.create_document(
        title=display[:200],
        source_type=source_type,
        extracted_text=extracted_text or None,
        raw_filename=filename or ("med-list.jpg" if kind == "image" else "med-list.pdf"),
        raw_content=content,
        metadata=meta,
    )
    return doc


async def propose_medications_from_upload(
    patient_id: str,
    content: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
    llm: LLMClient | None = None,
    persist_document: bool = True,
) -> dict[str, Any]:
    kind = validate_med_import_upload(
        content, content_type=content_type, filename=filename
    )
    client = llm or LLMClient()
    warnings: list[str] = []
    proposed: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"source_kind": kind}
    extracted_preview = ""
    text = ""

    if kind == "image":
        vision_proposed, vision_warnings, vision_meta = await _propose_from_image_vision(
            content, filename=filename
        )
        meta.update(vision_meta)
        if vision_proposed:
            proposed = vision_proposed
            warnings.extend(vision_warnings)
            extracted_preview = f"[Vision read {len(proposed)} medication(s) from photo]"
            text = extracted_preview
        else:
            warnings.extend([w for w in vision_warnings if "Vision" in w or "API" in w])

    if not proposed:
        text, ocr_meta = await extract_med_list_text_async(
            content, content_type=content_type, filename=filename
        )
        meta.update(ocr_meta)
        if is_empty_med_extract(text):
            hint = meta.get("ocr_hint")
            msg = "No readable text found in the upload."
            if hint:
                msg = f"{msg} {hint}"
            if not proposed:
                raise ValueError(msg)
        else:
            text_proposed, parse_warnings = await _propose_from_extracted_text(
                text, llm=client
            )
            proposed = text_proposed
            warnings.extend(parse_warnings)
            clipped = text if len(text) <= 800 else text[:800] + "…"
            extracted_preview = clipped

    warnings.extend(soft_dedupe_warnings(proposed, patient_id))
    if not proposed:
        warnings.append("No medications detected in the document")

    document = None
    if persist_document:
        try:
            document = await _persist_med_import_document(
                patient_id,
                content,
                filename=filename,
                content_type=content_type,
                extracted_text=text if not is_empty_med_extract(text) else extracted_preview,
                extraction_meta=meta,
            )
        except Exception as exc:
            warnings.append(f"Could not store photo in Library: {exc}")

    doc_id = document.get("id") if document else None
    if doc_id:
        meta["document_id"] = doc_id

    return {
        "proposed": proposed,
        "extraction_meta": meta,
        "warnings": warnings,
        "extracted_preview": extracted_preview[:800] if extracted_preview else "",
        "document_id": doc_id,
        "document": (
            {
                "id": document.get("id"),
                "title": document.get("title"),
                "source_type": document.get("source_type"),
                "file_url": f"/api/documents/{document.get('id')}/file",
            }
            if document
            else None
        ),
    }
