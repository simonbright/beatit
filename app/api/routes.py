from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, Field
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

from app.ingest.text import ingest_text
from app.ingest.url import ingest_url
from app.ingest.pdf import ingest_pdf_file, reextract_pdf_document
from app.services.medication_import import (
    clamp_proposed_medication,
    propose_medications_from_upload,
)
from app.services.diagnostic_import import (
    auto_confirm_lab_readings_from_document,
    clamp_proposed_diagnostic,
    propose_diagnostics_from_document,
    propose_diagnostics_from_upload,
)
from app.services.medication_safety import (
    get_medication_safety,
    run_medication_safety_review,
)
from app.ingest.imaging import ingest_imaging_file, is_allowed_imaging_upload
from app.ingest.video import ingest_video
from app.services.llm import LLMClient
from app.services.openrouter_client import OpenRouterClient
from app.services.openrouter_models import DEFAULT_OPENROUTER_MODEL, MODEL_IDS, OPENROUTER_MODELS
from app.services.patient_context import DEFAULT_PATIENT_CONTEXT, DEFAULT_REVIEWER_CONTEXT
from app.services.analysis_jobs import (
    ActiveAnalysisJobError,
    cancel_analysis_job,
    enqueue_analysis_job,
    enqueue_refinement_job,
    get_job_payload,
)
from app.services.synthesis import SynthesisService
from app.services.options_chat import OPTIONS_STARTER_PROMPTS, OptionsChatService
from app.services.chat_observations import (
    observation_library_document_ids,
    resolve_observations_for_analysis,
    save_observation_to_library,
)
from app.services.document_view import build_document_view, guess_media_type
from app.services.document_paths import heal_document_paths, resolve_document_file_path
from app.services.dicom_preview import is_dicom_document, render_dicom_preview_png
from app.services.investigation import InvestigationService
from app.services.imaging_catalog import (
    build_imaging_facets,
    build_imaging_series_catalog,
    match_imaging_documents,
)
from app.services.imaging_vision import sample_document_ids
from app.services.vision_jobs import (
    cancel_vision_job,
    enqueue_vision_job,
    get_job_payload as get_vision_job_payload,
)
from app.ingest.imaging import reindex_all_imaging_metadata
from app.services.pdf_export import (
    assessment_pdf_filename,
    build_assessment_pdf,
    build_diagnostics_pdf,
    build_document_coverage_pdf,
    build_medications_pdf,
    coverage_pdf_filename,
    diagnostics_pdf_filename,
    filter_medications_for_export,
    medication_export_scope_label,
    medications_pdf_filename,
    normalize_medication_export_scope,
)
from app.services.source_catalog import (
    DEFAULT_SOURCE_TYPES,
    SOURCE_TYPE_KEYS,
    SourceCatalog,
)
from app.services.audit import (
    ANALYSIS_COMPLETED,
    ANALYSIS_FAILED,
    ANALYSIS_PROMOTED,
    ANALYSIS_DRAFT_DISCARDED,
    ANALYSIS_REQUESTED,
    AUTH_LOGIN,
    AUTH_LOGOUT,
    CATEGORY_PREFIXES,
    DOCUMENT_CREATED,
    DOCUMENT_DELETED,
    DOCUMENT_CITATION_UPDATED,
    OPEN_ITEM_COMMENT_ADDED,
    OPEN_ITEM_INVESTIGATION_ACCEPTED,
    OPEN_ITEM_INVESTIGATION_DISCARDED,
    OPEN_ITEM_INVESTIGATION_DRAFT_COMMENTED,
    OPEN_ITEM_INVESTIGATION_DRAFT_CREATED,
    OPEN_ITEM_INVESTIGATION_FAILED,
    OPEN_ITEM_INVESTIGATION_STARTED,
    OPEN_ITEM_STATUS_CHANGED,
    PDF_EXPORTED,
    ANALYSIS_ANNOTATIONS_UPDATED,
    SETTINGS_MODEL_UPDATED,
    SETTINGS_PATIENT_CONTEXT_UPDATED,
    SETTINGS_REVIEWER_CONTEXT_UPDATED,
    SETTINGS_SOURCE_LABELS_UPDATED,
    enrich_audit_event,
    log_audit,
    preview_text,
)
from app.services.auth_session import (
    COOKIE_NAME,
    SESSION_DAYS,
    create_session_token,
    verify_credentials,
    verify_session_token,
)
from app.config import settings
from app.storage.database import Database
from app.storage.documents import DocumentStore
from app.version import version_info
from app.services.case_manager import (
    list_patients,
    create_patient,
    delete_patient,
    list_cases,
    create_case,
    delete_case,
    rename_case,
    get_active_context,
    activate_patient_case,
    sibling_case_dirs,
    find_patient_photo,
    save_patient_photo,
    get_patient_profile,
    update_patient_demographics,
    add_patient_measurement,
    delete_patient_measurement,
    add_patient_diagnostic,
    delete_patient_diagnostic,
    group_diagnostics_for_charts,
    add_patient_journal_entry,
    delete_patient_journal_entry,
    group_journal_for_charts,
    add_patient_medication,
    update_patient_medication,
    stop_patient_medication,
    delete_patient_medication,
    add_patient_food_drink,
    update_patient_food_drink,
    delete_patient_food_drink,
    add_patient_milestone,
    update_patient_milestone,
    delete_patient_milestone,
    age_years_from_dob,
    DIAGNOSTIC_PRESETS,
    JOURNAL_PRESETS,
    COMMON_REMEDIES,
)
from app.services.patient_milestones import MILESTONE_PRESETS, all_chart_milestones
from app.services.patient_documents import (
    list_active_patient_document_index,
    list_active_patient_documents_page,
    resolve_active_patient_document,
)

router = APIRouter(prefix="/api")


class TextIngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UrlIngestRequest(BaseModel):
    url: str = Field(min_length=4)
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class YoutubeIngestRequest(BaseModel):
    url: str = Field(min_length=4)
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FacebookIngestRequest(BaseModel):
    url: str = Field(min_length=4)
    title: str | None = None
    notes: str | None = Field(default=None, max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    query: str = Field(default="")
    document_ids: list[str] | None = None
    include_baseline_assessment: bool = False
    assessment_guidance: str | None = Field(default=None, max_length=5000)
    chat_observation_ids: list[str] | None = None


class OptionsChatSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    document_ids: list[str] | None = None
    include_latest_assessment: bool = True


class OptionsChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    stream: bool = True


class ChatObservationCreateRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message_id: str | None = None
    excerpt: str = Field(min_length=1, max_length=50000)
    title: str | None = Field(default=None, max_length=200)
    include_in_analysis: bool = True


class ChatObservationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    include_in_analysis: bool | None = None


class ApplyChatToHomeRequest(BaseModel):
    session_id: str = Field(min_length=1)
    message_id: str | None = None
    document_ids: list[str] | None = None
    assessment_guidance: str | None = Field(default=None, max_length=20000)


class ImagingVisionRequest(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=10)


class SettingsUpdateRequest(BaseModel):
    openrouter_model: str | None = None
    reviewer_context: str | None = None
    patient_context: str | None = None
    source_labels: dict[str, dict[str, str]] | None = None


class DocumentCitationUpdateRequest(BaseModel):
    citation_display_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


def _cookie_secure() -> bool:
    return bool(settings.render or settings.public_url.startswith("https"))


def _set_session_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_token(username),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=SESSION_DAYS * 86400,
        path="/",
    )


class OpenItemUpdateRequest(BaseModel):
    status: str | None = Field(default=None, max_length=50)
    comment: str | None = Field(default=None, max_length=5000)


class InvestigateOpenItemRequest(BaseModel):
    guidance: str = Field(default="", max_length=5000)


class AcceptInvestigationRequest(BaseModel):
    edited_response: str | None = Field(default=None, max_length=50000)


class AnalysisAnnotationsUpdate(BaseModel):
    annotation_title: str | None = Field(default=None, max_length=500)
    annotation_header: str | None = Field(default=None, max_length=5000)
    annotation_notes: str | None = Field(default=None, max_length=20000)


class RefineDraftRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20000)
    refinement: str = Field(default="", max_length=10000)
    document_ids: list[str] | None = None


async def _source_catalog(db: Database, documents: list[dict[str, Any]] | None = None) -> SourceCatalog:
    docs = documents if documents is not None else await db.list_documents()
    return SourceCatalog.from_settings(docs, await db.get_setting("source_labels"))


def _source_labels_payload(catalog: SourceCatalog) -> dict[str, dict[str, str]]:
    return {
        key: {
            "display": catalog.type_defs[key]["display"],
            "shorthand": catalog.type_defs[key]["shorthand"],
        }
        for key in SOURCE_TYPE_KEYS
    }


async def _get_services():
    db = Database()
    model = await db.get_setting("openrouter_model") or settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
    llm = LLMClient(openrouter=OpenRouterClient(model=model))
    store = DocumentStore(db)
    synthesis = SynthesisService(store, db, llm)
    investigation = InvestigationService(store, db, llm)
    return db, store, llm, synthesis, investigation


def _options_chat_service(db: Database, store: DocumentStore, llm: LLMClient) -> OptionsChatService:
    return OptionsChatService(store, db, llm)


def _actor(request: Request | None) -> str | None:
    if not request:
        return None
    return getattr(request.state, "user", None)


async def _audit(
    db: Database,
    request: Request | None,
    event_type: str,
    *,
    actor: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await log_audit(
        db,
        event_type,
        actor=actor or _actor(request),
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
    )


async def _finalize_clinical_report_document(
    store: DocumentStore,
    doc: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Auto-import lab charts when applicable, then persist handled/flagged state."""
    from app.services.clinical_report_handling import refresh_document_handling

    meta = doc.get("metadata") or {}
    kind = str(meta.get("clinical_report_kind") or "").lower()
    lab_import: dict[str, Any] | None = None
    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    profile = get_patient_profile(patient_id) if patient_id else None

    if kind == "lab" and patient_id:
        text = await store.read_extracted_text(doc)
        try:
            lab_import = await auto_confirm_lab_readings_from_document(
                patient_id,
                doc,
                extracted_text=text,
            )
            if lab_import.get("profile"):
                profile = lab_import["profile"]
        except Exception:
            lab_import = {
                "added_count": 0,
                "proposed_count": 0,
                "skipped_incomplete": 0,
                "offer_manual_import": True,
                "document_id": doc.get("id"),
                "document_title": doc.get("title"),
                "warnings": ["Automatic lab import failed — use Import to Labs"],
                "errors": [],
            }

    text = await store.read_extracted_text(doc)
    updated = await refresh_document_handling(
        store,
        doc,
        profile=profile,
        lab_import=lab_import,
        extracted_text=text,
    )
    if lab_import is not None and updated.get("handling"):
        lab_import["handling"] = updated["handling"]
        lab_import["flagged"] = updated["handling"].get("status") == "flagged"
        if lab_import.get("already_on_profile"):
            lab_import["flagged"] = False
    return updated, lab_import


@router.post("/login")
async def login(body: LoginRequest, response: Response, request: Request):
    if not settings.auth_enabled:
        return {"ok": True, "username": body.username.strip(), "auth": "disabled"}

    username = body.username.strip()
    if not verify_credentials(username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _set_session_cookie(response, username)
    db, _, _, _, _ = await _get_services()
    await _audit(
        db,
        request,
        AUTH_LOGIN,
        actor=username,
        resource_type="session",
        metadata={"username": username},
    )
    return {"ok": True, "username": username}


@router.post("/logout")
async def logout(response: Response, request: Request):
    db, _, _, _, _ = await _get_services()
    username = _actor(request)
    await _audit(
        db,
        request,
        AUTH_LOGOUT,
        actor=username,
        resource_type="session",
        metadata={"username": username} if username else None,
    )
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def auth_me(request: Request):
    if not settings.auth_enabled:
        return {"authenticated": True, "username": None, "auth": "disabled"}
    username = verify_session_token(request.cookies.get(COOKIE_NAME))
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"authenticated": True, "username": username}


@router.get("/health")
async def health():
    from app.ingest.pdf import ocr_runtime_status

    _, _, llm, _, _ = await _get_services()
    llm_status = await llm.health()
    return {
        "status": "ok",
        "llm": llm_status,
        "ocr": ocr_runtime_status(),
        **version_info(),
    }


@router.get("/version")
async def app_version():
    return version_info()


@router.get("/settings")
async def get_app_settings():
    db, _, llm, _, _ = await _get_services()
    model = await db.get_setting("openrouter_model") or settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
    patient_context = await db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
    reviewer_context = await db.get_setting("reviewer_context") or DEFAULT_REVIEWER_CONTEXT
    catalog = await _source_catalog(db, [])
    llm_health = await llm.health()
    return {
        "settings": {
            "llm_provider": settings.llm_provider,
            "openrouter_model": model,
            "ollama_base_url": settings.ollama_base_url,
            "ollama_model": settings.ollama_model,
            "ollama_vision_model": settings.ollama_vision_model,
            "patient_context": patient_context,
            "reviewer_context": reviewer_context,
            "source_labels": _source_labels_payload(catalog),
        },
        "llm": llm_health,
        "models": OPENROUTER_MODELS,
        "default_model": DEFAULT_OPENROUTER_MODEL,
        "default_patient_context": DEFAULT_PATIENT_CONTEXT,
        "default_reviewer_context": DEFAULT_REVIEWER_CONTEXT,
        "default_source_labels": _source_labels_payload(SourceCatalog.from_settings([], None)),
        "source_legend": catalog.legend(),
    }


@router.put("/settings")
async def update_app_settings(body: SettingsUpdateRequest, request: Request):
    if (
        body.openrouter_model is None
        and body.reviewer_context is None
        and body.patient_context is None
        and body.source_labels is None
    ):
        raise HTTPException(status_code=400, detail="Nothing to update")

    db, _, _, _, _ = await _get_services()
    updated: dict[str, str] = {}

    if body.openrouter_model is not None:
        model_id = body.openrouter_model.strip()
        if model_id not in MODEL_IDS:
            raise HTTPException(
                status_code=400,
                detail="Choose a model from the list or pick a supported preset",
            )
        old_model = await db.get_setting("openrouter_model")
        await db.set_setting("openrouter_model", model_id)
        updated["openrouter_model"] = model_id
        await _audit(
            db,
            request,
            SETTINGS_MODEL_UPDATED,
            resource_type="setting",
            resource_id="openrouter_model",
            metadata={"old_model": old_model, "new_model": model_id},
        )

    if body.reviewer_context is not None:
        context = body.reviewer_context.strip()
        if not context:
            raise HTTPException(status_code=400, detail="Clinical reviewer context cannot be empty")
        if len(context) > 5000:
            raise HTTPException(
                status_code=400,
                detail="Clinical reviewer context is too long (max 5000 characters)",
            )
        old_context = await db.get_setting("reviewer_context") or ""
        await db.set_setting("reviewer_context", context)
        updated["reviewer_context"] = context
        await _audit(
            db,
            request,
            SETTINGS_REVIEWER_CONTEXT_UPDATED,
            resource_type="setting",
            resource_id="reviewer_context",
            metadata={
                "old_length": len(old_context),
                "new_length": len(context),
                "old_preview": preview_text(old_context, 180),
                "new_preview": preview_text(context, 180),
            },
        )

    if body.patient_context is not None:
        context = body.patient_context.strip()
        if not context:
            raise HTTPException(status_code=400, detail="Patient context cannot be empty")
        if len(context) > 5000:
            raise HTTPException(status_code=400, detail="Patient context is too long (max 5000 characters)")
        old_context = await db.get_setting("patient_context") or ""
        await db.set_setting("patient_context", context)
        updated["patient_context"] = context
        await _audit(
            db,
            request,
            SETTINGS_PATIENT_CONTEXT_UPDATED,
            resource_type="setting",
            resource_id="patient_context",
            metadata={
                "old_length": len(old_context),
                "new_length": len(context),
                "old_preview": preview_text(old_context, 180),
                "new_preview": preview_text(context, 180),
            },
        )

    if body.source_labels is not None:
        old_catalog = await _source_catalog(db, [])
        validated: dict[str, dict[str, str]] = {}
        changes: list[str] = []
        for key in SOURCE_TYPE_KEYS:
            incoming = body.source_labels.get(key)
            if not isinstance(incoming, dict):
                raise HTTPException(status_code=400, detail=f"Missing source label for {key}")
            display = str(incoming.get("display", "")).strip()[:120]
            shorthand = str(incoming.get("shorthand", "")).strip()[:12]
            if not display or not shorthand:
                raise HTTPException(
                    status_code=400,
                    detail=f"Display name and shorthand are required for {key}",
                )
            default = DEFAULT_SOURCE_TYPES[key]
            entry: dict[str, str] = {}
            if display != default["display"]:
                entry["display"] = display
            if shorthand != default["shorthand"]:
                entry["shorthand"] = shorthand
            if entry:
                validated[key] = entry
            old = old_catalog.type_defs[key]
            if display != old["display"] or shorthand != old["shorthand"]:
                changes.append(
                    f'{key}: [{old["shorthand"]}] {old["display"]} → [{shorthand}] {display}'
                )
        await db.set_setting("source_labels", json.dumps(validated))
        updated["source_labels"] = _source_labels_payload(
            SourceCatalog.from_settings([], json.dumps(validated))
        )
        if changes:
            await _audit(
                db,
                request,
                SETTINGS_SOURCE_LABELS_UPDATED,
                resource_type="setting",
                resource_id="source_labels",
                metadata={
                    "summary": "; ".join(changes),
                    "changes": "; ".join(changes),
                },
            )

    return {"settings": updated}


@router.get("/audit-events")
async def list_audit_events(
    limit: int = 100,
    offset: int = 0,
    category: str = "all",
):
    db, _, _, _, _ = await _get_services()
    event_types = None
    if category and category != "all":
        event_types = list(CATEGORY_PREFIXES.get(category, ()))
        if not event_types:
            raise HTTPException(status_code=400, detail="Unknown audit category")

    events, total = await db.list_audit_events(
        limit=limit,
        offset=offset,
        event_types=event_types,
    )
    return {
        "events": [enrich_audit_event(event) for event in events],
        "total": total,
        "limit": max(1, min(limit, 500)),
        "offset": max(0, offset),
        "category": category,
    }


@router.get("/documents/index")
async def document_index():
    db, _, _, _, _ = await _get_services()
    patient_payload = await list_active_patient_document_index()
    if patient_payload is not None:
        return patient_payload
    return {
        "documents": await db.list_document_index(),
        "total": await db.count_documents(),
        "counts_by_type": await db.document_type_counts(),
    }


@router.get("/handling/flagged")
async def list_handling_flagged():
    """Labs / diagnostic reports that still need handling."""
    from app.services.clinical_report_handling import (
        auto_reextract_needs_ocr_for_patient,
        list_flagged_documents_for_patient,
    )

    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    if not patient_id:
        return {"items": [], "count": 0, "critical_count": 0}
    ocr_result = await auto_reextract_needs_ocr_for_patient(
        patient_id,
        active_case_id=ctx.get("case_id"),
    )
    payload = await list_flagged_documents_for_patient(
        patient_id,
        active_case_id=ctx.get("case_id"),
    )
    payload["auto_ocr"] = ocr_result
    return payload


@router.post("/documents/{doc_id}/handling/dismiss")
async def dismiss_document_handling_flag(doc_id: str, request: Request):
    from app.services.clinical_report_handling import (
        dismiss_document_handling,
        open_store_for_patient_document,
    )

    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    db, store, _, _, _ = await _get_services()
    doc = None
    target_store = store

    if patient_id:
        opened = await open_store_for_patient_document(
            patient_id,
            doc_id,
            active_case_id=ctx.get("case_id"),
        )
        if opened:
            target_store, doc = opened
    if doc is None:
        doc = await db.get_document(doc_id)
        target_store = store
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    updated = await dismiss_document_handling(target_store, doc)
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc_id,
        metadata={
            "action": "handling_dismiss",
            "title": updated.get("title"),
        },
    )
    catalog = await _source_catalog(db)
    updated["source_info"] = catalog.describe_document(updated)
    return {"document": updated, "ok": True}


@router.post("/handling/refresh")
async def refresh_all_handling_flags():
    """Re-evaluate handled/flagged state for all patient clinical PDFs."""
    from app.services.clinical_report_handling import (
        auto_reextract_needs_ocr_for_patient,
        list_flagged_documents_for_patient,
        open_store_for_patient_document,
        refresh_document_handling,
    )
    from app.services.patient_documents import list_patient_documents

    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    if not patient_id:
        return {"items": [], "count": 0, "critical_count": 0, "refreshed": 0}
    profile = get_patient_profile(patient_id)
    # Auto-OCR first so the user is not asked to switch cases or click Re-extract
    ocr_result = await auto_reextract_needs_ocr_for_patient(
        patient_id,
        active_case_id=ctx.get("case_id"),
    )
    docs = await list_patient_documents(
        patient_id,
        active_case_id=ctx.get("case_id"),
        source_type="pdf",
    )
    refreshed = 0
    for doc in docs:
        meta = doc.get("metadata") or {}
        kind = str(meta.get("clinical_report_kind") or "").lower()
        if not kind and not (
            "lab" in str(doc.get("title") or "").lower()
            or "lab" in str(meta.get("original_filename") or "").lower()
        ):
            # Still refresh PDFs that already carry handling metadata
            if not meta.get("handling_status"):
                continue
        opened = await open_store_for_patient_document(
            patient_id,
            doc["id"],
            active_case_id=ctx.get("case_id"),
        )
        if not opened:
            continue
        store, raw = opened
        await refresh_document_handling(store, raw, profile=profile)
        refreshed += 1
    payload = await list_flagged_documents_for_patient(
        patient_id,
        active_case_id=ctx.get("case_id"),
        profile=profile,
    )
    payload["refreshed"] = refreshed
    payload["auto_ocr"] = ocr_result
    return payload


@router.get("/documents/imaging/facets")
async def imaging_facets():
    db, _, _, _, _ = await _get_services()
    documents = await db.list_imaging_documents()
    return build_imaging_facets(documents)


@router.get("/documents/imaging/series")
async def imaging_series():
    db, _, _, _, _ = await _get_services()
    documents = await db.list_imaging_documents()
    return build_imaging_series_catalog(documents)


@router.post("/documents/imaging/reindex-metadata")
async def imaging_reindex_metadata(request: Request):
    db, store, _, _, _ = await _get_services()
    result = await reindex_all_imaging_metadata(store, db)
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="imaging",
        metadata={"action": "reindex_metadata", **result},
    )
    return result


@router.get("/documents/imaging/match")
async def imaging_match(
    modality: str | None = None,
    study_description: str | None = None,
    study_date: str | None = None,
    series_kind: str | None = None,
    series_description: str | None = None,
    series_key: str | None = None,
    convolution_kernel: str | None = None,
    anatomy_level: str | None = None,
    body_part: str | None = None,
    preview_limit: int = 40,
):
    db, _, _, _, _ = await _get_services()
    documents = await db.list_imaging_documents()
    return match_imaging_documents(
        documents,
        filters={
            "modality": modality,
            "study_description": study_description,
            "study_date": study_date,
            "series_kind": series_kind,
            "series_description": series_description,
            "series_key": series_key,
            "convolution_kernel": convolution_kernel,
            "anatomy_level": anatomy_level,
            "body_part": body_part,
        },
        preview_limit=preview_limit,
    )


@router.post("/documents/imaging/analyze-vision", status_code=202)
async def imaging_analyze_vision(body: ImagingVisionRequest, request: Request):
    db, _, _, _, _ = await _get_services()
    for doc_id in body.document_ids:
        doc = await db.get_document(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
        if doc.get("source_type") != "imaging":
            raise HTTPException(
                status_code=400,
                detail=f"Vision analysis requires imaging documents: {doc.get('title') or doc_id}",
            )
    job = enqueue_vision_job(
        document_ids=body.document_ids,
        requested_by=_actor(request),
    )
    await _audit(
        db,
        request,
        ANALYSIS_REQUESTED,
        resource_type="vision_job",
        resource_id=job["id"],
        metadata={
            "slice_count": len(body.document_ids),
            "document_ids": body.document_ids[:10],
        },
    )
    return {"job": job}


@router.get("/documents/imaging/vision-jobs/{job_id}")
async def imaging_vision_job_status(job_id: str):
    payload = get_vision_job_payload(job_id)
    if payload.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Vision job not found")
    return payload


@router.post("/documents/imaging/vision-jobs/{job_id}/cancel")
async def imaging_vision_job_cancel(job_id: str):
    payload = await cancel_vision_job(job_id)
    if payload.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Vision job not found")
    return payload


@router.get("/documents/imaging/sample")
async def imaging_sample(
    anatomy_level: str | None = None,
    series_kind: str | None = None,
    series_key: str | None = None,
    convolution_kernel: str | None = None,
    count: int = 3,
):
    db, _, _, _, _ = await _get_services()
    documents = await db.list_imaging_documents()
    match = match_imaging_documents(
        documents,
        filters={
            "anatomy_level": anatomy_level,
            "series_kind": series_kind,
            "series_key": series_key,
            "convolution_kernel": convolution_kernel,
        },
        preview_limit=100,
    )
    sample_count = max(1, min(count, 10))
    sampled = sample_document_ids(match["document_ids"], sample_count)
    preview_by_id = {row["id"]: row for row in match.get("preview") or []}
    return {
        "filters": match.get("filters") or {},
        "total_matches": match.get("total") or 0,
        "sample_count": len(sampled),
        "document_ids": sampled,
        "slices": [preview_by_id.get(doc_id) or {"id": doc_id} for doc_id in sampled],
    }


@router.get("/documents")
async def list_documents(
    limit: int = 50,
    offset: int = 0,
    source_type: str | None = None,
):
    db, _, _, _, _ = await _get_services()
    page_size = max(1, min(limit, 100))
    page_offset = max(0, offset)
    normalized_type = source_type.strip().lower() if source_type and source_type.strip() else None
    if normalized_type == "all":
        normalized_type = None

    patient_page = await list_active_patient_documents_page(
        limit=page_size,
        offset=page_offset,
        source_type=normalized_type,
    )
    if patient_page is not None:
        documents = patient_page["documents"]
        catalog = await _source_catalog(db, documents)
        for doc in documents:
            doc["source_info"] = catalog.describe_document(doc)
        return {
            **patient_page,
            "documents": documents,
            "source_legend": catalog.legend(),
        }

    total = await db.count_documents(normalized_type)
    counts_by_type = await db.document_type_counts()
    documents = await db.list_documents(
        limit=page_size,
        offset=page_offset,
        source_type=normalized_type,
    )
    catalog = await _source_catalog(db, documents)
    for doc in documents:
        doc["source_info"] = catalog.describe_document(doc)
    return {
        "documents": documents,
        "total": total,
        "limit": page_size,
        "offset": page_offset,
        "source_type": normalized_type,
        "counts_by_type": counts_by_type,
        "source_legend": catalog.legend(),
    }


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    db, store, _, _, _ = await _get_services()
    doc = await resolve_active_patient_document(doc_id)
    if not doc:
        doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc = await heal_document_paths(store, doc)
    catalog = await _source_catalog(db, [doc])
    doc["source_info"] = catalog.describe_document(doc)
    text = await store.read_extracted_text(doc)
    view = build_document_view(doc)
    return {"document": doc, "extracted_text": text, **view}


@router.get("/documents/{doc_id}/file")
async def get_document_file(doc_id: str):
    db, store, _, _, _ = await _get_services()
    doc = await resolve_active_patient_document(doc_id)
    if not doc:
        doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc = await heal_document_paths(store, doc)
    path = resolve_document_file_path(doc)
    if not path:
        raise HTTPException(status_code=404, detail="Original file not available")

    filename = path.name.split("_", 1)[-1] if "_" in path.name else path.name
    meta = doc.get("metadata") or {}
    if meta.get("original_filename"):
        filename = meta["original_filename"]

    return FileResponse(
        path,
        media_type=guess_media_type(filename, doc.get("source_type")),
        filename=filename,
        content_disposition_type="inline",
    )


@router.get("/documents/{doc_id}/preview")
async def get_document_preview(doc_id: str):
    db, store, _, _, _ = await _get_services()
    doc = await resolve_active_patient_document(doc_id)
    if not doc:
        doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc = await heal_document_paths(store, doc)
    path = resolve_document_file_path(doc)
    if not path:
        raise HTTPException(status_code=404, detail="Original file not available")
    if not is_dicom_document(doc):
        raise HTTPException(status_code=400, detail="Preview is only available for DICOM files")

    try:
        png_bytes = render_dicom_preview_png(file_path=path)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not render DICOM preview: {exc}",
        ) from exc

    return FastAPIResponse(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, request: Request):
    db, store, _, _, _ = await _get_services()
    patient_doc = await resolve_active_patient_document(doc_id)
    if patient_doc and not patient_doc.get("is_active_case"):
        raise HTTPException(
            status_code=403,
            detail="Switch to that focus case to delete this document",
        )
    doc = await db.get_document(doc_id)
    deleted = await store.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc:
        await _audit(
            db,
            request,
            DOCUMENT_DELETED,
            resource_type="document",
            resource_id=doc_id,
            metadata={
                "title": doc.get("title"),
                "source_type": doc.get("source_type"),
                "source_uri": doc.get("source_uri"),
            },
        )
    return {"deleted": True}


@router.patch("/documents/{doc_id}/citation")
async def update_document_citation(
    doc_id: str,
    body: DocumentCitationUpdateRequest,
    request: Request,
):
    db, _, _, _, _ = await _get_services()
    patient_doc = await resolve_active_patient_document(doc_id)
    if patient_doc and not patient_doc.get("is_active_case"):
        raise HTTPException(
            status_code=403,
            detail="Switch to that focus case to edit this document",
        )
    existing = await db.get_document(doc_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    old_display = existing.get("citation_display_name") or existing.get("title")
    doc = await db.update_document_citation_display_name(doc_id, body.citation_display_name)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    new_display = doc.get("citation_display_name") or doc.get("title")
    if new_display != old_display:
        await _audit(
            db,
            request,
            DOCUMENT_CITATION_UPDATED,
            resource_type="document",
            resource_id=doc_id,
            metadata={
                "title": doc.get("title"),
                "source_type": doc.get("source_type"),
                "old_display_name": old_display,
                "new_display_name": new_display,
            },
        )
    catalog = await _source_catalog(db)
    doc["source_info"] = catalog.describe_document(doc)
    return {"document": doc}


@router.post("/documents/{doc_id}/reextract")
async def reextract_document(doc_id: str, request: Request):
    """Re-run PDF text extraction / OCR for a stored PDF (any case for the active patient)."""
    from app.services.clinical_report_handling import open_store_for_patient_document

    db, store, _, _, _ = await _get_services()
    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    target_store = store
    doc = None

    if patient_id:
        opened = await open_store_for_patient_document(
            patient_id,
            doc_id,
            active_case_id=ctx.get("case_id"),
        )
        if opened:
            target_store, doc = opened
    if doc is None:
        doc = await db.get_document(doc_id)
        target_store = store
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if (doc.get("source_type") or "").lower() != "pdf":
        raise HTTPException(status_code=400, detail="Only PDF documents can be re-extracted")
    try:
        updated = await reextract_pdf_document(target_store, doc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc_id,
        metadata={
            "action": "reextract",
            "title": updated.get("title"),
            "extraction_method": (updated.get("metadata") or {}).get("extraction_method"),
            "needs_ocr": (updated.get("metadata") or {}).get("needs_ocr"),
            "clinical_report_kind": (updated.get("metadata") or {}).get("clinical_report_kind"),
        },
    )
    updated, lab_import = await _finalize_clinical_report_document(target_store, updated)
    catalog = await _source_catalog(target_store.db)
    updated["source_info"] = catalog.describe_document(updated)
    text = await target_store.read_extracted_text(updated)
    payload: dict[str, Any] = {
        "document": updated,
        "extracted_preview": (text or "")[:500],
        "handling": updated.get("handling"),
        **build_document_view(updated),
    }
    if lab_import is not None:
        payload["lab_import"] = lab_import
    return payload


@router.post("/documents/{doc_id}/replace-file")
async def replace_document_file(
    doc_id: str,
    request: Request,
    file: UploadFile = File(...),
):
    """Re-upload the original PDF when the stored file is missing or corrupt."""
    from app.services.clinical_report_handling import open_store_for_patient_document

    db, store, _, _, _ = await _get_services()
    ctx = get_active_context()
    patient_id = ctx.get("patient_id")
    target_store = store
    doc = None

    if patient_id:
        opened = await open_store_for_patient_document(
            patient_id,
            doc_id,
            active_case_id=ctx.get("case_id"),
        )
        if opened:
            target_store, doc = opened
    if doc is None:
        doc = await db.get_document(doc_id)
        target_store = store
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    filename = file.filename or "upload.pdf"
    lower = filename.lower()
    source_type = (doc.get("source_type") or "").lower()
    if source_type == "pdf" and not lower.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF to replace this document")

    # Remove stale file at the old path if present
    old = resolve_document_file_path(doc, prefer_dirs=[target_store.documents_dir])
    if old and old.is_file():
        try:
            old.unlink()
        except OSError:
            pass

    saved = await target_store.save_raw_file(doc_id, filename, content)
    meta = dict(doc.get("metadata") or {})
    meta["original_filename"] = filename
    meta["file_size"] = len(content)
    meta["replaced_file_at"] = datetime.now(timezone.utc).isoformat()
    await target_store.db.update_document_paths(doc_id, file_path=str(saved))
    updated = await target_store.db.update_document_metadata(doc_id, metadata=meta)
    updated = updated or {**doc, "file_path": str(saved), "metadata": meta}

    lab_import = None
    if source_type == "pdf" or lower.endswith(".pdf"):
        try:
            updated = await reextract_pdf_document(target_store, updated)
            updated, lab_import = await _finalize_clinical_report_document(target_store, updated)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc_id,
        metadata={
            "action": "replace_file",
            "title": updated.get("title"),
            "original_filename": filename,
            "extraction_method": (updated.get("metadata") or {}).get("extraction_method"),
        },
    )
    catalog = await _source_catalog(target_store.db)
    updated["source_info"] = catalog.describe_document(updated)
    text = await target_store.read_extracted_text(updated)
    payload: dict[str, Any] = {
        "document": updated,
        "extracted_preview": (text or "")[:500],
        "handling": updated.get("handling"),
        **build_document_view(updated),
    }
    if lab_import is not None:
        payload["lab_import"] = lab_import
    return payload


@router.post("/ingest/text")
async def ingest_text_route(body: TextIngestRequest, request: Request):
    db, store, _, _, _ = await _get_services()
    doc = await ingest_text(
        store,
        title=body.title,
        content=body.content,
        metadata=body.metadata,
    )
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "source_uri": doc.get("source_uri"),
        },
    )
    return {"document": doc}


@router.post("/ingest/url")
async def ingest_url_route(body: UrlIngestRequest, request: Request):
    db, store, _, _, _ = await _get_services()
    try:
        doc = await ingest_url(
            store,
            url=body.url,
            title=body.title,
            metadata=body.metadata,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "source_uri": doc.get("source_uri"),
        },
    )
    return {"document": doc}


@router.post("/ingest/youtube")
async def ingest_youtube_route(body: YoutubeIngestRequest, request: Request):
    from app.ingest.youtube import ingest_youtube

    db, store, _, _, _ = await _get_services()
    try:
        doc = await ingest_youtube(
            store,
            url=body.url,
            title=body.title,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "source_uri": doc.get("source_uri"),
        },
    )
    return {"document": doc}


@router.post("/ingest/facebook")
async def ingest_facebook_route(body: FacebookIngestRequest, request: Request):
    from app.ingest.facebook import ingest_facebook

    db, store, _, _, _ = await _get_services()
    try:
        doc = await ingest_facebook(
            store,
            url=body.url,
            title=body.title,
            notes=body.notes,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "source_uri": doc.get("source_uri"),
        },
    )
    return {"document": doc}


@router.post("/ingest/pdf")
async def ingest_pdf_route(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
):
    db, store, _, _, _ = await _get_services()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    filename = file.filename or "upload.pdf"
    doc = await ingest_pdf_file(
        store,
        filename=filename,
        content=content,
        title=title or filename,
    )
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "source_uri": doc.get("source_uri"),
            "original_filename": filename,
            "clinical_report_kind": (doc.get("metadata") or {}).get("clinical_report_kind"),
        },
    )
    doc, lab_import = await _finalize_clinical_report_document(store, doc)
    catalog = await _source_catalog(db)
    doc["source_info"] = catalog.describe_document(doc)
    payload: dict[str, Any] = {"document": doc, "handling": doc.get("handling")}
    if lab_import is not None:
        payload["lab_import"] = lab_import
    return payload


@router.post("/ingest/video")
async def ingest_video_route(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    notes: str | None = Form(default=None),
):
    db, store, _, _, _ = await _get_services()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    filename = file.filename or "upload.mp4"
    doc = await ingest_video(
        store,
        filename=filename,
        content=content,
        title=title or filename,
        notes=notes,
    )
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "source_uri": doc.get("source_uri"),
            "original_filename": filename,
        },
    )
    return {"document": doc}


@router.post("/ingest/imaging")
async def ingest_imaging_route(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    title_prefix: str | None = Form(default=None),
    relative_path: str | None = Form(default=None),
    notes: str | None = Form(default=None),
):
    db, store, _, _, _ = await _get_services()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    filename = Path(file.filename or "imaging.dcm").name
    if not is_allowed_imaging_upload(filename, content):
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported imaging file type. Use DICOM (.dcm), JPEG, PNG, TIFF, NIfTI, or ZIP. "
                "Extensionless DICOM slices from a study folder are accepted when the file content is valid DICOM."
            ),
        )

    try:
        doc = await ingest_imaging_file(
            store,
            filename=filename,
            content=content,
            title=title,
            relative_path=relative_path or file.filename,
            title_prefix=title_prefix,
            notes=notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "source_uri": doc.get("source_uri"),
            "original_filename": filename,
            "relative_path": relative_path or file.filename,
        },
    )
    return {"document": doc}


@router.post("/analyze", status_code=202)
async def analyze(body: AnalyzeRequest, request: Request):
    if not body.query.strip() and not body.include_baseline_assessment:
        raise HTTPException(
            status_code=400,
            detail="Provide a query or set include_baseline_assessment=true",
        )
    job_type = "baseline" if body.include_baseline_assessment and not body.query.strip() else "query"
    db, _, _, _, _ = await _get_services()
    build_on_id = None
    if job_type == "baseline":
        prior = await db.get_latest_analysis()
        if prior and prior.get("analysis_type") == "baseline":
            build_on_id = prior["id"]
    chat_obs_ids = body.chat_observation_ids
    if chat_obs_ids is None:
        pending = await db.list_chat_observations(
            include_in_analysis_only=True,
            pending_only=True,
        )
        chat_obs_ids = [o["id"] for o in pending]
    document_ids = body.document_ids
    if chat_obs_ids:
        obs_list = await resolve_observations_for_analysis(
            db, observation_ids=chat_obs_ids
        )
        extra_doc_ids = await observation_library_document_ids(obs_list)
        if extra_doc_ids:
            merged = list(document_ids or [])
            seen = set(merged)
            for doc_id in extra_doc_ids:
                if doc_id not in seen:
                    merged.append(doc_id)
                    seen.add(doc_id)
            document_ids = merged if merged else document_ids
    try:
        job = await enqueue_analysis_job(
            job_type=job_type,
            query=body.query,
            document_ids=document_ids,
            include_baseline_assessment=body.include_baseline_assessment,
            assessment_guidance=body.assessment_guidance,
            requested_by=_actor(request),
            build_on_analysis_id=build_on_id,
            chat_observation_ids=chat_obs_ids,
        )
    except ActiveAnalysisJobError as exc:
        raise HTTPException(
            status_code=409,
            detail="An analysis is already running",
        ) from exc
    await _audit(
        db,
        request,
        ANALYSIS_REQUESTED,
        resource_type="analysis_job",
        resource_id=job["id"],
        metadata={
            "job_type": job_type,
            "query_preview": preview_text(body.query or "Baseline assessment"),
            "document_count": len(body.document_ids or []),
            "guidance_preview": preview_text(body.assessment_guidance or ""),
            "save_as_draft": job_type == "query",
            "build_on_analysis_id": build_on_id,
        },
    )
    return {"job": job, "save_as_draft": job_type == "query"}


@router.post("/analyze/summarize", status_code=202)
async def summarize(request: Request, document_ids: list[str] | None = None):
    try:
        job = await enqueue_analysis_job(
            job_type="summarize",
            query="Summarize all stored documents",
            document_ids=document_ids,
            requested_by=_actor(request),
        )
    except ActiveAnalysisJobError as exc:
        raise HTTPException(
            status_code=409,
            detail="An analysis is already running",
        ) from exc
    db, _, _, _, _ = await _get_services()
    await _audit(
        db,
        request,
        ANALYSIS_REQUESTED,
        resource_type="analysis_job",
        resource_id=job["id"],
        metadata={
            "job_type": "summarize",
            "query_preview": "Summarize all stored documents",
            "document_count": len(document_ids or []),
        },
    )
    return {"job": job}


@router.get("/analyze/jobs/active")
async def active_analysis_job():
    db, _, _, _, _ = await _get_services()
    job = await db.get_active_analysis_job()
    if not job:
        return {"job": None}
    payload = await get_job_payload(job["id"])
    return {"job": payload}


@router.get("/analyze/jobs/{job_id}")
async def get_analysis_job(job_id: str):
    payload = await get_job_payload(job_id)
    if not payload:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return {"job": payload}


@router.post("/analyze/jobs/{job_id}/cancel")
async def cancel_analysis_job_route(job_id: str, request: Request):
    payload = await cancel_analysis_job(job_id)
    if not payload:
        raise HTTPException(status_code=404, detail="No running analysis job found")
    db, _, _, _, _ = await _get_services()
    await _audit(
        db,
        request,
        ANALYSIS_FAILED,
        resource_type="analysis_job",
        resource_id=job_id,
        metadata={
            "job_id": job_id,
            "job_type": payload.get("job_type"),
            "cancelled": True,
        },
    )
    return {"job": payload}


@router.get("/options-chat/starters")
async def options_chat_starters():
    return {"starters": OPTIONS_STARTER_PROMPTS}


@router.get("/options-chat/sessions")
async def list_options_chat_sessions(limit: int = 50):
    db, _, _, _, _ = await _get_services()
    return {"sessions": await db.list_chat_sessions(limit=limit)}


@router.post("/options-chat/sessions")
async def create_options_chat_session(body: OptionsChatSessionRequest, request: Request):
    db, store, llm, _, _ = await _get_services()
    chat = _options_chat_service(db, store, llm)
    session = await chat.create_session(
        document_ids=body.document_ids,
        include_latest_assessment=body.include_latest_assessment,
        title=body.title,
        created_by=_actor(request),
    )
    await _audit(
        db,
        request,
        ANALYSIS_REQUESTED,
        resource_type="chat_session",
        resource_id=session["id"],
        metadata={
            "job_type": "options_chat",
            "document_count": len(body.document_ids or []),
            "include_latest_assessment": body.include_latest_assessment,
        },
    )
    return {"session": session, "messages": []}


@router.get("/options-chat/sessions/{session_id}")
async def get_options_chat_session(session_id: str):
    db, store, llm, _, _ = await _get_services()
    chat = _options_chat_service(db, store, llm)
    bundle = await chat.get_session_bundle(session_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return bundle


@router.delete("/options-chat/sessions/{session_id}")
async def delete_options_chat_session(session_id: str, request: Request):
    db, _, _, _, _ = await _get_services()
    deleted = await db.delete_chat_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chat session not found")
    await _audit(
        db,
        request,
        ANALYSIS_DRAFT_DISCARDED,
        resource_type="chat_session",
        resource_id=session_id,
        metadata={"job_type": "options_chat"},
    )
    return {"deleted": True}


@router.post("/options-chat/sessions/{session_id}/messages")
async def post_options_chat_message(
    session_id: str,
    body: OptionsChatMessageRequest,
    request: Request,
):
    db, store, llm, _, _ = await _get_services()
    chat = _options_chat_service(db, store, llm)
    existing = await db.get_chat_session(session_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Chat session not found")

    if body.stream:
        events = await chat.send_message(session_id, body.content, stream=True)

        async def event_stream() -> AsyncIterator[bytes]:
            try:
                async for event in events:  # type: ignore[union-attr]
                    yield f"data: {json.dumps(event)}\n\n".encode("utf-8")
            except Exception as exc:
                payload = {"type": "error", "error": str(exc)}
                yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = await chat.send_message(session_id, body.content, stream=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Chat failed: {exc}") from exc

    return result


@router.get("/options-chat/observations")
async def list_chat_observations(
    session_id: str | None = None,
    pending_only: bool = False,
):
    db, _, _, _, _ = await _get_services()
    observations = await db.list_chat_observations(
        session_id=session_id,
        pending_only=pending_only,
    )
    pending_count = await db.count_pending_chat_observations()
    return {"observations": observations, "pending_count": pending_count}


@router.post("/options-chat/observations")
async def create_chat_observation(body: ChatObservationCreateRequest, request: Request):
    db, _, _, _, _ = await _get_services()
    try:
        observation = await db.create_chat_observation(
            session_id=body.session_id,
            message_id=body.message_id,
            excerpt=body.excerpt,
            title=body.title,
            include_in_analysis=body.include_in_analysis,
            created_by=_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"observation": observation}


@router.patch("/options-chat/observations/{observation_id}")
async def update_chat_observation(
    observation_id: str,
    body: ChatObservationUpdateRequest,
):
    db, _, _, _, _ = await _get_services()
    updated = await db.update_chat_observation(
        observation_id,
        title=body.title,
        include_in_analysis=body.include_in_analysis,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"observation": updated}


@router.delete("/options-chat/observations/{observation_id}")
async def delete_chat_observation(observation_id: str):
    db, _, _, _, _ = await _get_services()
    deleted = await db.delete_chat_observation(observation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Observation not found")
    return {"deleted": True}


@router.post("/options-chat/observations/{observation_id}/save-to-library")
async def save_chat_observation_to_library(observation_id: str, request: Request):
    db, store, _, _, _ = await _get_services()
    try:
        result = await save_observation_to_library(store, db, observation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = result["document"]
    await _audit(
        db,
        request,
        DOCUMENT_CREATED,
        resource_type="document",
        resource_id=doc["id"],
        metadata={
            "title": doc.get("title"),
            "source_type": doc.get("source_type"),
            "chat_observation_id": observation_id,
        },
    )
    return result


@router.post("/options-chat/apply-to-home", status_code=202)
async def apply_chat_to_home(body: ApplyChatToHomeRequest, request: Request):
    """Pin a chat reply (or latest assistant message) and enqueue a Home baseline update."""
    db, _, _, _, _ = await _get_services()
    session = await db.get_chat_session(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = await db.list_chat_messages(body.session_id)
    assistant_messages = [m for m in messages if m.get("role") == "assistant" and (m.get("content") or "").strip()]
    if not assistant_messages:
        raise HTTPException(status_code=400, detail="No assistant reply to apply yet")

    target = None
    if body.message_id:
        target = next((m for m in assistant_messages if m.get("id") == body.message_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Message not found in this session")
    else:
        target = assistant_messages[-1]

    excerpt = (target.get("content") or "").strip()
    if len(excerpt) > 50000:
        excerpt = excerpt[:50000]
    title = f"Chat → Home · {(session.get('title') or 'AI Chat')[:80]}"
    try:
        observation = await db.create_chat_observation(
            session_id=body.session_id,
            message_id=target.get("id"),
            excerpt=excerpt,
            title=title,
            include_in_analysis=True,
            created_by=_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    prior = await db.get_latest_analysis()
    build_on_id = None
    if prior and prior.get("analysis_type") == "baseline":
        build_on_id = prior["id"]

    guidance = (body.assessment_guidance or "").strip()
    if not guidance:
        guidance = (
            "Incorporate the pinned chat analysis into the Home assessment. "
            "Reconcile with existing sources; prefer chart documents over chat when they conflict. "
            "Preserve clinically important findings from the chat excerpt with source attribution."
        )

    document_ids = body.document_ids
    if document_ids is None:
        session_docs = session.get("document_ids") or []
        document_ids = session_docs or None

    try:
        job = await enqueue_analysis_job(
            job_type="baseline",
            query="",
            document_ids=document_ids,
            include_baseline_assessment=True,
            assessment_guidance=guidance,
            requested_by=_actor(request),
            build_on_analysis_id=build_on_id,
            chat_observation_ids=[observation["id"]],
        )
    except ActiveAnalysisJobError as exc:
        raise HTTPException(
            status_code=409,
            detail="An analysis is already running",
        ) from exc

    await _audit(
        db,
        request,
        ANALYSIS_REQUESTED,
        resource_type="analysis_job",
        resource_id=job["id"],
        metadata={
            "job_type": "baseline",
            "source": "options_chat_apply_to_home",
            "session_id": body.session_id,
            "message_id": target.get("id"),
            "observation_id": observation["id"],
        },
    )
    return {
        "job": job,
        "observation": observation,
        "message": "Home assessment update started from chat",
    }


@router.get("/custom-tasks")
async def list_custom_tasks():
    db, _, _, _, _ = await _get_services()
    active = await db.get_active_analysis_job()
    active_payload = None
    if active and active.get("job_type") == "query":
        active_payload = await get_job_payload(active["id"])

    jobs = await db.list_analysis_jobs(job_type="query", limit=20)
    job_payloads: list[dict[str, Any]] = []
    for job in jobs:
        payload = await get_job_payload(job["id"])
        if payload:
            job_payloads.append(payload)

    drafts = await db.list_draft_analyses(limit=50)
    return {
        "active_job": active_payload,
        "jobs": job_payloads,
        "drafts": drafts,
    }


@router.get("/analyses/drafts")
async def list_draft_analyses(limit: int = 50):
    db, _, _, _, _ = await _get_services()
    return {"drafts": await db.list_draft_analyses(limit=limit)}


@router.post("/analyses/{analysis_id}/refine", status_code=202)
async def refine_draft_analysis(
    analysis_id: str,
    body: RefineDraftRequest,
    request: Request,
):
    db, _, _, _, _ = await _get_services()
    existing = await db.get_analysis_by_id(analysis_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if existing.get("record_status") != "draft":
        raise HTTPException(status_code=400, detail="Only draft analyses can be refined")
    if existing.get("analysis_type") != "query":
        raise HTTPException(status_code=400, detail="Only custom task drafts can be refined")

    query = body.query.strip()
    refinement = body.refinement.strip()
    if not refinement and query == (existing.get("query") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Change the question or add refinement instructions",
        )

    try:
        job = await enqueue_refinement_job(
            analysis_id=analysis_id,
            query=query,
            refinement=refinement,
            document_ids=body.document_ids,
            requested_by=_actor(request),
        )
    except ActiveAnalysisJobError as exc:
        raise HTTPException(
            status_code=409,
            detail="An analysis is already running",
        ) from exc

    await _audit(
        db,
        request,
        ANALYSIS_REQUESTED,
        resource_type="analysis",
        resource_id=analysis_id,
        metadata={
            "job_id": job["id"],
            "job_type": "query",
            "refinement": True,
            "refine_analysis_id": analysis_id,
            "query_preview": preview_text(query),
            "refinement_preview": preview_text(refinement),
            "document_count": len(body.document_ids or existing.get("document_ids") or []),
            "save_as_draft": True,
        },
    )
    return {"job": job, "save_as_draft": True, "refine_analysis_id": analysis_id}


@router.post("/analyses/{analysis_id}/promote")
async def promote_draft_analysis(analysis_id: str, request: Request):
    db, _, _, _, _ = await _get_services()
    existing = await db.get_analysis_by_id(analysis_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if existing.get("record_status") != "draft":
        raise HTTPException(status_code=400, detail="Only draft analyses can be promoted")

    analysis = await db.promote_analysis(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    await _audit(
        db,
        request,
        ANALYSIS_PROMOTED,
        resource_type="analysis",
        resource_id=analysis_id,
        metadata={
            "analysis_id": analysis_id,
            "analysis_type": analysis.get("analysis_type"),
            "query_preview": preview_text(analysis.get("query")),
            "record_status": "official",
        },
    )
    return {"analysis": analysis}


@router.post("/analyses/{analysis_id}/discard")
async def discard_draft_analysis(analysis_id: str, request: Request):
    db, _, _, _, _ = await _get_services()
    existing = await db.get_analysis_by_id(analysis_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if existing.get("record_status") != "draft":
        raise HTTPException(status_code=400, detail="Only draft analyses can be discarded")

    discarded = await db.discard_draft_analysis(analysis_id)
    if not discarded:
        raise HTTPException(status_code=404, detail="Draft not found")

    await _audit(
        db,
        request,
        ANALYSIS_DRAFT_DISCARDED,
        resource_type="analysis",
        resource_id=analysis_id,
        metadata={
            "analysis_id": analysis_id,
            "query_preview": preview_text(existing.get("query")),
        },
    )
    return {"discarded": True}


@router.get("/analyses")
async def list_analyses(limit: int = 50):
    db, _, _, _, _ = await _get_services()
    return {"analyses": await db.list_analyses(limit=limit)}


@router.get("/analyses/latest")
async def latest_analysis():
    db, _, _, _, _ = await _get_services()
    return {"analysis": await db.get_latest_analysis()}


@router.get("/analyses/latest/export.pdf")
async def export_latest_assessment_pdf(request: Request):
    db, _, _, _, _ = await _get_services()
    analysis = await db.get_latest_analysis()
    if not analysis or not (analysis.get("response") or analysis.get("executive_summary")):
        raise HTTPException(status_code=404, detail="No assessment available to export")
    return await _export_analysis_pdf_response(db, request, analysis)


async def _export_analysis_pdf_response(
    db: Database,
    request: Request,
    analysis: dict[str, Any],
) -> FastAPIResponse:
    ctx = get_active_context()
    # Prefer identity pinned on the analysis so a live patient switch cannot relabel the PDF.
    patient_id = analysis.get("patient_id") or ctx.get("patient_id")
    case_id = analysis.get("case_id") or ctx.get("case_id")
    patient_label = analysis.get("patient_label") or ctx.get("patient_label")
    case_label = analysis.get("case_label") or ctx.get("case_label")

    context_db = db
    if analysis.get("patient_id") and analysis.get("case_id"):
        from app.services.case_manager import _case_dir

        case_db_path = _case_dir(analysis["patient_id"], analysis["case_id"]) / "beatit.db"
        if case_db_path.is_file() and str(case_db_path) != str(db.db_path):
            context_db = Database(db_path=case_db_path)

    patient_context = await context_db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
    catalog = await _source_catalog(context_db)
    profile = get_patient_profile(patient_id) if patient_id else None
    diagnostic_series = group_diagnostics_for_charts(profile) if profile else []
    milestones = all_chart_milestones(profile) if profile else []
    sub_bits: list[str] = []
    if profile:
        age = age_years_from_dob(profile.get("date_of_birth"))
        if age is not None:
            sub_bits.append(f"Age {age}")
        if profile.get("gender"):
            sub_bits.append(str(profile["gender"]))
    if case_label:
        sub_bits.append(f"Case: {case_label}")
    pdf_bytes = build_assessment_pdf(
        analysis,
        patient_context=patient_context,
        catalog=catalog,
        diagnostic_series=diagnostic_series,
        patient_label=patient_label,
        patient_subline=" · ".join(sub_bits) if sub_bits else None,
        milestones=milestones,
    )
    exported_at = datetime.now(timezone.utc)
    filename = assessment_pdf_filename(analysis, exported_at=exported_at)
    await _audit(
        db,
        request,
        PDF_EXPORTED,
        resource_type="analysis",
        resource_id=analysis.get("id"),
        metadata={
            "analysis_id": analysis.get("id"),
            "filename": filename,
            "analysis_type": analysis.get("analysis_type"),
            "record_status": analysis.get("record_status"),
            "created_by": analysis.get("created_by"),
            "patient_id": patient_id,
            "case_id": case_id,
            "patient_label": patient_label,
            "case_label": case_label,
        },
    )
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/analyses/{analysis_id}/export.pdf")
async def export_analysis_pdf(analysis_id: str, request: Request):
    db, _, _, _, _ = await _get_services()
    analysis = await db.get_analysis_by_id(analysis_id)
    if not analysis or not (analysis.get("response") or analysis.get("executive_summary")):
        raise HTTPException(status_code=404, detail="Analysis not found or empty")
    return await _export_analysis_pdf_response(db, request, analysis)


@router.patch("/analyses/{analysis_id}/annotations")
async def update_analysis_annotations(
    analysis_id: str,
    body: AnalysisAnnotationsUpdate,
    request: Request,
):
    db, _, _, _, _ = await _get_services()
    existing = await db.get_analysis_by_id(analysis_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Analysis not found")
    if existing.get("record_status") == "discarded":
        raise HTTPException(status_code=400, detail="Discarded analyses cannot be edited")

    if (
        body.annotation_title is None
        and body.annotation_header is None
        and body.annotation_notes is None
    ):
        raise HTTPException(status_code=400, detail="Nothing to update")

    analysis = await db.update_analysis_annotations(
        analysis_id,
        annotation_title=body.annotation_title,
        annotation_header=body.annotation_header,
        annotation_notes=body.annotation_notes,
    )
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")

    await _audit(
        db,
        request,
        ANALYSIS_ANNOTATIONS_UPDATED,
        resource_type="analysis",
        resource_id=analysis_id,
        metadata={
            "analysis_id": analysis_id,
            "annotation_title": analysis.get("annotation_title"),
            "query_preview": preview_text(existing.get("query")),
            "created_by": analysis.get("created_by"),
            "record_status": analysis.get("record_status"),
        },
    )
    return {"analysis": analysis}


@router.get("/analyses/{analysis_id}")
async def get_analysis(analysis_id: str):
    db, _, _, _, _ = await _get_services()
    analysis = await db.get_analysis_by_id(analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"analysis": analysis}


@router.get("/open-items/{open_item_id}")
async def get_open_item(open_item_id: str):
    db, _, _, _, _ = await _get_services()
    item = await db.get_open_item(open_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Open item not found")
    return {"open_item": item}


@router.post("/open-items/{open_item_id}/investigate")
async def investigate_open_item(
    open_item_id: str,
    request: Request,
    body: InvestigateOpenItemRequest | None = None,
):
    db, _, _, _, investigation = await _get_services()
    guidance = body.guidance if body else ""
    existing = await db.get_open_item(open_item_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Open item not found")

    await _audit(
        db,
        request,
        OPEN_ITEM_INVESTIGATION_STARTED,
        resource_type="open_item",
        resource_id=open_item_id,
        metadata={
            "item": existing.get("item"),
            "item_type": existing.get("item_type"),
            "guidance_preview": preview_text(guidance),
        },
    )

    try:
        item = await investigation.investigate_open_item(open_item_id, guidance=guidance)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        await _audit(
            db,
            request,
            OPEN_ITEM_INVESTIGATION_FAILED,
            resource_type="open_item",
            resource_id=open_item_id,
            metadata={
                "item": existing.get("item"),
                "error_preview": preview_text(str(exc), 300),
            },
        )
        raise HTTPException(
            status_code=502,
            detail=f"Investigation failed: {exc}",
        ) from exc

    await _audit(
        db,
        request,
        OPEN_ITEM_INVESTIGATION_DRAFT_CREATED,
        resource_type="open_item",
        resource_id=open_item_id,
        metadata={
            "item": item.get("item"),
            "item_type": item.get("item_type"),
            "model": item.get("investigation_draft_model"),
            "guidance_preview": preview_text(guidance),
        },
    )
    return {"open_item": item}


@router.post("/open-items/{open_item_id}/investigate/accept")
async def accept_open_item_investigation(
    open_item_id: str,
    request: Request,
    body: AcceptInvestigationRequest | None = None,
):
    db, _, _, _, _ = await _get_services()
    item = await db.get_open_item(open_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Open item not found")
    if not item.get("investigation_draft_response") and not (
        body and body.edited_response
    ):
        raise HTTPException(status_code=400, detail="No investigation draft to accept")

    edited = body.edited_response.strip() if body and body.edited_response else None
    updated = await db.accept_open_item_investigation(open_item_id, response=edited)
    await _audit(
        db,
        request,
        OPEN_ITEM_INVESTIGATION_ACCEPTED,
        resource_type="open_item",
        resource_id=open_item_id,
        metadata={
            "item": item.get("item"),
            "item_type": item.get("item_type"),
            "edited": bool(edited),
            "model": updated.get("investigation_model") if updated else item.get("investigation_draft_model"),
        },
    )
    return {"open_item": updated}


@router.post("/open-items/{open_item_id}/investigate/discard")
async def discard_open_item_investigation(open_item_id: str, request: Request):
    db, _, _, _, _ = await _get_services()
    item = await db.get_open_item(open_item_id)
    updated = await db.discard_open_item_investigation_draft(open_item_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Open item not found")
    await _audit(
        db,
        request,
        OPEN_ITEM_INVESTIGATION_DISCARDED,
        resource_type="open_item",
        resource_id=open_item_id,
        metadata={
            "item": item.get("item") if item else None,
            "item_type": item.get("item_type") if item else None,
        },
    )
    return {"open_item": updated}


@router.post("/open-items/{open_item_id}/investigate/comment")
async def comment_open_item_investigation(open_item_id: str, request: Request):
    db, _, _, _, _ = await _get_services()
    item = await db.get_open_item(open_item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Open item not found")
    if not item.get("investigation_draft_response"):
        raise HTTPException(status_code=400, detail="No investigation draft to save")

    updated = await db.add_open_item_investigation_draft_as_comment(open_item_id)
    await _audit(
        db,
        request,
        OPEN_ITEM_INVESTIGATION_DRAFT_COMMENTED,
        resource_type="open_item",
        resource_id=open_item_id,
        metadata={
            "item": item.get("item"),
            "item_type": item.get("item_type"),
        },
    )
    return {"open_item": updated}


@router.patch("/open-items/{open_item_id}")
async def update_open_item(open_item_id: str, body: OpenItemUpdateRequest, request: Request):
    db, _, _, _, _ = await _get_services()
    allowed = {"open", "investigating", "pending_review", "investigated", "resolved", "closed"}
    status = body.status.strip().lower() if body.status else None
    comment = body.comment.strip() if body.comment else None

    if status is None and not comment:
        raise HTTPException(status_code=400, detail="Provide status and/or comment")

    if status is not None and status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Status must be one of: {', '.join(sorted(allowed))}",
        )

    if comment and len(comment) > 5000:
        raise HTTPException(status_code=400, detail="Comment is too long (max 5000 characters)")

    existing = await db.get_open_item(open_item_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Open item not found")

    item = await db.update_open_item(open_item_id, status=status, comment=comment)
    if not item:
        raise HTTPException(status_code=404, detail="Open item not found")

    if comment:
        await _audit(
            db,
            request,
            OPEN_ITEM_COMMENT_ADDED,
            resource_type="open_item",
            resource_id=open_item_id,
            metadata={
                "item": existing.get("item"),
                "item_type": existing.get("item_type"),
                "comment": comment,
            },
        )
    if status is not None and status != existing.get("status"):
        await _audit(
            db,
            request,
            OPEN_ITEM_STATUS_CHANGED,
            resource_type="open_item",
            resource_id=open_item_id,
            metadata={
                "item": existing.get("item"),
                "item_type": existing.get("item_type"),
                "old_status": existing.get("status"),
                "new_status": status,
            },
        )
    return {"open_item": item}


# ------------------------------------------------------------------
# Patient / Case management
# ------------------------------------------------------------------

class CreatePatientRequest(BaseModel):
    label: str


class CreateCaseRequest(BaseModel):
    label: str
    patient_context: str | None = None


class RenameCaseRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)


class ActivateCaseRequest(BaseModel):
    patient_id: str
    case_id: str


@router.get("/patients")
async def api_list_patients():
    patients = list_patients()
    ctx = get_active_context()
    return {"patients": patients, "active": ctx}


@router.post("/patients")
async def api_create_patient(body: CreatePatientRequest):
    patient = create_patient(body.label)
    return {"patient": patient}


@router.delete("/patients/{patient_id}")
async def api_delete_patient(patient_id: str):
    ok = delete_patient(patient_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"ok": True}


@router.get("/patients/{patient_id}/photo")
async def api_get_patient_photo(patient_id: str):
    path = find_patient_photo(patient_id)
    if not path:
        raise HTTPException(status_code=404, detail="No photo")
    suffix = path.suffix.lower()
    media = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    return FileResponse(path, media_type=media.get(suffix, "image/jpeg"), headers={"Cache-Control": "private, max-age=3600"})


@router.post("/patients/{patient_id}/photo")
async def api_set_patient_photo(patient_id: str, file: UploadFile = File(...)):
    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Photo must be 5 MB or smaller")
    try:
        save_patient_photo(patient_id, content, file.content_type or "", file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ctx = get_active_context()
    return {"ok": True, "photo_url": ctx["photo_url"] if ctx.get("patient_id") == patient_id else f"/api/patients/{patient_id}/photo"}


class PatientDemographicsRequest(BaseModel):
    date_of_birth: str | None = None
    gender: str | None = None


class PatientMeasurementRequest(BaseModel):
    recorded_at: str
    height_cm: float | None = None
    weight_kg: float | None = None
    notes: str | None = None


@router.get("/patients/{patient_id}/profile")
async def api_get_patient_profile(patient_id: str):
    patients = list_patients()
    patient = next((p for p in patients if p["id"] == patient_id), None)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    return {
        "profile": profile,
        "patient": {"id": patient["id"], "label": patient["label"]},
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "diagnostic_presets": DIAGNOSTIC_PRESETS,
        "journal_series": group_journal_for_charts(profile),
        "journal_presets": JOURNAL_PRESETS,
        "milestone_presets": MILESTONE_PRESETS,
        "common_remedies": COMMON_REMEDIES,
    }


@router.get("/patients/{patient_id}/diagnostics/export.pdf")
async def export_patient_diagnostics_pdf(patient_id: str, request: Request):
    patients = list_patients()
    patient = next((p for p in patients if p["id"] == patient_id), None)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    series = group_diagnostics_for_charts(profile)
    if not series:
        raise HTTPException(status_code=404, detail="No diagnostics to export")

    sub_bits: list[str] = []
    age = age_years_from_dob(profile.get("date_of_birth"))
    if age is not None:
        sub_bits.append(f"Age {age}")
    if profile.get("gender"):
        sub_bits.append(str(profile["gender"]))
    patient_subline = " · ".join(sub_bits) if sub_bits else None

    exported_at = datetime.now(timezone.utc)
    milestones = all_chart_milestones(profile)
    pdf_bytes = build_diagnostics_pdf(
        series,
        patient_label=patient.get("label"),
        patient_subline=patient_subline,
        milestones=milestones,
    )
    filename = diagnostics_pdf_filename(
        patient_label=patient.get("label"),
        exported_at=exported_at,
    )
    db, _, _, _, _ = await _get_services()
    await _audit(
        db,
        request,
        PDF_EXPORTED,
        resource_type="patient",
        resource_id=patient_id,
        metadata={
            "filename": filename,
            "export_kind": "diagnostics",
            "series_count": len(series),
        },
    )
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/patients/{patient_id}/documents/coverage")
async def api_patient_document_coverage(patient_id: str):
    from app.services.document_coverage import build_document_coverage

    patients = list_patients()
    patient = next((p for p in patients if p["id"] == patient_id), None)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    coverage = await build_document_coverage(patient_id)
    return {
        "patient": {"id": patient["id"], "label": patient["label"]},
        "coverage": coverage,
    }


@router.get("/patients/{patient_id}/documents/coverage/export.pdf")
async def export_patient_document_coverage_pdf(patient_id: str, request: Request):
    from app.services.document_coverage import build_document_coverage

    patients = list_patients()
    patient = next((p for p in patients if p["id"] == patient_id), None)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    coverage = await build_document_coverage(patient_id)

    sub_bits: list[str] = []
    age = age_years_from_dob(profile.get("date_of_birth"))
    if age is not None:
        sub_bits.append(f"Age {age}")
    if profile.get("gender"):
        sub_bits.append(str(profile["gender"]))
    patient_subline = " · ".join(sub_bits) if sub_bits else None

    exported_at = datetime.now(timezone.utc)
    pdf_bytes = build_document_coverage_pdf(
        coverage,
        patient_label=patient.get("label"),
        patient_subline=patient_subline,
    )
    filename = coverage_pdf_filename(
        patient_label=patient.get("label"),
        exported_at=exported_at,
    )
    db, _, _, _, _ = await _get_services()
    await _audit(
        db,
        request,
        PDF_EXPORTED,
        resource_type="patient",
        resource_id=patient_id,
        metadata={
            "filename": filename,
            "export_kind": "document_coverage",
            "document_count": coverage.get("total"),
            "missing_count": coverage.get("missing_count"),
        },
    )
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.put("/patients/{patient_id}/profile")
async def api_update_patient_profile(patient_id: str, body: PatientDemographicsRequest):
    gender = body.gender
    if gender is not None:
        gender = gender.strip()
        allowed = {"", "female", "male", "other", "unknown", "prefer_not_to_say"}
        if gender.lower() not in allowed and gender:
            # allow free-text clinical labels too, but normalize empties
            pass
    dob = body.date_of_birth
    if dob is not None and dob.strip():
        try:
            datetime.fromisoformat(dob.strip()[:10])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date_of_birth must be YYYY-MM-DD") from exc
    profile = update_patient_demographics(
        patient_id,
        date_of_birth=body.date_of_birth,
        gender=body.gender,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


class PatientDiagnosticRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: float
    recorded_at: str
    unit: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=20)


@router.post("/patients/{patient_id}/measurements")
async def api_add_patient_measurement(patient_id: str, body: PatientMeasurementRequest):
    if not body.recorded_at.strip():
        raise HTTPException(status_code=400, detail="recorded_at is required")
    try:
        datetime.fromisoformat(body.recorded_at.strip()[:10])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="recorded_at must be YYYY-MM-DD") from exc
    try:
        entry = add_patient_measurement(
            patient_id,
            recorded_at=body.recorded_at.strip()[:10],
            height_cm=body.height_cm,
            weight_kg=body.weight_kg,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    return {
        "measurement": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
    }


@router.delete("/patients/{patient_id}/measurements/{measurement_id}")
async def api_delete_patient_measurement(patient_id: str, measurement_id: str):
    ok = delete_patient_measurement(patient_id, measurement_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Measurement not found")
    profile = get_patient_profile(patient_id)
    return {"ok": True, "profile": profile, "diagnostic_series": group_diagnostics_for_charts(profile)}


@router.post("/patients/{patient_id}/diagnostics")
async def api_add_patient_diagnostic(patient_id: str, body: PatientDiagnosticRequest):
    if not body.recorded_at.strip():
        raise HTTPException(status_code=400, detail="recorded_at is required")
    try:
        datetime.fromisoformat(body.recorded_at.strip()[:10])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="recorded_at must be YYYY-MM-DD") from exc
    try:
        entry = add_patient_diagnostic(
            patient_id,
            name=body.name,
            value=body.value,
            recorded_at=body.recorded_at.strip()[:10],
            unit=body.unit,
            notes=body.notes,
            category=body.category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    return {
        "diagnostic": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
    }


@router.delete("/patients/{patient_id}/diagnostics/{diagnostic_id}")
async def api_delete_patient_diagnostic(patient_id: str, diagnostic_id: str):
    ok = delete_patient_diagnostic(patient_id, diagnostic_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Diagnostic reading not found")
    profile = get_patient_profile(patient_id)
    return {
        "ok": True,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


class PatientDiagnosticConfirmItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    value: float
    recorded_at: str = Field(min_length=8, max_length=20)
    unit: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=20)
    source_document_id: str | None = Field(default=None, max_length=120)


class PatientDiagnosticConfirmRequest(BaseModel):
    diagnostics: list[PatientDiagnosticConfirmItem] = Field(default_factory=list, max_length=120)
    source_document_id: str | None = Field(default=None, max_length=120)


class PatientDiagnosticImportFromDocRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=120)


@router.post("/patients/{patient_id}/diagnostics/import")
async def api_import_patient_diagnostics(
    patient_id: str,
    file: UploadFile = File(...),
):
    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    content = await file.read()
    try:
        result = await propose_diagnostics_from_upload(
            patient_id,
            content,
            content_type=file.content_type,
            filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/patients/{patient_id}/diagnostics/import/from-document")
async def api_import_patient_diagnostics_from_document(
    patient_id: str,
    body: PatientDiagnosticImportFromDocRequest,
):
    from app.services.clinical_report_handling import open_store_for_patient_document

    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    db, store, _, _, _ = await _get_services()
    doc_id = body.document_id.strip()
    doc = None
    target_store = store
    opened = await open_store_for_patient_document(patient_id, doc_id)
    if opened:
        target_store, doc = opened
    if doc is None:
        doc = await db.get_document(doc_id)
        target_store = store
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    source_type = str(doc.get("source_type") or "").lower()
    if source_type not in {"pdf"} and not str(doc.get("file_path") or "").lower().endswith(
        (".pdf", ".jpg", ".jpeg", ".png", ".webp")
    ):
        # Allow image-like uploads stored as pdf source or raw image path
        meta = doc.get("metadata") or {}
        orig = str(meta.get("original_filename") or "").lower()
        if not orig.endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
            raise HTTPException(
                status_code=400,
                detail="Import to Labs supports PDF or image lab reports",
            )
    text = await target_store.read_extracted_text(doc)
    try:
        result = await propose_diagnostics_from_document(
            patient_id,
            doc,
            extracted_text=text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/patients/{patient_id}/diagnostics/import/confirm")
async def api_confirm_patient_diagnostics_import(
    patient_id: str,
    body: PatientDiagnosticConfirmRequest,
):
    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    if not body.diagnostics:
        raise HTTPException(status_code=400, detail="Select at least one reading to add")
    added: list[dict[str, Any]] = []
    errors: list[str] = []
    default_doc_id = (body.source_document_id or "").strip() or None
    for raw in body.diagnostics:
        clamped = clamp_proposed_diagnostic(raw.model_dump())
        if clamped is None:
            errors.append("Skipped an invalid row")
            continue
        if not clamped.get("recorded_at"):
            errors.append(f"Missing date for {clamped.get('name') or 'reading'}")
            continue
        row_doc_id = (raw.source_document_id or "").strip() or default_doc_id
        try:
            entry = add_patient_diagnostic(
                patient_id,
                name=clamped["name"],
                value=clamped["value"],
                recorded_at=clamped["recorded_at"],
                unit=clamped.get("unit"),
                notes=clamped.get("notes"),
                category=clamped.get("category"),
                source_document_id=row_doc_id,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if entry is None:
            raise HTTPException(status_code=404, detail="Patient not found")
        added.append(entry)
    if not added and errors:
        raise HTTPException(status_code=400, detail=errors[0])
    profile = get_patient_profile(patient_id)
    if default_doc_id:
        try:
            from app.services.clinical_report_handling import (
                open_store_for_patient_document,
                refresh_document_handling,
            )

            opened = await open_store_for_patient_document(patient_id, default_doc_id)
            if opened:
                target_store, src = opened
                await refresh_document_handling(
                    target_store,
                    src,
                    profile=profile,
                    lab_import={
                        "added_count": len(added),
                        "proposed_count": len(body.diagnostics),
                        "skipped_incomplete": 0,
                        "skipped_duplicate": 0,
                    },
                )
        except Exception:
            pass
    return {
        "added": added,
        "added_count": len(added),
        "errors": errors,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


class PatientJournalRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=80)
    text: str | None = Field(default=None, max_length=500)
    severity: int | None = Field(default=None, ge=1, le=5)
    recorded_at: str | None = Field(default=None, max_length=40)
    case_id: str | None = Field(default=None, max_length=120)


@router.post("/patients/{patient_id}/journal")
async def api_add_patient_journal(patient_id: str, body: PatientJournalRequest):
    try:
        entry = add_patient_journal_entry(
            patient_id,
            kind=body.kind,
            label=body.label,
            text=body.text,
            severity=body.severity,
            recorded_at=body.recorded_at,
            case_id=body.case_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    return {
        "entry": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.delete("/patients/{patient_id}/journal/{entry_id}")
async def api_delete_patient_journal(patient_id: str, entry_id: str):
    ok = delete_patient_journal_entry(patient_id, entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    profile = get_patient_profile(patient_id)
    return {
        "ok": True,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


class PatientMedicationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dosage: str | None = Field(default=None, max_length=80)
    frequency: str | None = Field(default=None, max_length=80)
    conditions: list[str] | str | None = None
    notes: str | None = Field(default=None, max_length=500)
    started_at: str | None = Field(default=None, max_length=20)
    ended_at: str | None = Field(default=None, max_length=20)
    category: str | None = Field(default="prescription", max_length=32)
    show_on_log: bool | None = None


class PatientMedicationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    dosage: str | None = Field(default=None, max_length=80)
    frequency: str | None = Field(default=None, max_length=80)
    conditions: list[str] | str | None = None
    notes: str | None = Field(default=None, max_length=500)
    started_at: str | None = Field(default=None, max_length=20)
    ended_at: str | None = Field(default=None, max_length=20)
    category: str | None = Field(default=None, max_length=32)
    show_on_log: bool | None = None
    history_note: str | None = Field(default=None, max_length=200)
    effective_at: str | None = Field(default=None, max_length=20)


class PatientMedicationStopRequest(BaseModel):
    stopped_at: str | None = Field(default=None, max_length=20)


class PatientMedicationConfirmItem(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dosage: str | None = Field(default=None, max_length=80)
    frequency: str | None = Field(default=None, max_length=80)
    conditions: list[str] | str | None = None
    notes: str | None = Field(default=None, max_length=500)
    started_at: str | None = Field(default=None, max_length=20)
    ended_at: str | None = Field(default=None, max_length=20)


class PatientMedicationConfirmRequest(BaseModel):
    medications: list[PatientMedicationConfirmItem] = Field(default_factory=list, max_length=80)


@router.get("/patients/{patient_id}/medications/export.pdf")
async def export_patient_medications_pdf(
    patient_id: str,
    request: Request,
    scope: str = "all",
):
    patients = list_patients()
    patient = next((p for p in patients if p["id"] == patient_id), None)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        scope_key = normalize_medication_export_scope(scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    profile = get_patient_profile(patient_id)
    meds = profile.get("medications") or []
    filtered = filter_medications_for_export(meds, scope_key)
    if not filtered:
        raise HTTPException(
            status_code=404,
            detail=f"No medications to export for scope “{medication_export_scope_label(scope_key)}”",
        )

    sub_bits: list[str] = []
    age = age_years_from_dob(profile.get("date_of_birth"))
    if age is not None:
        sub_bits.append(f"Age {age}")
    if profile.get("gender"):
        sub_bits.append(str(profile["gender"]))
    patient_subline = " · ".join(sub_bits) if sub_bits else None

    exported_at = datetime.now(timezone.utc)
    pdf_bytes = build_medications_pdf(
        meds,
        scope=scope_key,
        patient_label=patient.get("label"),
        patient_subline=patient_subline,
    )
    filename = medications_pdf_filename(
        patient_label=patient.get("label"),
        scope=scope_key,
        exported_at=exported_at,
    )
    db, _, _, _, _ = await _get_services()
    await _audit(
        db,
        request,
        PDF_EXPORTED,
        resource_type="patient",
        resource_id=patient_id,
        metadata={
            "filename": filename,
            "export_kind": "medications",
            "scope": scope_key,
            "row_count": len(filtered),
        },
    )
    return FastAPIResponse(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/patients/{patient_id}/medications/safety-review")
async def api_get_medication_safety_review(patient_id: str):
    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    saved = get_medication_safety(patient_id)
    profile = get_patient_profile(patient_id)
    return {
        "medication_safety": saved,
        "profile": profile,
    }


@router.post("/patients/{patient_id}/medications/safety-review")
async def api_run_medication_safety_review(patient_id: str):
    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        saved = await run_medication_safety_review(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    profile = get_patient_profile(patient_id)
    return {
        "medication_safety": saved,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.post("/patients/{patient_id}/medications/import")
async def api_import_patient_medications(
    patient_id: str,
    file: UploadFile = File(...),
):
    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    content = await file.read()
    try:
        result = await propose_medications_from_upload(
            patient_id,
            content,
            content_type=file.content_type,
            filename=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/patients/{patient_id}/medications/import/confirm")
async def api_confirm_patient_medications_import(
    patient_id: str,
    body: PatientMedicationConfirmRequest,
):
    patients = list_patients()
    if not any(p["id"] == patient_id for p in patients):
        raise HTTPException(status_code=404, detail="Patient not found")
    if not body.medications:
        raise HTTPException(status_code=400, detail="Select at least one medication to add")
    added: list[dict[str, Any]] = []
    errors: list[str] = []
    for raw in body.medications:
        clamped = clamp_proposed_medication(raw.model_dump())
        if clamped is None:
            errors.append("Skipped an invalid row")
            continue
        try:
            entry = add_patient_medication(
                patient_id,
                name=clamped["name"],
                dosage=clamped.get("dosage"),
                frequency=clamped.get("frequency"),
                conditions=clamped.get("conditions"),
                notes=clamped.get("notes"),
                started_at=clamped.get("started_at"),
                ended_at=clamped.get("ended_at"),
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if entry is None:
            raise HTTPException(status_code=404, detail="Patient not found")
        added.append(entry)
    if not added and errors:
        raise HTTPException(status_code=400, detail=errors[0])
    profile = get_patient_profile(patient_id)
    return {
        "added": added,
        "added_count": len(added),
        "errors": errors,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.post("/patients/{patient_id}/medications")
async def api_add_patient_medication(patient_id: str, body: PatientMedicationCreateRequest):
    try:
        entry = add_patient_medication(
            patient_id,
            name=body.name,
            dosage=body.dosage,
            frequency=body.frequency,
            conditions=body.conditions,
            notes=body.notes,
            started_at=body.started_at,
            ended_at=body.ended_at,
            category=body.category,
            show_on_log=body.show_on_log,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    return {
        "medication": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.patch("/patients/{patient_id}/medications/{medication_id}")
async def api_update_patient_medication(
    patient_id: str,
    medication_id: str,
    body: PatientMedicationUpdateRequest,
):
    fields_set = body.model_fields_set
    kwargs: dict[str, Any] = {"history_note": body.history_note}
    if "name" in fields_set:
        kwargs["name"] = body.name
    if "dosage" in fields_set:
        kwargs["dosage"] = body.dosage
    if "frequency" in fields_set:
        kwargs["frequency"] = body.frequency
    if "conditions" in fields_set:
        kwargs["conditions"] = body.conditions
    if "notes" in fields_set:
        kwargs["notes"] = body.notes
    if "started_at" in fields_set:
        kwargs["started_at"] = body.started_at
    if "ended_at" in fields_set:
        kwargs["ended_at"] = body.ended_at
    if "category" in fields_set:
        kwargs["category"] = body.category
    if "show_on_log" in fields_set:
        kwargs["show_on_log"] = body.show_on_log
    if "effective_at" in fields_set:
        kwargs["effective_at"] = body.effective_at
    try:
        entry = update_patient_medication(patient_id, medication_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Medication not found")
    profile = get_patient_profile(patient_id)
    return {
        "medication": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.post("/patients/{patient_id}/medications/{medication_id}/stop")
async def api_stop_patient_medication(
    patient_id: str,
    medication_id: str,
    body: PatientMedicationStopRequest | None = None,
):
    try:
        entry = stop_patient_medication(
            patient_id,
            medication_id,
            stopped_at=(body.stopped_at if body else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Medication not found")
    profile = get_patient_profile(patient_id)
    return {
        "medication": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.delete("/patients/{patient_id}/medications/{medication_id}")
async def api_delete_patient_medication(patient_id: str, medication_id: str):
    ok = delete_patient_medication(patient_id, medication_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Medication not found")
    profile = get_patient_profile(patient_id)
    return {
        "ok": True,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


class PatientFoodDrinkCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class PatientFoodDrinkUpdateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)


@router.post("/patients/{patient_id}/food-drinks")
async def api_add_patient_food_drink(patient_id: str, body: PatientFoodDrinkCreateRequest):
    try:
        entry = add_patient_food_drink(patient_id, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    return {
        "food_drink": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.patch("/patients/{patient_id}/food-drinks/{food_id}")
async def api_update_patient_food_drink(
    patient_id: str,
    food_id: str,
    body: PatientFoodDrinkUpdateRequest,
):
    try:
        entry = update_patient_food_drink(patient_id, food_id, label=body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Item not found")
    profile = get_patient_profile(patient_id)
    return {
        "food_drink": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.delete("/patients/{patient_id}/food-drinks/{food_id}")
async def api_delete_patient_food_drink(patient_id: str, food_id: str):
    ok = delete_patient_food_drink(patient_id, food_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item not found")
    profile = get_patient_profile(patient_id)
    return {
        "ok": True,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


class PatientMilestoneCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    date: str = Field(min_length=8, max_length=20)
    kind: str | None = Field(default="lifestyle", max_length=40)
    notes: str | None = Field(default=None, max_length=200)


class PatientMilestoneUpdateRequest(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    date: str | None = Field(default=None, max_length=20)
    kind: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=200)


@router.post("/patients/{patient_id}/milestones")
async def api_add_patient_milestone(patient_id: str, body: PatientMilestoneCreateRequest):
    try:
        entry = add_patient_milestone(
            patient_id,
            label=body.label,
            date=body.date,
            kind=body.kind,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    profile = get_patient_profile(patient_id)
    return {
        "milestone": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.patch("/patients/{patient_id}/milestones/{milestone_id}")
async def api_update_patient_milestone(
    patient_id: str,
    milestone_id: str,
    body: PatientMilestoneUpdateRequest,
):
    fields = body.model_fields_set
    kwargs: dict[str, Any] = {}
    if "label" in fields:
        kwargs["label"] = body.label
    if "date" in fields:
        kwargs["date"] = body.date
    if "kind" in fields:
        kwargs["kind"] = body.kind
    if "notes" in fields:
        kwargs["notes"] = body.notes
    try:
        entry = update_patient_milestone(patient_id, milestone_id, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="Milestone not found")
    profile = get_patient_profile(patient_id)
    return {
        "milestone": entry,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.delete("/patients/{patient_id}/milestones/{milestone_id}")
async def api_delete_patient_milestone(patient_id: str, milestone_id: str):
    ok = delete_patient_milestone(patient_id, milestone_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Milestone not found")
    profile = get_patient_profile(patient_id)
    return {
        "ok": True,
        "profile": profile,
        "diagnostic_series": group_diagnostics_for_charts(profile),
        "journal_series": group_journal_for_charts(profile),
    }


@router.get("/patients/{patient_id}/cases")
async def api_list_cases(patient_id: str):
    cases = list_cases(patient_id)
    return {"cases": cases}


@router.post("/patients/{patient_id}/cases")
async def api_create_case(patient_id: str, body: CreateCaseRequest):
    case = create_case(patient_id, body.label)
    if case is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if body.patient_context:
        activate_patient_case(patient_id, case["id"])
        db = Database()
        await db.init()
        await db.set_setting("patient_context", body.patient_context)
    return {"case": case}


@router.patch("/patients/{patient_id}/cases/{case_id}")
async def api_rename_case(patient_id: str, case_id: str, body: RenameCaseRequest):
    try:
        case = rename_case(patient_id, case_id, body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if case is None:
        raise HTTPException(status_code=404, detail="Patient or case not found")
    return {"case": case, "active": get_active_context()}


@router.delete("/patients/{patient_id}/cases/{case_id}")
async def api_delete_case(patient_id: str, case_id: str):
    ok = delete_case(patient_id, case_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Case not found")
    return {"ok": True}


@router.put("/cases/activate")
async def api_activate_case(body: ActivateCaseRequest):
    ok = activate_patient_case(body.patient_id, body.case_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Patient or case not found")
    db = Database()
    await db.init()
    ctx = get_active_context()
    return {"ok": True, "active": ctx}


@router.get("/cases/active")
async def api_active_context():
    return get_active_context()


@router.get("/cases/siblings")
async def api_sibling_cases():
    """List other cases for the same patient (for cross-case document browsing)."""
    ctx = get_active_context()
    if not ctx["patient_id"] or not ctx["case_id"]:
        return {"siblings": []}
    siblings = sibling_case_dirs(ctx["patient_id"], ctx["case_id"])
    return {"siblings": [{"id": s["id"], "label": s["label"]} for s in siblings]}


@router.get("/cases/siblings/{sibling_case_id}/documents")
async def api_sibling_case_documents(sibling_case_id: str):
    """List documents from a sibling case (read-only cross-case browsing)."""
    ctx = get_active_context()
    if not ctx["patient_id"]:
        raise HTTPException(status_code=400, detail="No active patient")
    siblings = sibling_case_dirs(ctx["patient_id"], ctx["case_id"])
    sibling = next((s for s in siblings if s["id"] == sibling_case_id), None)
    if not sibling:
        raise HTTPException(status_code=404, detail="Sibling case not found")
    sibling_db_path = Path(sibling["dir"]) / "beatit.db"
    if not sibling_db_path.exists():
        return {"documents": []}
    sib_db = Database(db_path=sibling_db_path)
    docs = await sib_db.list_documents()
    return {"documents": docs, "case_label": sibling["label"]}
