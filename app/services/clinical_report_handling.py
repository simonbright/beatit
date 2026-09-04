"""Track whether clinical reports (labs, imaging reports, …) are handled or flagged."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.clinical_report_classify import (
    DIAGNOSTIC_CITATION_KINDS,
    clinical_report_kind_label,
    normalize_clinical_report_kind,
)

# Machine reasons shown in Flagged / alerts
REASON_NEEDS_OCR = "needs_ocr"
REASON_LAB_CHARTS_PENDING = "lab_charts_pending"
REASON_LAB_PARTIAL = "lab_partial"
REASON_UNCLASSIFIED = "unclassified"
REASON_IMPORT_FAILED = "lab_import_failed"

HANDLING_OK = "ok"
HANDLING_FLAGGED = "flagged"
HANDLING_DISMISSED = "dismissed"

REASON_LABELS: dict[str, str] = {
    REASON_NEEDS_OCR: "Needs OCR / text extraction",
    REASON_LAB_CHARTS_PENDING: "Lab readings not on charts",
    REASON_LAB_PARTIAL: "Some lab readings still need review",
    REASON_UNCLASSIFIED: "Clinical report type unclear",
    REASON_IMPORT_FAILED: "Automatic lab import failed",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _meta(doc: dict[str, Any]) -> dict[str, Any]:
    meta = doc.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _is_empty_extract(meta: dict[str, Any], text: str | None = None) -> bool:
    if meta.get("needs_ocr"):
        return True
    method = str(meta.get("extraction_method") or "").lower()
    if method in {"empty", "failed"}:
        return True
    chars = meta.get("extracted_chars")
    if isinstance(chars, int) and chars < 40:
        return True
    if text is not None and len((text or "").strip()) < 40:
        return True
    return False


def _readings_from_doc(profile: dict[str, Any] | None, doc_id: str) -> int:
    if not profile or not doc_id:
        return 0
    return sum(
        1
        for d in (profile.get("diagnostics") or [])
        if str(d.get("source_document_id") or "") == doc_id
    )


def evaluate_document_handling(
    doc: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    lab_import: dict[str, Any] | None = None,
    extracted_text: str | None = None,
) -> dict[str, Any]:
    """Derive handling status + reasons for a library document."""
    meta = _meta(doc)
    source_type = str(doc.get("source_type") or "").lower()
    if source_type != "pdf":
        return {
            "status": HANDLING_OK,
            "reasons": [],
            "message": None,
            "severity": "info",
            "kind": None,
            "kind_label": None,
        }

    if str(meta.get("handling_status") or "").lower() == HANDLING_DISMISSED:
        return {
            "status": HANDLING_DISMISSED,
            "reasons": list(meta.get("handling_reasons") or []),
            "message": meta.get("handling_message") or "Dismissed",
            "severity": "info",
            "kind": normalize_clinical_report_kind(meta.get("clinical_report_kind")),
            "kind_label": meta.get("clinical_report_kind_label"),
            "dismissed_at": meta.get("handling_dismissed_at"),
        }

    kind = normalize_clinical_report_kind(meta.get("clinical_report_kind"))
    kind_label = meta.get("clinical_report_kind_label") or clinical_report_kind_label(kind)
    reasons: list[str] = []
    messages: list[str] = []

    empty = _is_empty_extract(meta, extracted_text)
    is_clinical = kind in DIAGNOSTIC_CITATION_KINDS or kind == "unknown"

    if empty and (kind in DIAGNOSTIC_CITATION_KINDS or _looks_like_clinical_filename(doc, meta)):
        reasons.append(REASON_NEEDS_OCR)
        messages.append("Little or no text extracted — re-extract / OCR before relying on this report")

    if kind == "unknown" and _looks_like_clinical_filename(doc, meta) and not empty:
        reasons.append(REASON_UNCLASSIFIED)
        messages.append("Looks like a clinical report but type is unclear — review and re-extract if needed")
        if _looks_like_lab_filename(doc, meta):
            reasons.append(REASON_LAB_CHARTS_PENDING)
            messages.append("Possible lab report with no chart readings yet — Import to Labs or re-extract")

    if kind == "lab":
        linked = _readings_from_doc(profile, str(doc.get("id") or ""))
        if lab_import is not None:
            added = int(lab_import.get("added_count") or 0)
            proposed = int(lab_import.get("proposed_count") or 0)
            incomplete = int(lab_import.get("skipped_incomplete") or 0)
            import_failed = bool(
                lab_import.get("offer_manual_import")
                or any("failed" in str(w).lower() for w in (lab_import.get("warnings") or []))
            )
        else:
            added = max(int(meta.get("lab_charts_added") or 0), linked)
            proposed = int(meta.get("lab_charts_proposed") or 0)
            incomplete = int(meta.get("lab_charts_incomplete") or 0)
            stored = str(meta.get("lab_charts_status") or "").lower()
            import_failed = stored in {"failed", "needs_review"} and added == 0

        charted = max(added, linked)
        if empty:
            reasons.append(REASON_LAB_CHARTS_PENDING)
            messages.append("Cannot chart lab values until text is extracted")
        elif charted == 0:
            reasons.append(
                REASON_IMPORT_FAILED if import_failed or proposed > 0 else REASON_LAB_CHARTS_PENDING
            )
            messages.append(
                "Lab report is tagged but no readings are on Home Labs charts — Import to Labs"
            )
        elif incomplete > 0 or (proposed > charted):
            reasons.append(REASON_LAB_PARTIAL)
            leftover = max(incomplete, proposed - charted)
            messages.append(
                f"{charted} reading(s) on charts; {leftover} still need dates or review"
            )

    # Non-lab diagnostic reports with text are considered handled once classified
    if not reasons and kind in DIAGNOSTIC_CITATION_KINDS and kind != "lab":
        return {
            "status": HANDLING_OK,
            "reasons": [],
            "message": f"Tagged as {kind_label}",
            "severity": "info",
            "kind": kind,
            "kind_label": kind_label,
        }

    if not reasons and kind == "lab":
        linked = _readings_from_doc(profile, str(doc.get("id") or ""))
        stored_added = int(meta.get("lab_charts_added") or 0)
        return {
            "status": HANDLING_OK,
            "reasons": [],
            "message": f"Lab readings on charts ({max(linked, stored_added) or 'ok'})",
            "severity": "info",
            "kind": kind,
            "kind_label": kind_label,
        }

    if not reasons:
        return {
            "status": HANDLING_OK,
            "reasons": [],
            "message": None,
            "severity": "info",
            "kind": kind if kind != "unknown" else None,
            "kind_label": kind_label if kind != "unknown" else None,
        }

    # Deduplicate reasons preserving order
    seen: set[str] = set()
    uniq_reasons: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq_reasons.append(r)

    severity = "critical" if (
        REASON_LAB_CHARTS_PENDING in uniq_reasons
        or REASON_IMPORT_FAILED in uniq_reasons
        or REASON_NEEDS_OCR in uniq_reasons
    ) else "warning"

    return {
        "status": HANDLING_FLAGGED,
        "reasons": uniq_reasons,
        "message": "; ".join(messages) if messages else REASON_LABELS.get(uniq_reasons[0], "Needs review"),
        "severity": severity,
        "kind": kind if kind != "unknown" else None,
        "kind_label": kind_label if kind != "unknown" else None,
    }


def _looks_like_clinical_filename(doc: dict[str, Any], meta: dict[str, Any]) -> bool:
    hay = f"{doc.get('title') or ''} {meta.get('original_filename') or ''}".lower()
    needles = (
        "lab",
        "labs",
        "mri",
        "ct ",
        "ultrasound",
        "pathology",
        "biopsy",
        "echo",
        "ecg",
        "ekg",
        "blood",
        "lipid",
        "report",
    )
    return any(n in hay for n in needles)


def _looks_like_lab_filename(doc: dict[str, Any], meta: dict[str, Any]) -> bool:
    hay = f"{doc.get('title') or ''} {meta.get('original_filename') or ''}".lower()
    return any(
        n in hay
        for n in ("lab", "labs", "bloodwork", "blood work", "lipid", "cholesterol", "hba1c")
    )


def apply_handling_to_metadata(
    metadata: dict[str, Any] | None,
    evaluation: dict[str, Any],
    *,
    lab_import: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    status = evaluation.get("status") or HANDLING_OK
    # Don't overwrite an explicit dismiss unless re-evaluating to ok after success
    if str(meta.get("handling_status") or "") == HANDLING_DISMISSED and status != HANDLING_OK:
        return meta

    meta["handling_status"] = status
    meta["handling_reasons"] = list(evaluation.get("reasons") or [])
    meta["handling_message"] = evaluation.get("message")
    meta["handling_severity"] = evaluation.get("severity") or "info"
    meta["handling_updated_at"] = _now_iso()
    if status == HANDLING_OK:
        meta.pop("handling_dismissed_at", None)

    if lab_import is not None:
        added = int(lab_import.get("added_count") or 0)
        proposed = int(lab_import.get("proposed_count") or 0)
        incomplete = int(lab_import.get("skipped_incomplete") or 0)
        meta["lab_charts_added"] = added
        meta["lab_charts_proposed"] = proposed
        meta["lab_charts_incomplete"] = incomplete
        if added > 0 and incomplete == 0 and proposed <= added:
            meta["lab_charts_status"] = "imported"
        elif added > 0:
            meta["lab_charts_status"] = "partial"
        elif proposed > 0:
            meta["lab_charts_status"] = "needs_review"
        else:
            meta["lab_charts_status"] = "failed" if evaluation.get("status") == HANDLING_FLAGGED else "pending"

    return meta


async def refresh_document_handling(
    store: Any,
    doc: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    lab_import: dict[str, Any] | None = None,
    extracted_text: str | None = None,
) -> dict[str, Any]:
    """Evaluate handling and persist metadata on the document."""
    evaluation = evaluate_document_handling(
        doc,
        profile=profile,
        lab_import=lab_import,
        extracted_text=extracted_text,
    )
    meta = apply_handling_to_metadata(
        doc.get("metadata"),
        evaluation,
        lab_import=lab_import,
    )
    updated = await store.db.update_document_metadata(doc["id"], metadata=meta)
    result = updated or {**doc, "metadata": meta}
    result["handling"] = {
        **evaluation,
        "document_id": result.get("id"),
        "title": result.get("title"),
        "source_type": result.get("source_type"),
    }
    return result


def flag_item_from_document(
    doc: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a Flagged-list item, or None if not flagged."""
    evaluation = evaluate_document_handling(doc, profile=profile)
    if evaluation["status"] != HANDLING_FLAGGED:
        return None
    meta = _meta(doc)
    return {
        "document_id": doc.get("id"),
        "title": doc.get("citation_display_name") or doc.get("title"),
        "source_type": doc.get("source_type"),
        "kind": evaluation.get("kind") or meta.get("clinical_report_kind"),
        "kind_label": evaluation.get("kind_label") or meta.get("clinical_report_kind_label"),
        "reasons": evaluation["reasons"],
        "reason_labels": [REASON_LABELS.get(r, r) for r in evaluation["reasons"]],
        "message": evaluation.get("message"),
        "severity": evaluation.get("severity") or "warning",
        "created_at": doc.get("created_at"),
        "updated_at": meta.get("handling_updated_at") or doc.get("updated_at"),
        "actions": _suggested_actions(evaluation["reasons"]),
    }


def _suggested_actions(reasons: list[str]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if REASON_NEEDS_OCR in reasons:
        actions.append({"id": "reextract", "label": "Re-extract / OCR"})
    if any(
        r in reasons
        for r in (REASON_LAB_CHARTS_PENDING, REASON_LAB_PARTIAL, REASON_IMPORT_FAILED)
    ):
        actions.append({"id": "import_labs", "label": "Import to Labs"})
    actions.append({"id": "view", "label": "View document"})
    actions.append({"id": "dismiss", "label": "Dismiss"})
    # Deduplicate by id
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for a in actions:
        if a["id"] not in seen:
            seen.add(a["id"])
            out.append(a)
    return out


async def list_flagged_documents_for_patient(
    patient_id: str,
    *,
    active_case_id: str | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.services.case_manager import get_patient_profile
    from app.services.patient_documents import list_patient_documents

    docs = await list_patient_documents(
        patient_id,
        active_case_id=active_case_id,
    )
    prof = profile if profile is not None else get_patient_profile(patient_id)
    items: list[dict[str, Any]] = []
    for doc in docs:
        item = flag_item_from_document(doc, profile=prof)
        if item:
            items.append(item)
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    items.sort(
        key=lambda it: (
            severity_rank.get(str(it.get("severity") or "info"), 9),
            str(it.get("updated_at") or it.get("created_at") or ""),
        )
    )
    return {
        "items": items,
        "count": len(items),
        "critical_count": sum(1 for it in items if it.get("severity") == "critical"),
    }


async def dismiss_document_handling(store: Any, doc: dict[str, Any]) -> dict[str, Any]:
    meta = _meta(doc)
    meta["handling_status"] = HANDLING_DISMISSED
    meta["handling_dismissed_at"] = _now_iso()
    meta["handling_updated_at"] = _now_iso()
    meta["handling_message"] = "Dismissed — marked as reviewed"
    updated = await store.db.update_document_metadata(doc["id"], metadata=meta)
    return updated or {**doc, "metadata": meta}
