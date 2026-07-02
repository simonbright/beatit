from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Callable, Awaitable

from app.storage.database import Database
from app.storage.documents import DocumentStore

logger = logging.getLogger(__name__)

_running_tasks: dict[str, asyncio.Task] = {}
_jobs: dict[str, dict[str, Any]] = {}


async def _run_job(job_id: str) -> None:
    job = _jobs.get(job_id)
    if not job or job["status"] not in {"pending", "running"}:
        return

    db = Database()
    store = DocumentStore(db)
    job["status"] = "running"

    async def on_progress(update: dict[str, Any]) -> None:
        job["progress"] = update

    try:
        from app.services.imaging_vision import analyze_imaging_slices

        result = await analyze_imaging_slices(
            store,
            db,
            document_ids=job["document_ids"],
            created_by=job.get("requested_by"),
            on_progress=on_progress,
        )
        job["status"] = "completed"
        job["result"] = result
        job["progress"] = None
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        job["error"] = "Cancelled by user"
        raise
    except Exception as exc:
        logger.exception("Vision job %s failed", job_id)
        job["status"] = "failed"
        job["error"] = str(exc)
        job["progress"] = None
    finally:
        _running_tasks.pop(job_id, None)


def enqueue_vision_job(
    *,
    document_ids: list[str],
    requested_by: str | None = None,
) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "id": job_id,
        "status": "pending",
        "document_ids": list(document_ids),
        "requested_by": requested_by,
        "progress": None,
        "result": None,
        "error": None,
    }
    task = asyncio.create_task(_run_job(job_id))
    _running_tasks[job_id] = task
    return get_job_payload(job_id)


def get_job_payload(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        return {"id": job_id, "status": "not_found"}
    payload = {
        "id": job["id"],
        "status": job["status"],
        "document_ids": job.get("document_ids") or [],
        "progress": job.get("progress"),
        "error": job.get("error"),
    }
    if job.get("result"):
        payload["result"] = job["result"]
    return payload


async def cancel_vision_job(job_id: str) -> dict[str, Any]:
    job = _jobs.get(job_id)
    if not job:
        return get_job_payload(job_id)
    if job["status"] not in {"pending", "running"}:
        return get_job_payload(job_id)

    task = _running_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    else:
        job["status"] = "cancelled"
        job["error"] = "Cancelled by user"

    return get_job_payload(job_id)
