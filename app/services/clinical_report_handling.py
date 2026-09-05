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
    chars = meta.get("extracted_chars")
    if isinstance(chars, int) and chars >= 40:
        return False
    if text is not None and len((text or "").strip()) >= 40:
        return False
    if meta.get("needs_ocr"):
        return True
    method = str(meta.get("extraction_method") or "").lower()
    if method in {"empty", "failed"}:
        return True
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


async def open_store_for_patient_document(
    patient_id: str,
    doc_id: str,
    *,
    active_case_id: str | None = None,
) -> tuple[Any, dict[str, Any]] | None:
    """Return (DocumentStore, document) for the case that owns doc_id."""
    from app.services.patient_documents import find_patient_document
    from app.storage.database import Database
    from app.storage.documents import DocumentStore
    from app.services.case_manager import _case_dir

    found = await find_patient_document(
        patient_id,
        doc_id,
        active_case_id=active_case_id,
    )
    if not found:
        return None
    case_id = found.get("case_id")
    if not case_id:
        return None
    db_path = _case_dir(patient_id, case_id) / "beatit.db"
    if not db_path.exists():
        return None
    db = Database(db_path=db_path)
    store = DocumentStore(db)
    doc = await db.get_document(doc_id)
    if not doc:
        return None
    return store, doc


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

    if empty and (kind in DIAGNOSTIC_CITATION_KINDS or _looks_like_clinical_filename(doc, meta)):
        reasons.append(REASON_NEEDS_OCR)
        from app.services.document_paths import resolve_document_file_path

        if not resolve_document_file_path(doc):
            messages.append(
                "Original PDF missing on disk — use Replace file in Library, then Import to Labs"
            )
        else:
            messages.append(
                "Little or no text extracted — re-extract / OCR before relying on this report"
            )


    if kind == "unknown" and _looks_like_clinical_filename(doc, meta) and not empty:
        # Only flag unclear type when it looks like a lab and charts are empty,
        # or when it strongly matches imaging/pathology keywords (not bare "report").
        if _looks_like_lab_filename(doc, meta):
            linked = _readings_from_doc(profile, str(doc.get("id") or ""))
            already = int(meta.get("lab_charts_added") or 0)
            stored_ok = str(meta.get("lab_charts_status") or "").lower() in {
                "imported",
                "already_on_profile",
            }
            if linked == 0 and already == 0 and not stored_ok:
                reasons.append(REASON_UNCLASSIFIED)
                reasons.append(REASON_LAB_CHARTS_PENDING)
                messages.append(
                    "Possible lab report with no chart readings yet — Import to Labs or re-extract"
                )
        elif _looks_like_strong_diagnostic_filename(doc, meta):
            reasons.append(REASON_UNCLASSIFIED)
            messages.append(
                "Looks like a diagnostic report but type is unclear — review and re-extract if needed"
            )

    if kind == "lab":
        linked = _readings_from_doc(profile, str(doc.get("id") or ""))
        skipped_duplicate = 0
        if lab_import is not None:
            added = int(lab_import.get("added_count") or 0)
            proposed = int(lab_import.get("proposed_count") or 0)
            incomplete = int(lab_import.get("skipped_incomplete") or 0)
            skipped_duplicate = int(lab_import.get("skipped_duplicate") or 0)
            import_failed = bool(
                lab_import.get("offer_manual_import")
                or any("failed" in str(w).lower() for w in (lab_import.get("warnings") or []))
            )
        else:
            added = max(int(meta.get("lab_charts_added") or 0), linked)
            proposed = int(meta.get("lab_charts_proposed") or 0)
            incomplete = int(meta.get("lab_charts_incomplete") or 0)
            skipped_duplicate = int(meta.get("lab_charts_duplicates") or 0)
            stored = str(meta.get("lab_charts_status") or "").lower()
            import_failed = stored in {"failed", "needs_review"} and added == 0
            if stored in {"imported", "already_on_profile"}:
                added = max(added, 1)

        charted = max(added, linked)
        # Readings already on Home Labs for this panel count as handled.
        covered_by_duplicates = skipped_duplicate > 0 and charted == 0 and incomplete == 0
        if covered_by_duplicates:
            charted = skipped_duplicate

        if empty:
            reasons.append(REASON_LAB_CHARTS_PENDING)
            messages.append("Cannot chart lab values until text is extracted")
        elif charted == 0 and not covered_by_duplicates:
            reasons.append(
                REASON_IMPORT_FAILED if import_failed or proposed > 0 else REASON_LAB_CHARTS_PENDING
            )
            messages.append(
                "Lab report is tagged but no readings are on Home Labs charts — Import to Labs"
            )
        elif incomplete > 0 and charted == 0:
            reasons.append(REASON_LAB_PARTIAL)
            messages.append(
                f"No readings charted yet; {incomplete} still need dates or review"
            )
        # If some readings are on charts, do not keep the flag for leftover incomplete rows.
        # User can re-import or dismiss; charts are no longer empty.

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
        dupes = 0
        if lab_import is not None:
            dupes = int(lab_import.get("skipped_duplicate") or 0)
        else:
            dupes = int(meta.get("lab_charts_duplicates") or 0)
        count = max(linked, stored_added, dupes)
        return {
            "status": HANDLING_OK,
            "reasons": [],
            "message": (
                f"Lab readings already on charts ({count})"
                if dupes and not linked and not stored_added
                else f"Lab readings on charts ({count or 'ok'})"
            ),
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
        "ct-",
        "ultrasound",
        "pathology",
        "biopsy",
        "echo",
        "ecg",
        "ekg",
        "bloodwork",
        "blood work",
        "lipid",
        "cholesterol",
        "hba1c",
    )
    return any(n in hay for n in needles)


def _looks_like_lab_filename(doc: dict[str, Any], meta: dict[str, Any]) -> bool:
    hay = f"{doc.get('title') or ''} {meta.get('original_filename') or ''}".lower()
    return any(
        n in hay
        for n in ("lab", "labs", "bloodwork", "blood work", "lipid", "cholesterol", "hba1c")
    )


def _looks_like_strong_diagnostic_filename(doc: dict[str, Any], meta: dict[str, Any]) -> bool:
    hay = f"{doc.get('title') or ''} {meta.get('original_filename') or ''}".lower()
    return any(
        n in hay
        for n in (
            "mri",
            "ultrasound",
            "pathology",
            "biopsy",
            "echocardiogram",
            "ct scan",
            "ct abdomen",
            "ct chest",
            "ct pelvis",
        )
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
        duplicates = int(lab_import.get("skipped_duplicate") or 0)
        meta["lab_charts_added"] = added
        meta["lab_charts_proposed"] = proposed
        meta["lab_charts_incomplete"] = incomplete
        meta["lab_charts_duplicates"] = duplicates
        if status == HANDLING_OK and duplicates > 0 and added == 0:
            meta["lab_charts_status"] = "already_on_profile"
            meta["lab_charts_added"] = max(added, duplicates)
        elif added > 0 and incomplete == 0:
            meta["lab_charts_status"] = "imported"
        elif added > 0:
            meta["lab_charts_status"] = "partial"
        elif proposed > 0 and incomplete > 0:
            meta["lab_charts_status"] = "needs_review"
        elif proposed > 0:
            meta["lab_charts_status"] = "failed" if status == HANDLING_FLAGGED else "pending"
        else:
            meta["lab_charts_status"] = "failed" if status == HANDLING_FLAGGED else "pending"

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
        "actions": _suggested_actions(evaluation["reasons"], meta=meta),
    }


def _suggested_actions(reasons: list[str], *, meta: dict[str, Any] | None = None) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if REASON_NEEDS_OCR in reasons:
        label = "Retry OCR" if (meta or {}).get("auto_ocr_attempted") else "Re-extract / OCR"
        actions.append({"id": "reextract", "label": label})
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


async def auto_reextract_needs_ocr_for_patient(
    patient_id: str,
    *,
    active_case_id: str | None = None,
    max_docs: int = 12,
) -> dict[str, Any]:
    """Re-run OCR for PDFs that still need text — no user case-switch required.

    Skips documents already auto-attempted that still lack text (avoids loops).
    Explicit Re-extract from the UI clears that gate by calling reextract directly.
    """
    from app.ingest.pdf import reextract_pdf_document
    from app.services.patient_documents import list_patient_documents

    docs = await list_patient_documents(
        patient_id,
        active_case_id=active_case_id,
        source_type="pdf",
    )
    attempted: list[str] = []
    recovered: list[str] = []
    failed: list[dict[str, str]] = []

    for doc in docs:
        if len(attempted) >= max_docs:
            break
        meta = _meta(doc)
        needs = bool(meta.get("needs_ocr")) or str(meta.get("extraction_method") or "") == "empty"
        if not needs:
            continue
        if meta.get("auto_ocr_attempted") and needs:
            # Already tried automatically; leave for explicit retry / replace-file
            continue
        opened = await open_store_for_patient_document(
            patient_id,
            doc["id"],
            active_case_id=active_case_id,
        )
        if not opened:
            failed.append({"document_id": doc["id"], "error": "Document store not found"})
            continue
        store, raw = opened
        doc_id = str(raw.get("id") or doc["id"])
        attempted.append(doc_id)
        try:
            updated = await reextract_pdf_document(store, raw)
            new_meta = dict(updated.get("metadata") or {})
            new_meta["auto_ocr_attempted"] = True
            new_meta["auto_ocr_attempted_at"] = _now_iso()
            saved = await store.db.update_document_metadata(doc_id, metadata=new_meta)
            updated = saved or {**updated, "metadata": new_meta}
            still_needs = bool(new_meta.get("needs_ocr")) or str(
                new_meta.get("extraction_method") or ""
            ) == "empty"
            if still_needs:
                failed.append(
                    {
                        "document_id": doc_id,
                        "error": new_meta.get("ocr_hint")
                        or "Still little extractable text after OCR",
                    }
                )
            else:
                recovered.append(doc_id)
                # Refresh handling so lab flags update after successful OCR
                try:
                    from app.services.case_manager import get_patient_profile

                    await refresh_document_handling(
                        store,
                        updated,
                        profile=get_patient_profile(patient_id),
                    )
                except Exception:
                    pass
        except Exception as exc:
            # Mark attempted so we do not spin forever
            try:
                fail_meta = dict(raw.get("metadata") or {})
                fail_meta["auto_ocr_attempted"] = True
                fail_meta["auto_ocr_attempted_at"] = _now_iso()
                fail_meta["auto_ocr_error"] = str(exc)[:400]
                await store.db.update_document_metadata(doc_id, metadata=fail_meta)
            except Exception:
                pass
            failed.append({"document_id": doc_id, "error": str(exc)[:400]})

    return {
        "attempted": attempted,
        "recovered": recovered,
        "failed": failed,
        "attempted_count": len(attempted),
        "recovered_count": len(recovered),
        "failed_count": len(failed),
    }
