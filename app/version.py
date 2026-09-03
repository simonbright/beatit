"""Single source of truth for app version — bump when releasing."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

APP_NAME = "BeatIt"
APP_VERSION = "1.1.0"
# Optional ISO override (date or datetime). Leave empty to use git/file timestamp.
APP_UPDATED = ""

EASTERN = ZoneInfo("America/New_York")
_VERSION_FILE = Path(__file__)


def _parse_updated(iso: str) -> datetime | None:
    raw = iso.strip()
    if not raw:
        return None
    if "T" not in raw:
        raw = f"{raw}T12:00:00"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EASTERN)
    return dt.astimezone(EASTERN)


def _git_commit_time() -> datetime | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", "app/version.py"],
            cwd=_VERSION_FILE.parent.parent,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode != 0:
            return None
        return _parse_updated(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _file_mtime() -> datetime:
    mtime = _VERSION_FILE.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone(EASTERN)


def release_updated_at() -> datetime:
    now = datetime.now(EASTERN)
    if APP_UPDATED.strip():
        explicit = _parse_updated(APP_UPDATED)
        if explicit:
            return min(explicit, now)
    git_time = _git_commit_time()
    if git_time:
        return min(git_time, now)
    return min(_file_mtime(), now)


def _format_version_updated(dt: datetime) -> str:
    eastern = dt.astimezone(EASTERN)
    date = eastern.strftime("%B %d, %Y").replace(" 0", " ")
    hour = eastern.strftime("%I").lstrip("0") or "12"
    tz_label = eastern.strftime("%Z")
    tz = "ET" if tz_label in ("EST", "EDT") else tz_label
    time = f"{hour}:{eastern.strftime('%M %p')} {tz}"
    return f"{date} · {time}"


def version_info() -> dict[str, str]:
    updated_at = release_updated_at()
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "updated": updated_at.isoformat(),
        "updated_display": _format_version_updated(updated_at),
    }
