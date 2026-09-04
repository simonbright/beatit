"""Parse a medications list from extracted PDF/image text via the LLM."""

from __future__ import annotations

import json
import re
from typing import Any

from app.ingest.pdf import (
    extract_med_list_text,
    is_empty_med_extract,
)
from app.services.case_manager import (
    _normalize_conditions,
    _normalize_med_date,
    get_patient_profile,
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


async def propose_medications_from_upload(
    patient_id: str,
    content: bytes,
    *,
    content_type: str | None = None,
    filename: str | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    text, meta = extract_med_list_text(
        content, content_type=content_type, filename=filename
    )
    warnings: list[str] = []
    if is_empty_med_extract(text):
        hint = meta.get("ocr_hint")
        msg = "No readable text found in the upload."
        if hint:
            msg = f"{msg} {hint}"
        raise ValueError(msg)

    # Cap prompt size for large OCR dumps
    clipped = text if len(text) <= 24000 else text[:24000] + "\n…[truncated]"
    client = llm or LLMClient()
    try:
        raw = await client.chat(
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

    proposed, parse_warnings = parse_medications_json(raw)
    warnings.extend(parse_warnings)
    warnings.extend(soft_dedupe_warnings(proposed, patient_id))
    if not proposed:
        warnings.append("No medications detected in the document")

    return {
        "proposed": proposed,
        "extraction_meta": meta,
        "warnings": warnings,
        "extracted_preview": clipped[:800],
    }
