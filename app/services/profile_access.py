"""Which patient profiles a signed-in user may open."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.services.auth_users import user_is_admin, user_profile_allowlist
from app.services.case_manager import get_active_context

# APIs that must stay reachable even when the global active profile is not theirs.
_OPEN_API_PATHS = {
    "/api/auth/me",
    "/api/logout",
    "/api/health",
    "/api/version",
    "/api/patients",
    "/api/cases/activate",
    "/api/cases/active",
}


def patient_id_from_api_path(path: str) -> str | None:
    parts = [p for p in str(path or "").split("/") if p]
    # api / patients / {id} / ...
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "patients":
        return parts[2]
    return None


def can_access_patient(username: str | None, patient_id: str | None) -> bool:
    if not settings.auth_enabled:
        return True
    if user_is_admin(username):
        return True
    pid = str(patient_id or "").strip()
    if not pid:
        return False
    allowed = user_profile_allowlist(username or "")
    if allowed is None:
        return True
    return pid in allowed


def enforce_request_access(request: Request) -> Response | None:
    """Return a 403 response when this request would expose another profile."""
    if not settings.auth_enabled:
        return None
    username = getattr(request.state, "user", None)
    if user_is_admin(username):
        return None

    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    pid = patient_id_from_api_path(path)

    if pid:
        if not can_access_patient(username, pid):
            return JSONResponse(
                status_code=403,
                content={"detail": "You don't have access to this profile"},
            )
        # Guests cannot delete a whole profile.
        if method == "DELETE" and path == f"/api/patients/{pid}":
            return JSONResponse(
                status_code=403,
                content={"detail": "You can't delete profiles"},
            )
        return None

    if path == "/api/patients" and method == "POST":
        return JSONResponse(
            status_code=403,
            content={"detail": "You can't add profiles"},
        )

    if path.startswith("/api/auth/users"):
        return JSONResponse(
            status_code=403,
            content={"detail": "You can't manage sign-in access"},
        )

    if not path.startswith("/api/") or path in _OPEN_API_PATHS:
        return None

    active_id = get_active_context().get("patient_id")
    if active_id and not can_access_patient(username, str(active_id)):
        return JSONResponse(
            status_code=403,
            content={"detail": "Switch to a profile you can access"},
        )
    return None
