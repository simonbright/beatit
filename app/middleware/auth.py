from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.config import settings
from app.services.auth_session import COOKIE_NAME, verify_session_token
from app.services.profile_access import enforce_request_access
from app.services.user_context import reset_request_actor, set_request_actor

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
            token = set_request_actor(username)
            try:
                denied = enforce_request_access(request)
                if denied is not None:
                    return denied
                return await call_next(request)
            finally:
                reset_request_actor(token)

        if path.startswith("/api/"):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

        return RedirectResponse(url="/login", status_code=302)
