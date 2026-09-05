"""Patient-wide documentation coverage / inventory report."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from app.services.clinical_report_classify import (
    CLINICAL_REPORT_KIND_LABELS,
    CLINICAL_REPORT_KINDS,
    clinical_report_kind_label,
)
from app.services.document_paths import resolve_document_file_path
from app.services.patient_documents import list_patient_documents

# Expected clinical documentation kinds for the Present/Missing checklist
COVERAGE_CHECKLIST_KINDS = (
    "lab",
    "mri",
    "ct",
    "ultrasound",
    "pathology",
    "cardiology",
    "other_report",
)

SOURCE_TYPE_LABELS: dict[str, str] = {
    "pdf": "PDF",
    "text": "Clinical note",
    "url": "Web page",
    "youtube": "YouTube",
    "facebook": "Facebook video",
    "video": "Video",
    "imaging": "Imaging (DICOM)",
    "chat_observation": "Chat observation",
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _display_name(doc: dict[str, Any]) -> str:
    return (
        str(doc.get("citation_display_name") or "").strip()
        or str(doc.get("title") or "").strip()
        or "Untitled"
    )


def _imaging_modality_kind(meta: dict[str, Any]) -> str | None:
    """Map DICOM modality hints to clinical kind when possible."""
    bits = " ".join(
        str(meta.get(k) or "")
        for k in (
            "modality",
            "dicom_modality",
            "dicom_study_description",
            "dicom_series_description",
            "dicom_protocol_name",
        )
    ).lower()
    if not bits.strip():
        return None
    if "mr" in bits.split() or "mri" in bits or "magnetic resonance" in bits:
        return "mri"
    if "ct" in bits.split() or "computed tomography" in bits:
        return "ct"
    if "us" in bits.split() or "ultrasound" in bits or "sonograph" in bits:
        return "ultrasound"
    return None


def _coverage_kinds_for_doc(doc: dict[str, Any]) -> list[str]:
    """Clinical kinds this document contributes to (for checklist Present)."""
    meta = doc.get("metadata") or {}
    source = str(doc.get("source_type") or "").lower()
    kinds: list[str] = []
    kind = str(meta.get("clinical_report_kind") or "").strip().lower()
    if kind and kind in CLINICAL_REPORT_KINDS and kind != "unknown":
        kinds.append(kind)
    if source == "imaging":
        modality_kind = _imaging_modality_kind(meta if isinstance(meta, dict) else {})
        if modality_kind and modality_kind not in kinds:
            kinds.append(modality_kind)
    return kinds


def _inventory_group_key(doc: dict[str, Any]) -> tuple[str, str]:
    """(group_id, group_label) for By type inventory."""
    meta = doc.get("metadata") or {}
    source = str(doc.get("source_type") or "unknown").lower()
    kind = str(meta.get("clinical_report_kind") or "").strip().lower()
    if kind and kind != "unknown":
        return f"kind:{kind}", clinical_report_kind_label(kind)
    if source == "imaging":
        modality_kind = _imaging_modality_kind(meta if isinstance(meta, dict) else {})
        if modality_kind:
            return f"kind:{modality_kind}", clinical_report_kind_label(modality_kind)
        return "imaging", SOURCE_TYPE_LABELS["imaging"]
    return source or "unknown", SOURCE_TYPE_LABELS.get(source, source.upper() or "Unknown")


def _month_key(iso: str | None) -> tuple[str, str]:
    dt = _parse_iso(iso)
    if not dt:
        return "unknown", "Unknown date"
    key = f"{dt.year:04d}-{dt.month:02d}"
    label = dt.strftime("%B %Y")
    return key, label


def _doc_row(doc: dict[str, Any]) -> dict[str, Any]:
    meta = doc.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    source = str(doc.get("source_type") or "unknown").lower()
    kind = str(meta.get("clinical_report_kind") or "").strip().lower() or None
    handling = str(meta.get("handling_status") or "").strip().lower()
    needs_ocr = bool(meta.get("needs_ocr")) or str(meta.get("extraction_method") or "") == "empty"
    file_missing = False
    if source in {"pdf", "imaging", "video"} or doc.get("file_path"):
        file_missing = resolve_document_file_path(doc) is None and bool(doc.get("file_path"))
        # Also missing when path expected for PDF but heal finds nothing
        if source == "pdf" and resolve_document_file_path(doc) is None:
            file_missing = True
    group_id, group_label = _inventory_group_key(doc)
    month_id, month_label = _month_key(doc.get("created_at"))
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "display_name": _display_name(doc),
        "source_type": source,
        "source_type_label": SOURCE_TYPE_LABELS.get(source, source.upper() or "Unknown"),
        "clinical_report_kind": kind,
        "clinical_report_kind_label": clinical_report_kind_label(kind) if kind else None,
        "case_id": doc.get("case_id"),
        "case_label": doc.get("case_label"),
        "is_active_case": bool(doc.get("is_active_case")),
        "created_at": doc.get("created_at"),
        "needs_ocr": needs_ocr,
        "flagged": handling == "flagged",
        "file_missing": file_missing,
        "unclassified_pdf": source == "pdf" and (not kind or kind == "unknown"),
        "group_id": group_id,
        "group_label": group_label,
        "month_id": month_id,
        "month_label": month_label,
    }


async def build_document_coverage(patient_id: str) -> dict[str, Any]:
    """Aggregate patient-wide documentation coverage for UI + PDF."""
    docs = await list_patient_documents(patient_id)
    rows = [_doc_row(d) for d in docs]

    present_kinds: set[str] = set()
    for doc in docs:
        for k in _coverage_kinds_for_doc(doc):
            present_kinds.add(k)

    has_imaging = any(str(d.get("source_type") or "").lower() == "imaging" for d in docs)
    checklist: list[dict[str, Any]] = []
    for kind in COVERAGE_CHECKLIST_KINDS:
        present = kind in present_kinds
        count = sum(1 for r in rows if r.get("clinical_report_kind") == kind)
        if kind in {"mri", "ct", "ultrasound"}:
            # Include modality-mapped imaging in counts when kind matches group
            count = sum(
                1
                for r in rows
                if r.get("group_id") == f"kind:{kind}"
            )
        checklist.append(
            {
                "id": kind,
                "label": CLINICAL_REPORT_KIND_LABELS.get(kind, kind),
                "present": present,
                "count": count,
            }
        )
    checklist.append(
        {
            "id": "imaging",
            "label": "Imaging (DICOM)",
            "present": has_imaging,
            "count": sum(1 for r in rows if r.get("source_type") == "imaging"),
        }
    )

    counts_by_type: dict[str, int] = defaultdict(int)
    counts_by_kind: dict[str, int] = defaultdict(int)
    for r in rows:
        counts_by_type[r["source_type"]] += 1
        if r.get("clinical_report_kind") and r["clinical_report_kind"] != "unknown":
            counts_by_kind[r["clinical_report_kind"]] += 1

    attention = {
        "needs_ocr": [r for r in rows if r["needs_ocr"]],
        "flagged": [r for r in rows if r["flagged"]],
        "file_missing": [r for r in rows if r["file_missing"]],
        "unclassified_pdf": [r for r in rows if r["unclassified_pdf"]],
    }

    by_type_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_month_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    month_labels: dict[str, str] = {}
    group_labels: dict[str, str] = {}
    for r in rows:
        by_type_map[r["group_id"]].append(r)
        group_labels[r["group_id"]] = r["group_label"]
        by_month_map[r["month_id"]].append(r)
        month_labels[r["month_id"]] = r["month_label"]

    def _sort_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda x: str(x.get("created_at") or ""),
            reverse=True,
        )

    by_type = [
        {
            "id": gid,
            "label": group_labels[gid],
            "count": len(items),
            "documents": _sort_rows(items),
        }
        for gid, items in sorted(
            by_type_map.items(),
            key=lambda kv: (-len(kv[1]), group_labels.get(kv[0], kv[0]).lower()),
        )
    ]
    by_month = [
        {
            "id": mid,
            "label": month_labels[mid],
            "count": len(items),
            "documents": _sort_rows(items),
        }
        for mid, items in sorted(
            by_month_map.items(),
            key=lambda kv: (kv[0] == "unknown", kv[0]),
            reverse=True,
        )
    ]

    missing = [c for c in checklist if not c["present"]]
    return {
        "patient_id": patient_id,
        "total": len(rows),
        "checklist": checklist,
        "missing_count": len(missing),
        "present_count": len(checklist) - len(missing),
        "counts_by_type": dict(counts_by_type),
        "counts_by_kind": dict(counts_by_kind),
        "attention": {
            "needs_ocr": attention["needs_ocr"],
            "flagged": attention["flagged"],
            "file_missing": attention["file_missing"],
            "unclassified_pdf": attention["unclassified_pdf"],
            "counts": {
                "needs_ocr": len(attention["needs_ocr"]),
                "flagged": len(attention["flagged"]),
                "file_missing": len(attention["file_missing"]),
                "unclassified_pdf": len(attention["unclassified_pdf"]),
            },
        },
        "by_type": by_type,
        "by_month": by_month,
        "documents": _sort_rows(rows),
    }
