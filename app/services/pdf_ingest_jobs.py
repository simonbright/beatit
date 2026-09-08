"""Background PDF/lab ingest: OCR + classify + chart import after a fast upload."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from app.config import settings
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


def _jobs_dir() -> Path:
    path = Path(settings.data_dir) / "ingest_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
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


def _persist_job(job: dict[str, Any]) -> None:
    path = _job_path(job["id"])
    # Drop huge nested blobs from disk snapshot if needed — keep lab_import summary
    snapshot = {
        "id": job["id"],
        "status": job["status"],
        "document_id": job.get("document_id"),
        "patient_id": job.get("patient_id"),
        "case_id": job.get("case_id"),
        "original_filename": job.get("original_filename"),
        "prefer_kind": job.get("prefer_kind"),
        "progress": job.get("progress"),
        "error": job.get("error"),
        "result": job.get("result"),
    }
    try:
        path.write_text(json.dumps(snapshot, default=str), encoding="utf-8")
    except OSError:
        logger.exception("Could not persist ingest job %s", job.get("id"))


def _load_job_from_disk(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("id") != job_id:
        return None
    return data


def get_job_payload(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id) or _load_job_from_disk(job_id)
    if not job:
        return {"id": job_id, "status": "not_found"}
    # Stale "running" after process restart → mark failed so the client can recover
    if (
        job.get("status") in {"pending", "running"}
        and job_id not in _running_tasks
        and job_id not in _jobs
    ):
        job = dict(job)
        job["status"] = "failed"
        job["error"] = "Server restarted while processing — re-upload this file"
        job["progress"] = None
        _persist_job(job)
    return _public_job(job)


def _set_progress(job: dict[str, Any], stage: str) -> None:
    job["progress"] = {"stage": stage}
    _persist_job(job)


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


async def _mark_doc_stage(
    db: Database, doc_id: str, *, stage: str, job_id: str
) -> None:
    doc = await db.get_document(doc_id)
    if not doc:
        return
    meta = dict(doc.get("metadata") or {})
    meta["ingest_processing"] = True
    meta["ingest_job_id"] = job_id
    meta["ingest_stage"] = stage
    await db.update_document_metadata(doc_id, metadata=meta)


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
        _set_progress(job, "starting")
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

            _set_progress(job, "extracting")
            await _mark_doc_stage(db, doc_id, stage="extracting", job_id=job_id)
            await asyncio.sleep(0)

            # skip_local_ocr on Render is handled inside extract_pdf_text_async
            doc = await reextract_pdf_document(store, doc)
            await asyncio.sleep(0)
            doc = await _restore_prefer_kind(db, doc, job.get("prefer_kind"))

            _set_progress(job, "importing_labs")
            await _mark_doc_stage(db, doc_id, stage="importing_labs", job_id=job_id)
            await asyncio.sleep(0)

            doc, lab_import = await _finalize_clinical_report_document(
                store, doc, patient_id=patient_id
            )
            meta = dict(doc.get("metadata") or {})
            meta["ingest_processing"] = False
            meta.pop("ingest_error", None)
            meta.pop("ingest_stage", None)
            meta["ingest_job_id"] = job_id
            updated = await db.update_document_metadata(doc_id, metadata=meta)
            doc = updated or {**doc, "metadata": meta}

            try:
                docs = await db.list_documents()
                labels = await db.get_setting("source_labels")
                catalog = SourceCatalog.from_settings(docs, labels)
                doc["source_info"] = catalog.describe_document(doc)
            except Exception:
                logger.exception("Could not attach source_info for %s", doc_id)

            # Compact lab_import for disk (avoid huge profile blobs if present)
            lab_out = None
            if isinstance(lab_import, dict):
                lab_out = {
                    k: v
                    for k, v in lab_import.items()
                    if k != "profile"
                }

            job["status"] = "completed"
            job["progress"] = None
            job["result"] = {
                "document": doc,
                "handling": doc.get("handling"),
                "lab_import": lab_out if lab_out is not None else lab_import,
            }
            _persist_job(job)
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["error"] = "Cancelled"
            job["progress"] = None
            _persist_job(job)
            raise
        except Exception as exc:
            logger.exception("PDF ingest job %s failed", job_id)
            job["status"] = "failed"
            job["error"] = str(exc)
            job["progress"] = None
            _persist_job(job)
            try:
                db, _store = await _open_case_store(
                    job["patient_id"], job["case_id"]
                )
                doc = await db.get_document(job["document_id"])
                if doc:
                    meta = dict(doc.get("metadata") or {})
                    meta["ingest_processing"] = False
                    meta["ingest_error"] = str(exc)[:500]
                    meta.pop("ingest_stage", None)
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
    job = {
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
    _jobs[job_id] = job
    _persist_job(job)
    task = asyncio.create_task(_run_pdf_ingest_job(job_id))
    _running_tasks[job_id] = task
    return _public_job(job)


def pending_or_running_count() -> int:
    return sum(
        1
        for job in _jobs.values()
        if job.get("status") in {"pending", "running"}
    )
