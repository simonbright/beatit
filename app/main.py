from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.middleware.auth import SessionAuthMiddleware
from app.storage.database import Database
from app.services.analysis_jobs import resume_pending_jobs
from app.version import APP_VERSION

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database()
    await db.init()
    await resume_pending_jobs()
    yield


app = FastAPI(
    title="BeatIt — Oncology Case Analysis",
    description="Store clinical research material and synthesize oncology insights",
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
    return {"message": "BeatIt API running. Static UI not found."}
