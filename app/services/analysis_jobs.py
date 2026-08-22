import asyncio
import logging
from typing import Any

from app.config import settings
from app.services.audit import ANALYSIS_COMPLETED, ANALYSIS_FAILED, log_audit, preview_text
from app.services.llm import LLMClient
from app.services.openrouter_client import OpenRouterClient
from app.services.openrouter_models import DEFAULT_OPENROUTER_MODEL
from app.services.synthesis import SynthesisService
from app.storage.database import Database, _now_iso
from app.storage.documents import DocumentStore

logger = logging.getLogger(__name__)

_running_tasks: dict[str, asyncio.Task] = {}


class ActiveAnalysisJobError(Exception):
    def __init__(self, job: dict[str, Any]):
        self.job = job
        super().__init__("An analysis is already running")


async def _build_synthesis() -> SynthesisService:
    db = Database()
    model = await db.get_setting("openrouter_model") or settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
    llm = LLMClient(openrouter=OpenRouterClient(model=model))
    store = DocumentStore(db)
    return SynthesisService(store, db, llm)


async def _run_job(job_id: str) -> None:
    db = Database()
    job = await db.get_analysis_job(job_id)
    if not job or job["status"] not in {"pending", "running"}:
        return

    try:
        await db.update_analysis_job(job_id, status="running", started_at=_now_iso())
        synthesis = await _build_synthesis()
        document_ids = job["document_ids"] or None
        if not document_ids:
            document_ids = None

        if job["job_type"] == "summarize":
            result = await synthesis.summarize_documents(document_ids)
        elif job.get("refine_analysis_id"):
            result = await synthesis.refine_custom_task(
                analysis_id=job["refine_analysis_id"],
                query=job["query"],
                refinement=job.get("refinement_notes") or "",
                document_ids=document_ids,
                created_by=job.get("requested_by"),
            )
        else:
            result = await synthesis.analyze(
                query=job["query"],
                document_ids=document_ids,
                include_baseline_assessment=job["include_baseline_assessment"],
                assessment_guidance=job.get("assessment_guidance"),
                analysis_type=job["job_type"],
                created_by=job.get("requested_by"),
                build_on_analysis_id=job.get("build_on_analysis_id"),
                chat_observation_ids=job.get("chat_observation_ids") or None,
            )

        if job.get("chat_observation_ids"):
            await db.consume_chat_observations(job["chat_observation_ids"])

        await db.update_analysis_job(
            job_id,
            status="completed",
            analysis_id=result["id"],
            completed_at=_now_iso(),
        )
        await log_audit(
            db,
            ANALYSIS_COMPLETED,
            actor=job.get("requested_by"),
            resource_type="analysis",
            resource_id=result["id"],
            metadata={
                "job_id": job_id,
                "job_type": job["job_type"],
                "analysis_type": result.get("analysis_type"),
                "analysis_id": result["id"],
                "record_status": result.get("record_status", "official"),
                "model": result.get("model"),
                "document_count": len(result.get("document_ids") or []),
                "open_items_count": len(result.get("open_items") or []),
                "query_preview": preview_text(job.get("query")),
                "refinement": bool(job.get("refine_analysis_id")),
                "refine_analysis_id": job.get("refine_analysis_id"),
                "build_on_analysis_id": job.get("build_on_analysis_id"),
            },
        )
    except asyncio.CancelledError:
        db = Database()
        current = await db.get_analysis_job(job_id)
        if current and current["status"] in {"pending", "running"}:
            await db.update_analysis_job(
                job_id,
                status="cancelled",
                error="Cancelled by user",
                completed_at=_now_iso(),
            )
        raise
    except Exception as exc:
        logger.exception("Analysis job %s failed", job_id)
        await db.update_analysis_job(
            job_id,
            status="failed",
            error=str(exc),
            completed_at=_now_iso(),
        )
        await log_audit(
            db,
            ANALYSIS_FAILED,
            actor=job.get("requested_by"),
            resource_type="analysis_job",
            resource_id=job_id,
            metadata={
                "job_id": job_id,
                "job_type": job.get("job_type"),
                "error_preview": preview_text(str(exc), 300),
                "query_preview": preview_text(job.get("query")),
            },
        )
    finally:
        _running_tasks.pop(job_id, None)


def _spawn_job(job_id: str) -> None:
    task = asyncio.create_task(_run_job(job_id))
    _running_tasks[job_id] = task


async def enqueue_analysis_job(
    *,
    job_type: str,
    query: str = "",
    document_ids: list[str] | None = None,
    include_baseline_assessment: bool = False,
    assessment_guidance: str | None = None,
    requested_by: str | None = None,
    build_on_analysis_id: str | None = None,
    chat_observation_ids: list[str] | None = None,
) -> dict[str, Any]:
    db = Database()
    active = await db.get_active_analysis_job()
    if active:
        raise ActiveAnalysisJobError(active)

    job = await db.create_analysis_job(
        job_type=job_type,
        query=query,
        document_ids=document_ids,
        include_baseline_assessment=include_baseline_assessment,
        assessment_guidance=assessment_guidance,
        requested_by=requested_by,
        build_on_analysis_id=build_on_analysis_id,
        chat_observation_ids=chat_observation_ids,
    )
    _spawn_job(job["id"])
    return job


async def enqueue_refinement_job(
    *,
    analysis_id: str,
    query: str,
    refinement: str = "",
    document_ids: list[str] | None = None,
    requested_by: str | None = None,
) -> dict[str, Any]:
    db = Database()
    active = await db.get_active_analysis_job()
    if active:
        raise ActiveAnalysisJobError(active)

    job = await db.create_analysis_job(
        job_type="query",
        query=query,
        document_ids=document_ids,
        requested_by=requested_by,
        refine_analysis_id=analysis_id,
        refinement_notes=refinement,
    )
    _spawn_job(job["id"])
    return job


async def resume_pending_jobs() -> None:
    db = Database()
    active = await db.get_active_analysis_job()
    if not active:
        return
    if active["id"] not in _running_tasks:
        _spawn_job(active["id"])


async def get_job_payload(job_id: str) -> dict[str, Any] | None:
    db = Database()
    job = await db.get_analysis_job(job_id)
    if not job:
        return None

    payload = dict(job)
    if job["status"] == "completed" and job.get("analysis_id"):
        analysis = await db.get_analysis_by_id(job["analysis_id"])
        payload["analysis"] = analysis
    else:
        payload["analysis"] = None
    return payload


async def cancel_analysis_job(job_id: str) -> dict[str, Any] | None:
    db = Database()
    job = await db.get_analysis_job(job_id)
    if not job or job["status"] not in {"pending", "running"}:
        return None

    task = _running_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    else:
        await db.update_analysis_job(
            job_id,
            status="cancelled",
            error="Cancelled by user",
            completed_at=_now_iso(),
        )
        _running_tasks.pop(job_id, None)

    return await get_job_payload(job_id)
