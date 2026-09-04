"""Classify clinical report PDFs (labs, MRI, ultrasound, …) from title + text."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.llm import LLMClient

CLINICAL_REPORT_KINDS = (
    "lab",
    "mri",
    "ultrasound",
    "ct",
    "pathology",
    "cardiology",
    "other_report",
    "unknown",
)

CLINICAL_REPORT_KIND_LABELS: dict[str, str] = {
    "lab": "Lab report",
    "mri": "MRI report",
    "ultrasound": "Ultrasound report",
    "ct": "CT report",
    "pathology": "Pathology report",
    "cardiology": "Cardiology report",
    "other_report": "Clinical report",
    "unknown": "Unclassified",
}

# Kinds that should cite as Diag (same bucket as DICOM imaging).
DIAGNOSTIC_CITATION_KINDS = frozenset(
    {
        "lab",
        "mri",
        "ultrasound",
        "ct",
        "pathology",
        "cardiology",
        "other_report",
    }
)

_HEURISTIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "lab",
        (
            "lab ",
            "labs ",
            "laboratory",
            "bloodwork",
            "blood work",
            "lipid",
            "cholesterol",
            "hba1c",
            "cbc",
            "metabolic panel",
            "chemistry panel",
            "specimen",
            "collection date",
            "reference range",
            "mmol/l",
            "mg/dl",
        ),
    ),
    (
        "mri",
        ("mri", "magnetic resonance", "mr imaging", "mr abdomen", "mr pelvis"),
    ),
    (
        "ultrasound",
        ("ultrasound", "sonograph", "sonogram", "doppler us", " us "),
    ),
    (
        "ct",
        (" ct ", "ct scan", "ct chest", "ct abdomen", "computed tomography", "cat scan"),
    ),
    (
        "pathology",
        ("pathology", "histology", "biopsy", "cytology", "surgical pathology"),
    ),
    (
        "cardiology",
        (
            "echo",
            "echocardiogram",
            "ecg",
            "ekg",
            "electrocardiogram",
            "holter",
            "stress test",
            "cardiac cath",
            "coronary calcium",
        ),
    ),
]

_CLASSIFY_SYSTEM = (
    "You classify clinical documents for a medical records library. "
    "Return ONLY a JSON object. No prose, no markdown fences."
)

_CLASSIFY_USER = """Classify this clinical document.

Return JSON:
{{"kind": "<one of: lab, mri, ultrasound, ct, pathology, cardiology, other_report, unknown>", "confidence": <0.0-1.0>}}

Rules:
- lab: laboratory / blood / chemistry / lipid / panel results with numeric assays
- mri / ultrasound / ct: imaging interpretation reports for that modality
- pathology: biopsy / histology / cytology reports
- cardiology: ECG, echo, stress test, Holter, cardiac cath (not general labs)
- other_report: other clinical diagnostic reports that are not plain notes
- unknown: letters, notes, invoices, or unclear documents
- Prefer the dominant clinical purpose of the document

Title: {title}
Filename: {filename}

Text (may be truncated):
---
{text}
---
"""


def normalize_clinical_report_kind(value: Any) -> str:
    kind = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "labs": "lab",
        "laboratory": "lab",
        "bloodwork": "lab",
        "blood_work": "lab",
        "us": "ultrasound",
        "sonography": "ultrasound",
        "sonogram": "ultrasound",
        "echo": "cardiology",
        "ecg": "cardiology",
        "ekg": "cardiology",
        "histology": "pathology",
        "biopsy": "pathology",
        "report": "other_report",
        "clinical_report": "other_report",
    }
    kind = aliases.get(kind, kind)
    if kind in CLINICAL_REPORT_KINDS:
        return kind
    return "unknown"


def clinical_report_kind_label(kind: str | None) -> str:
    return CLINICAL_REPORT_KIND_LABELS.get(
        normalize_clinical_report_kind(kind), CLINICAL_REPORT_KIND_LABELS["unknown"]
    )


def is_diagnostic_citation_kind(kind: str | None) -> bool:
    return normalize_clinical_report_kind(kind) in DIAGNOSTIC_CITATION_KINDS


def _haystack(title: str, filename: str, text: str) -> str:
    return f" {title} {filename} {text[:4000]} ".lower()


def classify_clinical_report_heuristic(
    *,
    title: str = "",
    filename: str = "",
    text: str = "",
) -> dict[str, Any]:
    hay = _haystack(title, filename, text)
    scores: dict[str, int] = {}
    for kind, needles in _HEURISTIC_RULES:
        score = 0
        for needle in needles:
            if needle in hay:
                score += 2 if needle.strip() in (title or "").lower() or needle.strip() in (
                    filename or ""
                ).lower() else 1
        if score:
            scores[kind] = score
    if not scores:
        return {
            "kind": "unknown",
            "label": clinical_report_kind_label("unknown"),
            "confidence": 0.15,
            "method": "heuristic",
        }
    best = max(scores.items(), key=lambda item: item[1])
    kind = best[0]
    confidence = min(0.92, 0.45 + 0.08 * best[1])
    # Ambiguous top scores → lower confidence
    ranked = sorted(scores.values(), reverse=True)
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        confidence = min(confidence, 0.55)
        kind = "other_report" if ranked[0] < 4 else kind
    return {
        "kind": kind,
        "label": clinical_report_kind_label(kind),
        "confidence": round(confidence, 3),
        "method": "heuristic",
    }


def _strip_json_object(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _parse_llm_kind(raw: str) -> dict[str, Any] | None:
    payload = _strip_json_object(raw)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = normalize_clinical_report_kind(data.get("kind"))
    try:
        confidence = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))
    return {
        "kind": kind,
        "label": clinical_report_kind_label(kind),
        "confidence": round(confidence, 3),
        "method": "llm",
    }


async def classify_clinical_report(
    *,
    title: str = "",
    filename: str = "",
    text: str = "",
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Return kind / label / confidence for a clinical report document."""
    heuristic = classify_clinical_report_heuristic(
        title=title, filename=filename, text=text
    )
    cleaned = (text or "").strip()
    # Strong filename/title match — skip LLM
    if heuristic["kind"] != "unknown" and heuristic["confidence"] >= 0.78:
        title_file = f" {title} {filename} ".lower()
        needles = next(
            (n for kind, n in _HEURISTIC_RULES if kind == heuristic["kind"]),
            (),
        )
        if any(n.strip() and n.strip() in title_file for n in needles):
            return heuristic

    if not cleaned or len(cleaned) < 40:
        return heuristic

    client = llm or LLMClient()
    clipped = cleaned if len(cleaned) <= 6000 else cleaned[:6000] + "\n…[truncated]"
    try:
        raw = await client.chat(
            messages=[
                {"role": "system", "content": _CLASSIFY_SYSTEM},
                {
                    "role": "user",
                    "content": _CLASSIFY_USER.format(
                        title=(title or "").strip() or "(none)",
                        filename=(filename or "").strip() or "(none)",
                        text=clipped,
                    ),
                },
            ],
            temperature=0.0,
        )
    except Exception:
        return heuristic

    llm_result = _parse_llm_kind(raw)
    if not llm_result:
        return heuristic

    # Prefer LLM when heuristic is weak/unknown; blend when both agree
    if heuristic["kind"] == "unknown":
        return llm_result
    if llm_result["kind"] == heuristic["kind"]:
        return {
            **llm_result,
            "confidence": round(
                min(0.97, max(heuristic["confidence"], llm_result["confidence"]) + 0.05),
                3,
            ),
            "method": "heuristic+llm",
        }
    if llm_result["confidence"] >= 0.65:
        return llm_result
    return heuristic


def apply_classification_to_metadata(
    metadata: dict[str, Any] | None,
    classification: dict[str, Any],
) -> dict[str, Any]:
    meta = dict(metadata or {})
    kind = normalize_clinical_report_kind(classification.get("kind"))
    meta["clinical_report_kind"] = kind
    meta["clinical_report_kind_label"] = clinical_report_kind_label(kind)
    meta["clinical_report_confidence"] = classification.get("confidence")
    meta["clinical_report_method"] = classification.get("method")
    return meta


async def classify_and_update_document(
    store: Any,
    doc: dict[str, Any],
    *,
    extracted_text: str | None = None,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    """Classify a document and persist clinical_report_* metadata fields."""
    meta = dict(doc.get("metadata") or {})
    title = str(doc.get("title") or "")
    filename = str(meta.get("original_filename") or "")
    text = (extracted_text or "").strip()
    if not text and hasattr(store, "read_extracted_text"):
        text = (await store.read_extracted_text(doc) or "").strip()

    classification = await classify_clinical_report(
        title=title,
        filename=filename,
        text=text,
        llm=llm,
    )
    updated_meta = apply_classification_to_metadata(meta, classification)
    updated = await store.db.update_document_metadata(doc["id"], metadata=updated_meta)
    return updated or {**doc, "metadata": updated_meta}
