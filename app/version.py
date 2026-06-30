"""Single source of truth for app version — bump when releasing."""

APP_NAME = "BeatIt"
APP_VERSION = "0.5.8"
APP_UPDATED = "2026-06-30"


def version_info() -> dict[str, str]:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "updated": APP_UPDATED,
    }
