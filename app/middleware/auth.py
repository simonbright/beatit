from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.config import settings
from app.services.auth_session import COOKIE_NAME, verify_session_token

PUBLIC_PATHS = {"/api/health", "/api/version", "/login", "/api/login"}
PUBLIC_PREFIXES = ("/static/",)


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.auth_enabled:
            request.state.user = "local"
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        username = verify_session_token(request.cookies.get(COOKIE_NAME))
        if username:
            request.state.user = username
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

        return RedirectResponse(url="/login", status_code=302)
