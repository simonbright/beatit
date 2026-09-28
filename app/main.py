from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.middleware.auth import SessionAuthMiddleware
from app.storage.database import Database
from app.services.analysis_jobs import resume_pending_jobs, scrub_all_case_databases
from app.services.case_manager import migrate_legacy_if_needed, scrub_all_patient_profiles
from app.version import APP_NAME, APP_VERSION

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate_legacy_if_needed()
    scrub_all_patient_profiles()
    await scrub_all_case_databases()
    db = Database()
    await db.init()
    await resume_pending_jobs()
    yield


app = FastAPI(
    title=f"{APP_NAME} — Patient Care Workspace",
    description="Store clinical research material and synthesize clinical insights",
    version=APP_VERSION,
    lifespan=lifespan,
)

if settings.auth_enabled:
    app.add_middleware(SessionAuthMiddleware)

app.include_router(router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _inject_static_version(html: str) -> str:
    versioned = f"?v={APP_VERSION}"
    return (
        html.replace('href="/static/styles.css"', f'href="/static/styles.css{versioned}"')
        .replace('src="/static/app.js"', f'src="/static/app.js{versioned}"')
        .replace('src="/static/updating.js"', f'src="/static/updating.js{versioned}"')
    )


@app.get("/sw.js")
async def service_worker():
    path = STATIC_DIR / "sw.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Service worker not found")
    return FileResponse(
        path,
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/manifest.webmanifest")
async def web_manifest():
    path = STATIC_DIR / "manifest.webmanifest"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Manifest not found")
    raw = path.read_text(encoding="utf-8")
    # Bust home-screen / PWA icon caches when APP_VERSION changes
    for name in (
        "icon-192.png",
        "icon-512.png",
        "icon-512-maskable.png",
        "favicon.png",
        "apple-touch-icon.png",
    ):
        raw = raw.replace(f"/static/{name}", f"/static/{name}?v={APP_VERSION}")
    return Response(
        content=raw,
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/login")
async def login_page():
    login_path = STATIC_DIR / "login.html"
    if login_path.exists():
        return HTMLResponse(
            _inject_static_version(login_path.read_text(encoding="utf-8")),
            headers={"Cache-Control": "no-cache"},
        )
    return RedirectResponse(url="/", status_code=302)


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(
            _inject_static_version(index_path.read_text(encoding="utf-8")),
            headers={"Cache-Control": "no-cache"},
        )
    return {"message": f"{APP_NAME} API running. Static UI not found."}
