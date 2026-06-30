from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.ingest.text import ingest_text
from app.ingest.url import ingest_url
from app.ingest.youtube import ingest_youtube
from app.ingest.pdf import ingest_pdf_file
from app.ingest.video import ingest_video
from app.services.llm import LLMClient
from app.services.openrouter_client import OpenRouterClient
from app.services.openrouter_models import DEFAULT_OPENROUTER_MODEL, MODEL_IDS, OPENROUTER_MODELS
from app.services.synthesis import SynthesisService
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


class AnalyzeRequest(BaseModel):
    query: str = Field(default="")
    document_ids: list[str] | None = None
    include_baseline_assessment: bool = False


class SettingsUpdateRequest(BaseModel):
    openrouter_model: str = Field(min_length=3, max_length=200)


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


async def _get_services():
    db = Database()
    model = await db.get_setting("openrouter_model") or settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
    llm = LLMClient(openrouter=OpenRouterClient(model=model))
    store = DocumentStore(db)
    synthesis = SynthesisService(store, db, llm)
    return db, store, llm, synthesis


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    if not settings.auth_enabled:
        return {"ok": True, "username": body.username.strip(), "auth": "disabled"}

    username = body.username.strip()
    if not verify_credentials(username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    _set_session_cookie(response, username)
    return {"ok": True, "username": username}


@router.post("/logout")
async def logout(response: Response):
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
    _, _, llm, _ = await _get_services()
    llm_status = await llm.health()
    return {"status": "ok", "llm": llm_status}


@router.get("/settings")
async def get_app_settings():
    db, _, llm, _ = await _get_services()
    model = await db.get_setting("openrouter_model") or settings.openrouter_model or DEFAULT_OPENROUTER_MODEL
    return {
        "settings": {
            "llm_provider": settings.llm_provider,
            "openrouter_model": model,
        },
        "models": OPENROUTER_MODELS,
        "default_model": DEFAULT_OPENROUTER_MODEL,
    }


@router.put("/settings")
async def update_app_settings(body: SettingsUpdateRequest):
    model_id = body.openrouter_model.strip()
    if model_id not in MODEL_IDS:
        raise HTTPException(
            status_code=400,
            detail="Choose a model from the list or pick a supported preset",
        )
    db, _, _, _ = await _get_services()
    await db.set_setting("openrouter_model", model_id)
    return {
        "settings": {
            "openrouter_model": model_id,
        }
    }


@router.get("/documents")
async def list_documents():
    db, _, _, _ = await _get_services()
    return {"documents": await db.list_documents()}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str):
    db, store, _, _ = await _get_services()
    doc = await db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    text = await store.read_extracted_text(doc)
    return {"document": doc, "extracted_text": text}


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    _, store, _, _ = await _get_services()
    deleted = await store.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True}


@router.post("/ingest/text")
async def ingest_text_route(body: TextIngestRequest):
    _, store, _, _ = await _get_services()
    doc = await ingest_text(
        store,
        title=body.title,
        content=body.content,
        metadata=body.metadata,
    )
    return {"document": doc}


@router.post("/ingest/url")
async def ingest_url_route(body: UrlIngestRequest):
    _, store, _, _ = await _get_services()
    try:
        doc = await ingest_url(
            store,
            url=body.url,
            title=body.title,
            metadata=body.metadata,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"document": doc}


@router.post("/ingest/youtube")
async def ingest_youtube_route(body: YoutubeIngestRequest):
    _, store, _, _ = await _get_services()
    try:
        doc = await ingest_youtube(
            store,
            url=body.url,
            title=body.title,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"document": doc}


@router.post("/ingest/pdf")
async def ingest_pdf_route(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
):
    _, store, _, _ = await _get_services()
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
    return {"document": doc}


@router.post("/ingest/video")
async def ingest_video_route(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    notes: str | None = Form(default=None),
):
    _, store, _, _ = await _get_services()
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
    return {"document": doc}


@router.post("/analyze")
async def analyze(body: AnalyzeRequest):
    _, _, _, synthesis = await _get_services()
    if not body.query.strip() and not body.include_baseline_assessment:
        raise HTTPException(
            status_code=400,
            detail="Provide a query or set include_baseline_assessment=true",
        )
    try:
        result = await synthesis.analyze(
            query=body.query,
            document_ids=body.document_ids,
            include_baseline_assessment=body.include_baseline_assessment,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Analysis failed: {exc}",
        ) from exc
    return {"analysis": result}


@router.post("/analyze/summarize")
async def summarize(document_ids: list[str] | None = None):
    _, _, _, synthesis = await _get_services()
    try:
        result = await synthesis.summarize_documents(document_ids)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Synthesis failed: {exc}",
        ) from exc
    return {"analysis": result}


@router.get("/analyses")
async def list_analyses(limit: int = 50):
    db, _, _, _ = await _get_services()
    return {"analyses": await db.list_analyses(limit=limit)}


@router.get("/analyses/{analysis_id}")
async def get_analysis(analysis_id: str):
    db, _, _, _ = await _get_services()
    analyses = await db.list_analyses(limit=500)
    match = next((a for a in analyses if a["id"] == analysis_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"analysis": match}
