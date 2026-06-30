from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.middleware.auth import SessionAuthMiddleware
from app.storage.database import Database

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database()
    await db.init()
    yield


app = FastAPI(
    title="BeatIt — Oncology Case Analysis",
    description="Store clinical research material and synthesize oncology insights",
    version="0.1.0",
    lifespan=lifespan,
)

if settings.auth_enabled:
    app.add_middleware(SessionAuthMiddleware)

app.include_router(router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/login")
async def login_page():
    login_path = STATIC_DIR / "login.html"
    if login_path.exists():
        return FileResponse(login_path)
    return RedirectResponse(url="/", status_code=302)


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "BeatIt API running. Static UI not found."}
