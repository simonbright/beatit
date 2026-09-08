"""Background PDF/lab ingest: OCR + classify + chart import after a fast upload."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.services.case_manager import _case_dir
from app.services.clinical_report_classify import (
    apply_classification_to_metadata,
    clinical_report_kind_label,
    normalize_clinical_report_kind,
)
from app.services.source_catalog import SourceCatalog
from app.storage.database import Database
from app.storage.documents import DocumentStore

logger = logging.getLogger(__name__)

_running_tasks: dict[str, asyncio.Task] = {}
_jobs: dict[str, dict[str, Any]] = {}
_worker_lock = asyncio.Lock()


def get_job_payload(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        return {"id": job_id, "status": "not_found"}
    payload = {
        "id": job["id"],
        "status": job["status"],
        "document_id": job.get("document_id"),
        "progress": job.get("progress"),
        "error": job.get("error"),
        "original_filename": job.get("original_filename"),
    }
    if job.get("result") is not None:
        payload["result"] = job["result"]
    return payload


async def _open_case_store(patient_id: str, case_id: str) -> tuple[Database, DocumentStore]:
    db_path = _case_dir(patient_id, case_id) / "beatit.db"
    db = Database(db_path=db_path)
    await db.init()
    return db, DocumentStore(db)


async def _restore_prefer_kind(
    db: Database, doc: dict[str, Any], prefer_kind: str | None
) -> dict[str, Any]:
    preferred = normalize_clinical_report_kind(prefer_kind) if prefer_kind else None
    if not preferred or preferred == "unknown":
        return doc
    meta = apply_classification_to_metadata(
        doc.get("metadata"),
        {
            "kind": preferred,
            "label": clinical_report_kind_label(preferred),
            "confidence": 0.9,
            "method": "upload_hint",
        },
    )
    updated = await db.update_document_metadata(doc["id"], metadata=meta)
    return updated or {**doc, "metadata": meta}


async def _run_pdf_ingest_job(job_id: str) -> None:
    job = _jobs.get(job_id)
    if not job or job["status"] not in {"pending", "running"}:
        return

    # Serialize heavy OCR/LLM so Render starter doesn't OOM / 502 under bulk upload.
    async with _worker_lock:
        job = _jobs.get(job_id)
        if not job or job["status"] not in {"pending", "running"}:
            return
        job["status"] = "running"
        job["progress"] = {"stage": "starting"}
        patient_id = job["patient_id"]
        case_id = job["case_id"]
        doc_id = job["document_id"]

        try:
            from app.api.routes import _finalize_clinical_report_document
            from app.ingest.pdf import reextract_pdf_document

            db, store = await _open_case_store(patient_id, case_id)
            doc = await db.get_document(doc_id)
            if not doc:
                raise ValueError("Document not found for background ingest")

            job["progress"] = {"stage": "extracting"}
            # reextract also runs LLM classify when text is available
            doc = await reextract_pdf_document(store, doc)
            doc = await _restore_prefer_kind(db, doc, job.get("prefer_kind"))

            job["progress"] = {"stage": "importing_labs"}
            doc, lab_import = await _finalize_clinical_report_document(
                store, doc, patient_id=patient_id
            )
            meta = dict(doc.get("metadata") or {})
            meta["ingest_processing"] = False
            meta.pop("ingest_error", None)
            updated = await db.update_document_metadata(doc_id, metadata=meta)
            doc = updated or {**doc, "metadata": meta}

            try:
                docs = await db.list_documents()
                labels = await db.get_setting("source_labels")
                catalog = SourceCatalog.from_settings(docs, labels)
                doc["source_info"] = catalog.describe_document(doc)
            except Exception:
                logger.exception("Could not attach source_info for %s", doc_id)

            job["status"] = "completed"
            job["progress"] = None
            job["result"] = {
                "document": doc,
                "handling": doc.get("handling"),
                "lab_import": lab_import,
            }
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["error"] = "Cancelled"
            raise
        except Exception as exc:
            logger.exception("PDF ingest job %s failed", job_id)
            job["status"] = "failed"
            job["error"] = str(exc)
            job["progress"] = None
            try:
                db, _store = await _open_case_store(
                    job["patient_id"], job["case_id"]
                )
                doc = await db.get_document(job["document_id"])
                if doc:
                    meta = dict(doc.get("metadata") or {})
                    meta["ingest_processing"] = False
                    meta["ingest_error"] = str(exc)[:500]
                    await db.update_document_metadata(job["document_id"], metadata=meta)
            except Exception:
                logger.exception("Could not clear ingest_processing flag")
        finally:
            _running_tasks.pop(job_id, None)


def enqueue_pdf_ingest_job(
    *,
    document_id: str,
    patient_id: str,
    case_id: str,
    original_filename: str | None = None,
    prefer_kind: str | None = None,
) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "id": job_id,
        "status": "pending",
        "document_id": document_id,
        "patient_id": patient_id,
        "case_id": case_id,
        "original_filename": original_filename,
        "prefer_kind": prefer_kind,
        "progress": {"stage": "queued"},
        "result": None,
        "error": None,
    }
    task = asyncio.create_task(_run_pdf_ingest_job(job_id))
    _running_tasks[job_id] = task
    return get_job_payload(job_id)
