from pathlib import Path
from typing import Any
import json
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, Field

from app.ingest.text import ingest_text
from app.ingest.url import ingest_url
from app.ingest.pdf import ingest_pdf_file
from app.ingest.imaging import ingest_imaging_file, is_allowed_imaging_upload
from app.ingest.video import ingest_video
from app.services.llm import LLMClient
from app.services.openrouter_client import OpenRouterClient
from app.services.openrouter_models import DEFAULT_OPENROUTER_MODEL, MODEL_IDS, OPENROUTER_MODELS
from app.services.patient_context import DEFAULT_PATIENT_CONTEXT
from app.services.analysis_jobs import (
    ActiveAnalysisJobError,
    enqueue_analysis_job,
    enqueue_refinement_job,
    get_job_payload,
)
from app.services.synthesis import SynthesisService
from app.services.document_view import build_document_view, file_is_available, guess_media_type
from app.services.dicom_preview import is_dicom_document, render_dicom_preview_png
from app.services.investigation import InvestigationService
from app.services.pdf_export import assessment_pdf_filename, build_assessment_pdf
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


class SettingsUpdateRequest(BaseModel):
    openrouter_model: str | None = None
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
    _, _, llm, _, _ = await _get_services()
    llm_status = await llm.health()
    return {"status": "ok", "llm": llm_status, **version_info()}


@router.get("/version")
async def app_version():
    return version_info()


@router.get("/settings")
async def get_app_settings():
    db, _, llm, _, _ = await _get_services()
    model = await db.get_setting("openrouter_model") or settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
    patient_context = await db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
    catalog = await _source_catalog(db, [])
    return {
        "settings": {
            "llm_provider": settings.llm_provider,
            "openrouter_model": model,
            "patient_context": patient_context,
            "source_labels": _source_labels_payload(catalog),
        },
        "models": OPENROUTER_MODELS,
        "default_model": DEFAULT_OPENROUTER_MODEL,
        "default_patient_context": DEFAULT_PATIENT_CONTEXT,
        "default_source_labels": _source_labels_payload(SourceCatalog.from_settings([], None)),
        "source_legend": catalog.legend(),
    }


@router.put("/settings")
async def update_app_settings(body: SettingsUpdateRequest, request: Request):
    if (
        body.openrouter_model is None
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
    return {
        "documents": await db.list_document_index(),
        "total": await db.count_documents(),
        "counts_by_type": await db.document_type_counts(),
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
    doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    catalog = await _source_catalog(db)
    doc["source_info"] = catalog.describe_document(doc)
    text = await store.read_extracted_text(doc)
    view = build_document_view(doc)
    return {"document": doc, "extracted_text": text, **view}


@router.get("/documents/{doc_id}/file")
async def get_document_file(doc_id: str):
    db, _, _, _, _ = await _get_services()
    doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not file_is_available(doc):
        raise HTTPException(status_code=404, detail="Original file not available")

    path = Path(doc["file_path"])
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
    db, _, _, _, _ = await _get_services()
    doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not file_is_available(doc):
        raise HTTPException(status_code=404, detail="Original file not available")
    if not is_dicom_document(doc):
        raise HTTPException(status_code=400, detail="Preview is only available for DICOM files")

    path = Path(doc["file_path"])
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
        },
    )
    return {"document": doc}


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
    try:
        job = await enqueue_analysis_job(
            job_type=job_type,
            query=body.query,
            document_ids=body.document_ids,
            include_baseline_assessment=body.include_baseline_assessment,
            assessment_guidance=body.assessment_guidance,
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
            "job_type": job_type,
            "query_preview": preview_text(body.query or "Baseline assessment"),
            "document_count": len(body.document_ids or []),
            "guidance_preview": preview_text(body.assessment_guidance or ""),
            "save_as_draft": job_type == "query",
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
    patient_context = await db.get_setting("patient_context") or DEFAULT_PATIENT_CONTEXT
    catalog = await _source_catalog(db)
    pdf_bytes = build_assessment_pdf(
        analysis,
        patient_context=patient_context,
        catalog=catalog,
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
