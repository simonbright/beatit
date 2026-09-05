import asyncio
import logging
from typing import Any

from app.config import settings
from app.services.audit import ANALYSIS_COMPLETED, ANALYSIS_FAILED, log_audit, preview_text
from app.services.case_manager import get_active_context, load_registry, _case_dir
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


def _db_for_scope(
    *,
    patient_id: str | None = None,
    case_id: str | None = None,
) -> Database:
    """Open the case DB pinned on the job — never trust ambient active case alone."""
    if patient_id and case_id:
        path = _case_dir(patient_id, case_id) / "beatit.db"
        if path.is_file():
            return Database(db_path=path)
    return Database()


async def _build_synthesis(
    *,
    patient_id: str | None = None,
    case_id: str | None = None,
) -> SynthesisService:
    db = _db_for_scope(patient_id=patient_id, case_id=case_id)
    model = await db.get_setting("openrouter_model") or settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
    llm = LLMClient(openrouter=OpenRouterClient(model=model))
    store = DocumentStore(db)
    return SynthesisService(store, db, llm)


def _scope_from_context() -> dict[str, str | None]:
    ctx = get_active_context()
    return {
        "patient_id": ctx.get("patient_id"),
        "case_id": ctx.get("case_id"),
        "patient_label": ctx.get("patient_label"),
        "case_label": ctx.get("case_label"),
    }


async def _run_job(
    job_id: str,
    *,
    patient_id: str | None = None,
    case_id: str | None = None,
) -> None:
    db = _db_for_scope(patient_id=patient_id, case_id=case_id)
    job = await db.get_analysis_job(job_id)
    if not job or job["status"] not in {"pending", "running"}:
        return

    patient_id = job.get("patient_id") or patient_id
    case_id = job.get("case_id") or case_id
    db = _db_for_scope(patient_id=patient_id, case_id=case_id)

    try:
        await db.update_analysis_job(job_id, status="running", started_at=_now_iso())
        synthesis = await _build_synthesis(patient_id=patient_id, case_id=case_id)
        document_ids = job["document_ids"] or None
        if not document_ids:
            document_ids = None

        analyze_kwargs = {
            "patient_id": patient_id,
            "case_id": case_id,
            "patient_label": job.get("patient_label"),
            "case_label": job.get("case_label"),
        }

        if job["job_type"] == "summarize":
            result = await synthesis.summarize_documents(
                document_ids,
                created_by=job.get("requested_by"),
                **analyze_kwargs,
            )
        elif job.get("refine_analysis_id"):
            result = await synthesis.refine_custom_task(
                analysis_id=job["refine_analysis_id"],
                query=job["query"],
                refinement=job.get("refinement_notes") or "",
                document_ids=document_ids,
                created_by=job.get("requested_by"),
                **analyze_kwargs,
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
                **analyze_kwargs,
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
                "patient_id": patient_id,
                "case_id": case_id,
            },
        )
    except asyncio.CancelledError:
        db = _db_for_scope(patient_id=patient_id, case_id=case_id)
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
                "error": str(exc)[:500],
                "patient_id": patient_id,
                "case_id": case_id,
            },
        )


def _spawn_job(
    job_id: str,
    *,
    patient_id: str | None = None,
    case_id: str | None = None,
) -> None:
    task = asyncio.create_task(
        _run_job(job_id, patient_id=patient_id, case_id=case_id),
        name=f"analysis-job-{job_id}",
    )
    _running_tasks[job_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        _running_tasks.pop(job_id, None)
        try:
            t.result()
        except (asyncio.CancelledError, Exception):
            pass

    task.add_done_callback(_cleanup)


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

    scope = _scope_from_context()
    job = await db.create_analysis_job(
        job_type=job_type,
        query=query,
        document_ids=document_ids,
        include_baseline_assessment=include_baseline_assessment,
        assessment_guidance=assessment_guidance,
        requested_by=requested_by,
        build_on_analysis_id=build_on_analysis_id,
        chat_observation_ids=chat_observation_ids,
        patient_id=scope["patient_id"],
        case_id=scope["case_id"],
        patient_label=scope["patient_label"],
        case_label=scope["case_label"],
    )
    _spawn_job(
        job["id"],
        patient_id=scope["patient_id"],
        case_id=scope["case_id"],
    )
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

    scope = _scope_from_context()
    job = await db.create_analysis_job(
        job_type="query",
        query=query,
        document_ids=document_ids,
        requested_by=requested_by,
        refine_analysis_id=analysis_id,
        refinement_notes=refinement,
        patient_id=scope["patient_id"],
        case_id=scope["case_id"],
        patient_label=scope["patient_label"],
        case_label=scope["case_label"],
    )
    _spawn_job(
        job["id"],
        patient_id=scope["patient_id"],
        case_id=scope["case_id"],
    )
    return job


async def resume_pending_jobs() -> None:
    """Resume active jobs across all case DBs (not only the currently active case)."""
    reg = load_registry()
    seen_paths: set[str] = set()
    for patient in reg.get("patients", []):
        for case in patient.get("cases", []):
            path = _case_dir(patient["id"], case["id"]) / "beatit.db"
            key = str(path)
            if key in seen_paths or not path.is_file():
                continue
            seen_paths.add(key)
            db = Database(db_path=path)
            active = await db.get_active_analysis_job()
            if active and active["id"] not in _running_tasks:
                _spawn_job(
                    active["id"],
                    patient_id=active.get("patient_id") or patient["id"],
                    case_id=active.get("case_id") or case["id"],
                )
    # Also check ambient active DB (covers fresh installs / legacy)
    db = Database()
    active = await db.get_active_analysis_job()
    if active and active["id"] not in _running_tasks:
        _spawn_job(
            active["id"],
            patient_id=active.get("patient_id"),
            case_id=active.get("case_id"),
        )


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
    if not job:
        return None
    if job["status"] not in {"pending", "running"}:
        return job

    task = _running_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    await db.update_analysis_job(
        job_id,
        status="cancelled",
        error="Cancelled by user",
        completed_at=_now_iso(),
    )
    return await db.get_analysis_job(job_id)


async def scrub_all_case_databases() -> None:
    """Init/scrub every case DB so legacy cancer defaults cannot linger on wrong patients."""
    reg = load_registry()
    for patient in reg.get("patients", []):
        for case in patient.get("cases", []):
            path = _case_dir(patient["id"], case["id"]) / "beatit.db"
            if not path.is_file():
                continue
            db = Database(db_path=path)
            await db.init(case_label=case.get("label"), case_id=case.get("id"))
